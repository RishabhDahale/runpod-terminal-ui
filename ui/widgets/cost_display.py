"""Cost estimation widget that updates reactively."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual.reactive import reactive
from textual.widgets import Static

from models import GpuType
from pod_manager import PodManager


class CostDisplay(Static):

    gpu_price: reactive[float] = reactive(0.0)
    gpu_count: reactive[int] = reactive(1)
    pod_count: reactive[int] = reactive(1)

    def render(self) -> str:
        if self.gpu_price <= 0:
            return "[dim]Select a GPU type to see cost estimate[/dim]"
        per_pod = self.gpu_price * self.gpu_count
        total = per_pod * self.pod_count
        return (
            f"[bold]Cost Estimate:[/bold]  "
            f"${per_pod:.2f}/hr per pod  |  "
            f"[bold green]${total:.2f}/hr total[/bold green] for {self.pod_count} pod(s)  |  "
            f"~${total * 24:.2f}/day"
        )

    def update_from_gpu(self, gpu_type: GpuType | None, cloud_type: str = "ALL") -> None:
        if not gpu_type:
            self.gpu_price = 0.0
            return
        if cloud_type == "COMMUNITY":
            self.gpu_price = gpu_type.community_price
        elif cloud_type == "SECURE":
            self.gpu_price = gpu_type.secure_price
        else:
            self.gpu_price = gpu_type.lowest_price
