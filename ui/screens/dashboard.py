"""Dashboard screen — live pod status with template summary and quick actions."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from models import Pod
from runpod_client import RunPodConnectionError, RunPodError
from ui.app import ConfirmActionScreen
from ui.widgets.pod_table import PodTable


class DashboardScreen(Screen):

    BINDINGS = [
        ("f5", "force_refresh", "Refresh"),
        ("x", "stop_pod", "Stop Pod"),
        ("i", "pod_info", "Pod Info"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._pods: list[Pod] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(" Loading pods...", id="summary-bar")
        yield PodTable(id="pod-table")
        yield Static("", id="template-summary")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(10.0, self._auto_refresh)
        self.refresh_data()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def _auto_refresh(self) -> None:
        self.refresh_data()

    @work(exclusive=True)
    async def refresh_data(self) -> None:
        try:
            self._pods = await self.app.api_client.list_pods()
        except RunPodConnectionError:
            self.notify(
                "Lost connection to RunPod API", severity="warning", timeout=8
            )
            return
        except RunPodError as e:
            self.notify(f"API error: {e}", severity="error", timeout=8)
            return

        table = self.query_one("#pod-table", PodTable)
        table.update_pods(self._pods)
        self._update_summary(self._pods)
        self._update_template_summary(self._pods)

    def _update_summary(self, pods: list[Pod]) -> None:
        bar = self.query_one("#summary-bar", Static)

        if not pods:
            bar.update(
                " [dim]No active pods. Press [bold]s[/bold] to deploy.[/dim]"
            )
            return

        running = [p for p in pods if p.desired_status == "RUNNING"]
        total_cost = sum(p.cost_per_hr for p in running)
        stopped = sum(
            1 for p in pods if p.desired_status in ("EXITED", "STOPPED")
        )

        # Uptime range
        uptimes = [
            p.runtime.uptime_seconds
            for p in running
            if p.runtime and p.runtime.uptime_seconds > 0
        ]
        uptime_range = ""
        if uptimes:
            lo, hi = min(uptimes), max(uptimes)
            lo_s = f"{lo // 3600}h {(lo % 3600) // 60}m" if lo >= 3600 else f"{lo // 60}m"
            hi_s = f"{hi // 3600}h {(hi % 3600) // 60}m" if hi >= 3600 else f"{hi // 60}m"
            uptime_range = f"Uptime: {lo_s}–{hi_s}"

        parts = [
            f" Active: [bold]{len(running)}[/bold]",
            f"Total: [bold green]${total_cost:.2f}/hr[/bold green]",
        ]
        if stopped:
            parts.append(f"Stopped: {stopped}")
        if uptime_range:
            parts.append(uptime_range)

        bar.update("  |  ".join(parts))

    def _update_template_summary(self, pods: list[Pod]) -> None:
        summary = self.query_one("#template-summary", Static)
        running = [p for p in pods if p.desired_status == "RUNNING"]

        if not running:
            summary.update("")
            return

        # Group by image short name
        groups: dict[str, list[Pod]] = {}
        for p in running:
            key = (
                p.image_name.split("/")[-1].split(":")[0]
                if p.image_name
                else "unknown"
            )
            groups.setdefault(key, []).append(p)

        lines = [" [bold]Summary by Template[/bold]"]
        for name, group_pods in sorted(
            groups.items(), key=lambda x: -len(x[1])
        ):
            cost = sum(p.cost_per_hr for p in group_pods)
            gpus = set(
                p.gpu_display_name for p in group_pods if p.gpu_display_name
            )
            gpu_str = ", ".join(gpus) if gpus else "—"
            n = len(group_pods)
            label = "pod" if n == 1 else "pods"
            lines.append(
                f"   {name}: {n} {label}  |  ${cost:.2f}/hr  |  {gpu_str}"
            )

        summary.update("\n".join(lines))

    # --- Quick Actions ---

    def action_force_refresh(self) -> None:
        self.notify("Refreshing...", timeout=2)
        self.refresh_data()

    def action_stop_pod(self) -> None:
        table = self.query_one("#pod-table", PodTable)
        pod_id = table.get_selected_pod_id()
        if not pod_id:
            self.notify("Select a pod first", severity="warning")
            return

        pod = next((p for p in self._pods if p.id == pod_id), None)
        if not pod:
            return

        msg = (
            f"[bold]Stop pod {pod.name}?[/bold]\n\n"
            f"GPU: {pod.gpu_display_name}\n"
            f"Cost: ${pod.cost_per_hr:.2f}/hr\n"
            f"Uptime: {pod.uptime_display}"
        )

        def on_confirm(result: bool) -> None:
            if result:
                self._do_stop_pod(pod_id)

        self.app.push_screen(
            ConfirmActionScreen(msg, "Stop"), callback=on_confirm
        )

    @work(exclusive=True)
    async def _do_stop_pod(self, pod_id: str) -> None:
        try:
            await self.app.api_client.stop_pod(pod_id)
            self.notify("Pod stopped", severity="information")
        except RunPodError as e:
            self.notify(f"Failed to stop pod: {e}", severity="error", timeout=8)
        finally:
            self.refresh_data()

    def action_pod_info(self) -> None:
        table = self.query_one("#pod-table", PodTable)
        pod_id = table.get_selected_pod_id()
        if not pod_id:
            self.notify("Select a pod first", severity="warning")
            return

        pod = next((p for p in self._pods if p.id == pod_id), None)
        if not pod:
            return

        info_lines = [
            f"[bold]Pod Details[/bold]",
            f"  ID:     {pod.id}",
            f"  Name:   {pod.name}",
            f"  Image:  {pod.image_name}",
            f"  GPU:    {pod.gpu_display_name} x{pod.gpu_count}",
            f"  Status: {pod.desired_status}",
            f"  Cost:   ${pod.cost_per_hr:.2f}/hr",
            f"  Uptime: {pod.uptime_display}",
            f"  Volume: {pod.volume_in_gb}GB  |  Disk: {pod.container_disk_in_gb}GB",
        ]
        if pod.runtime and pod.runtime.gpus:
            for i, gpu in enumerate(pod.runtime.gpus):
                info_lines.append(
                    f"  GPU {i}: {gpu.gpu_util_percent:.0f}% util, "
                    f"{gpu.memory_util_percent:.0f}% mem"
                )

        self.notify("\n".join(info_lines), severity="information", timeout=15)
