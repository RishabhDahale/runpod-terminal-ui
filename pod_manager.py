"""Pod scaling logic, rolling deploys, rollback, cost estimation, and history."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from models import DeployRecord, DeployStatus, GpuType, Pod
from runpod_client import RunPodClient, RunPodError


class PodManager:
    def __init__(self, client: RunPodClient, history_path: Path):
        self.client = client
        self.history_path = history_path
        self._active_deploy: Optional[DeployRecord] = None
        self._deploy_cancelled = False

    # --- Cost Estimation ---

    @staticmethod
    def estimate_cost(
        gpu_type: GpuType, gpu_count: int, pod_count: int,
        cloud_type: str = "ALL", hours: float = 1.0,
    ) -> dict[str, float]:
        if cloud_type == "COMMUNITY":
            price = gpu_type.community_price
        elif cloud_type == "SECURE":
            price = gpu_type.secure_price
        else:
            price = gpu_type.lowest_price
        per_pod_hr = price * gpu_count
        total_hr = per_pod_hr * pod_count
        return {
            "per_pod_hr": per_pod_hr,
            "total_hr": total_hr,
            "total_period": total_hr * hours,
        }

    # --- Scale Up ---

    async def scale_up(
        self,
        count: int,
        name_prefix: str,
        image_name: str,
        gpu_type_id: str,
        gpu_count: int = 1,
        cloud_type: str = "ALL",
        volume_in_gb: int = 20,
        container_disk_in_gb: int = 20,
        ports: str = "8888/http",
        volume_mount_path: str = "/workspace",
        env: list[str] | None = None,
        template_id: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> DeployRecord:
        deploy_id = uuid.uuid4().hex[:8]
        start = datetime.now(timezone.utc)
        record = DeployRecord(
            deploy_id=deploy_id,
            timestamp=start.isoformat(),
            action="scale_up",
            status=DeployStatus.IN_PROGRESS.value,
            gpu_type=gpu_type_id,
            pod_count=count,
            new_image=image_name,
        )

        created_pods = []
        errors = []
        for i in range(count):
            name = f"{name_prefix}-{deploy_id}-{i}"
            try:
                pod = await self.client.create_pod(
                    name=name,
                    image_name=image_name,
                    gpu_type_id=gpu_type_id,
                    gpu_count=gpu_count,
                    cloud_type=cloud_type,
                    volume_in_gb=volume_in_gb,
                    container_disk_in_gb=container_disk_in_gb,
                    ports=ports,
                    volume_mount_path=volume_mount_path,
                    env=env,
                    template_id=template_id,
                )
                created_pods.append(pod)
                if on_progress:
                    on_progress(i + 1, count)
            except Exception as e:
                errors.append(f"Pod {i+1}: {e}")
                # Stop trying if GPUs ran out
                if "no longer any instances available" in str(e).lower():
                    errors.append(
                        f"Stopped after {len(created_pods)}/{count} — no GPUs available"
                    )
                    break

        record.pod_ids = [p.id for p in created_pods]
        record.pod_count = len(created_pods)
        record.duration_seconds = (datetime.now(timezone.utc) - start).total_seconds()

        if errors:
            record.error = "; ".join(errors)
            record.status = DeployStatus.FAILED.value if not created_pods else DeployStatus.COMPLETED.value
        else:
            record.status = DeployStatus.COMPLETED.value

        self._record_deploy(record)
        return record

    # --- Scale Down ---

    async def scale_down(
        self,
        pod_ids: list[str],
        action: str = "stop",  # "stop" or "terminate"
        on_progress: Callable[[int, int], None] | None = None,
    ) -> DeployRecord:
        deploy_id = uuid.uuid4().hex[:8]
        start = datetime.now(timezone.utc)
        record = DeployRecord(
            deploy_id=deploy_id,
            timestamp=start.isoformat(),
            action="scale_down",
            status=DeployStatus.IN_PROGRESS.value,
            pod_count=len(pod_ids),
            pod_ids=list(pod_ids),
            notes=f"action={action}",
        )

        async def _stop_one(pod_id: str, index: int) -> str | Exception:
            try:
                if action == "terminate":
                    await self.client.terminate_pod(pod_id)
                else:
                    await self.client.stop_pod(pod_id)
                if on_progress:
                    on_progress(index + 1, len(pod_ids))
                return pod_id
            except Exception as e:
                return e

        tasks = [_stop_one(pid, i) for i, pid in enumerate(pod_ids)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = []
        for r in results:
            if isinstance(r, Exception):
                errors.append(str(r))

        record.duration_seconds = (datetime.now(timezone.utc) - start).total_seconds()
        if errors:
            record.error = "; ".join(errors)
            record.status = DeployStatus.FAILED.value
        else:
            record.status = DeployStatus.COMPLETED.value

        self._record_deploy(record)
        return record

    # --- Rolling Deploy ---

    async def rolling_deploy(
        self,
        target_pods: list[Pod],
        new_image: str,
        grace_period_minutes: int = 15,
        health_check_timeout: int = 300,
        on_state_change: Callable[[str, str, str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_countdown: Callable[[int], None] | None = None,
    ) -> DeployRecord:
        deploy_id = uuid.uuid4().hex[:8]
        start = datetime.now(timezone.utc)
        self._deploy_cancelled = False

        record = DeployRecord(
            deploy_id=deploy_id,
            timestamp=start.isoformat(),
            action="rolling_deploy",
            status=DeployStatus.IN_PROGRESS.value,
            old_image=target_pods[0].image_name if target_pods else "",
            new_image=new_image,
            pod_count=len(target_pods),
        )
        self._active_deploy = record

        completed_pairs: list[tuple[Pod, Pod]] = []  # (old, new)
        grace_seconds = grace_period_minutes * 60

        try:
            for idx, old_pod in enumerate(target_pods):
                if self._deploy_cancelled:
                    await self._rollback_deploy(completed_pairs, record)
                    record.status = DeployStatus.ROLLED_BACK.value
                    record.error = "Cancelled by user"
                    break

                # Step 1: Create replacement pod
                if on_state_change:
                    on_state_change(old_pod.id, "CREATE_NEW", "")
                try:
                    new_pod = await self.client.create_pod(
                        name=f"{old_pod.name}-v2-{deploy_id}",
                        image_name=new_image,
                        gpu_type_id=old_pod.gpu_display_name,
                        gpu_count=old_pod.gpu_count,
                        volume_in_gb=old_pod.volume_in_gb,
                        container_disk_in_gb=old_pod.container_disk_in_gb,
                        volume_mount_path=old_pod.volume_mount_path,
                        ports=old_pod.ports_config or "8888/http",
                        env=old_pod.env,
                    )
                except Exception as e:
                    if on_state_change:
                        on_state_change(old_pod.id, "FAILED", str(e))
                    await self._rollback_deploy(completed_pairs, record)
                    record.status = DeployStatus.FAILED.value
                    record.error = f"Failed to create replacement for {old_pod.id}: {e}"
                    break

                if on_state_change:
                    on_state_change(old_pod.id, "HEALTH_CHECK", new_pod.id)

                # Step 2: Health check
                healthy = await self._wait_for_healthy(new_pod.id, health_check_timeout)
                if not healthy:
                    if on_state_change:
                        on_state_change(old_pod.id, "ROLLING_BACK", new_pod.id)
                    try:
                        await self.client.terminate_pod(new_pod.id)
                    except RunPodError:
                        pass
                    await self._rollback_deploy(completed_pairs, record)
                    record.status = DeployStatus.FAILED.value
                    record.error = f"New pod {new_pod.id} failed health check"
                    break

                # Step 3: Grace period (drain)
                if on_state_change:
                    on_state_change(old_pod.id, "DRAINING", new_pod.id)
                remaining = grace_seconds
                while remaining > 0 and not self._deploy_cancelled:
                    if on_countdown:
                        on_countdown(remaining)
                    await asyncio.sleep(min(1, remaining))
                    remaining -= 1
                if on_countdown:
                    on_countdown(0)

                if self._deploy_cancelled:
                    try:
                        await self.client.terminate_pod(new_pod.id)
                    except RunPodError:
                        pass
                    await self._rollback_deploy(completed_pairs, record)
                    record.status = DeployStatus.ROLLED_BACK.value
                    record.error = "Cancelled by user during grace period"
                    break

                # Step 4: Terminate old pod
                if on_state_change:
                    on_state_change(old_pod.id, "TERMINATE_OLD", new_pod.id)
                try:
                    await self.client.terminate_pod(old_pod.id)
                except RunPodError:
                    pass  # Old pod may already be stopped

                completed_pairs.append((old_pod, new_pod))
                if on_state_change:
                    on_state_change(old_pod.id, "COMPLETED", new_pod.id)
                if on_progress:
                    on_progress(len(completed_pairs), len(target_pods))
            else:
                # All pods completed successfully
                record.status = DeployStatus.COMPLETED.value
                record.pod_ids = [p.id for _, p in completed_pairs]

        except Exception as e:
            record.status = DeployStatus.FAILED.value
            record.error = str(e)
            await self._rollback_deploy(completed_pairs, record)

        record.duration_seconds = (datetime.now(timezone.utc) - start).total_seconds()
        self._record_deploy(record)
        self._active_deploy = None
        return record

    def cancel_deploy(self) -> None:
        self._deploy_cancelled = True

    async def _wait_for_healthy(self, pod_id: str, timeout: int) -> bool:
        elapsed = 0
        interval = 5
        while elapsed < timeout:
            if self._deploy_cancelled:
                return False
            try:
                pod = await self.client.get_pod(pod_id)
                if (
                    pod.desired_status == "RUNNING"
                    and pod.runtime is not None
                    and pod.runtime.uptime_seconds > 0
                ):
                    return True
            except RunPodError:
                pass
            await asyncio.sleep(interval)
            elapsed += interval
        return False

    async def _rollback_deploy(
        self, pairs: list[tuple[Pod, Pod]], record: DeployRecord
    ) -> None:
        record.status = DeployStatus.ROLLING_BACK.value
        for old_pod, new_pod in pairs:
            # Terminate replacement pods
            try:
                await self.client.terminate_pod(new_pod.id)
            except RunPodError:
                pass
            # Resume old pods if they were stopped
            try:
                await self.client.resume_pod(old_pod.id, gpu_count=old_pod.gpu_count)
            except RunPodError:
                pass

    # --- History ---

    def _record_deploy(self, record: DeployRecord) -> None:
        try:
            with open(self.history_path, "a") as f:
                f.write(record.to_json_line() + "\n")
                f.flush()
        except OSError:
            pass  # History is best-effort

    def load_history(self, limit: int = 200) -> list[DeployRecord]:
        if not self.history_path.exists():
            return []
        records = []
        try:
            with open(self.history_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        records.append(DeployRecord.from_dict(d))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        records.reverse()  # Most recent first
        return records[:limit]

    def truncate_history(self, keep: int = 500) -> None:
        if not self.history_path.exists():
            return
        try:
            with open(self.history_path) as f:
                lines = f.readlines()
            if len(lines) > keep * 2:
                with open(self.history_path, "w") as f:
                    f.writelines(lines[-keep:])
        except OSError:
            pass
