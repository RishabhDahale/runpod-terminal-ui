"""GPU type selector with availability and pricing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual import work
from textual.widgets import Select

from models import GpuType
from runpod_client import RunPodError


class GpuSelector(Select):
    """Dropdown that loads GPU types from RunPod API with pricing info."""

    _gpu_types: list[GpuType] = []

    def on_mount(self) -> None:
        self.loading = True
        self._load_gpu_types()

    @work
    async def _load_gpu_types(self) -> None:
        try:
            self._gpu_types = await self.app.api_client.list_gpu_types()
            # Sort: available first, then by price
            available = sorted(
                [g for g in self._gpu_types if g.lowest_price > 0 and g.is_available],
                key=lambda g: g.lowest_price,
            )
            unavailable = sorted(
                [g for g in self._gpu_types if g.lowest_price > 0 and not g.is_available],
                key=lambda g: g.lowest_price,
            )
            options = [
                (
                    f"{g.display_name} ({g.memory_gb}GB) — ${g.lowest_price:.2f}/hr"
                    f" [{g.available_count} avail]",
                    g.id,
                )
                for g in available
            ] + [
                (
                    f"{g.display_name} ({g.memory_gb}GB) — ${g.lowest_price:.2f}/hr [UNAVAIL]",
                    g.id,
                )
                for g in unavailable
            ]
            self.set_options(options)
        except RunPodError as e:
            self.set_options([("Error loading GPUs — try again", "")])
            self.app.notify(f"Failed to load GPU types: {e}", severity="error", timeout=8)
        finally:
            self.loading = False

    def get_gpu_type(self, gpu_id: str) -> GpuType | None:
        return next((g for g in self._gpu_types if g.id == gpu_id), None)

    @property
    def gpu_types(self) -> list[GpuType]:
        return self._gpu_types

    def reload(self) -> None:
        self.loading = True
        self._load_gpu_types()

    def preselect(self, gpu_id: str) -> None:
        if gpu_id:
            self.value = gpu_id
