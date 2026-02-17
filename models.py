"""Data models for RunPod Dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PodStatus(str, Enum):
    RUNNING = "RUNNING"
    EXITED = "EXITED"
    CREATED = "CREATED"
    RESTARTING = "RESTARTING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class CloudType(str, Enum):
    ALL = "ALL"
    COMMUNITY = "COMMUNITY"
    SECURE = "SECURE"


class DeployStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    HEALTH_CHECK = "health_check"
    DRAINING = "draining"
    ROLLING_BACK = "rolling_back"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class GpuMetrics:
    gpu_util_percent: float = 0.0
    memory_util_percent: float = 0.0


@dataclass
class PortMapping:
    ip: str = ""
    is_ip_public: bool = False
    private_port: int = 0
    public_port: int = 0
    port_type: str = ""


@dataclass
class PodRuntime:
    uptime_seconds: int = 0
    gpus: list[GpuMetrics] = field(default_factory=list)
    ports: list[PortMapping] = field(default_factory=list)


@dataclass
class Pod:
    id: str
    name: str
    image_name: str = ""
    desired_status: str = ""
    cost_per_hr: float = 0.0
    gpu_count: int = 0
    gpu_display_name: str = ""
    volume_in_gb: int = 0
    container_disk_in_gb: int = 0
    volume_mount_path: str = "/workspace"
    template_id: str = ""
    machine_id: str = ""
    runtime: Optional[PodRuntime] = None
    env: list[str] = field(default_factory=list)  # ["KEY=VALUE", ...]
    ports_config: str = ""

    @property
    def status_color(self) -> str:
        if self.desired_status == "RUNNING" and self.runtime and self.runtime.uptime_seconds > 0:
            return "green"
        elif self.desired_status == "RUNNING":
            return "yellow"
        elif self.desired_status in ("EXITED", "STOPPED"):
            return "red"
        return "dim"

    @property
    def uptime_display(self) -> str:
        if not self.runtime or self.runtime.uptime_seconds == 0:
            return "--"
        secs = self.runtime.uptime_seconds
        hours, remainder = divmod(secs, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @property
    def avg_gpu_util(self) -> Optional[float]:
        if not self.runtime or not self.runtime.gpus:
            return None
        return sum(g.gpu_util_percent for g in self.runtime.gpus) / len(self.runtime.gpus)

    @property
    def avg_mem_util(self) -> Optional[float]:
        if not self.runtime or not self.runtime.gpus:
            return None
        return sum(g.memory_util_percent for g in self.runtime.gpus) / len(self.runtime.gpus)


@dataclass
class GpuType:
    id: str
    display_name: str
    memory_gb: int = 0
    secure_price: float = 0.0
    community_price: float = 0.0
    max_gpu_count: int = 0
    max_gpu_count_community: int = 0
    max_gpu_count_secure: int = 0
    # Availability fields (from lowestPrice query)
    stock_status: str = ""  # "High", "Medium", "Low", or ""
    available_gpu_counts: list[int] = field(default_factory=list)
    max_unreserved_gpu_count: int = 0
    total_count: int = 0
    rented_count: int = 0
    rental_percentage: float = 0.0
    secure_cloud: bool = False
    community_cloud: bool = False

    @property
    def lowest_price(self) -> float:
        prices = [p for p in [self.community_price, self.secure_price] if p > 0]
        return min(prices) if prices else 0.0

    @property
    def available_count(self) -> int:
        """Number of GPUs available right now."""
        return max(self.total_count - self.rented_count, 0)

    @property
    def is_available(self) -> bool:
        """Whether at least 1 GPU is available."""
        return self.stock_status in ("High", "Medium", "Low")


@dataclass
class Template:
    id: str
    name: str
    image_name: str
    category: str = ""
    container_disk_in_gb: int = 5
    volume_in_gb: int = 0
    volume_mount_path: str = "/workspace"
    docker_start_cmd: str = ""
    env: dict[str, str] = field(default_factory=dict)  # {"KEY": "VALUE"}
    ports: str = ""
    is_public: bool = False
    is_serverless: bool = False


@dataclass
class ScalingPreset:
    name: str
    entries: list[dict] = field(default_factory=list)
    # Each entry: {"template_id": str, "gpu_type_id": str, "gpu_count": int, "pod_count": int, "cloud_type": str}


@dataclass
class DeployRecord:
    deploy_id: str
    timestamp: str
    action: str  # scale_up, scale_down, rolling_deploy, rollback, bulk_stop
    status: str  # DeployStatus value
    template_name: str = ""
    gpu_type: str = ""
    pod_count: int = 0
    pod_ids: list[str] = field(default_factory=list)
    old_image: str = ""
    new_image: str = ""
    region: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    notes: str = ""

    def to_json_line(self) -> str:
        d = {
            "deploy_id": self.deploy_id,
            "timestamp": self.timestamp,
            "action": self.action,
            "status": self.status,
            "template_name": self.template_name,
            "gpu_type": self.gpu_type,
            "pod_count": self.pod_count,
            "pod_ids": self.pod_ids,
            "old_image": self.old_image,
            "new_image": self.new_image,
            "region": self.region,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "notes": self.notes,
        }
        return json.dumps(d)

    @classmethod
    def from_dict(cls, d: dict) -> DeployRecord:
        return cls(
            deploy_id=d.get("deploy_id", ""),
            timestamp=d.get("timestamp", ""),
            action=d.get("action", ""),
            status=d.get("status", ""),
            template_name=d.get("template_name", ""),
            gpu_type=d.get("gpu_type", ""),
            pod_count=d.get("pod_count", 0),
            pod_ids=d.get("pod_ids", []),
            old_image=d.get("old_image", ""),
            new_image=d.get("new_image", ""),
            region=d.get("region", ""),
            error=d.get("error", ""),
            duration_seconds=d.get("duration_seconds", 0.0),
            notes=d.get("notes", ""),
        )
