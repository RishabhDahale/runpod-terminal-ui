"""Live pod status table with color-coded rows and column sorting."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual.widgets import DataTable
from rich.text import Text

from models import Pod


class PodTable(DataTable):
    """DataTable for displaying pods with sortable columns.

    Click a column header to sort. Sortable: Name, GPU %, $/hr.
    """

    COLUMN_DEFS = [
        ("ID", "id"),
        ("Name", "name"),
        ("GPU", "gpu"),
        ("Status", "status"),
        ("GPU %", "gpu_util"),
        ("Mem %", "mem_util"),
        ("Uptime", "uptime"),
        ("$/hr", "cost_hr"),
    ]

    # col_key → default descending on first click
    SORTABLE = {
        "name": False,
        "gpu_util": True,
        "cost_hr": True,
    }

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pods: list[Pod] = []
        self._sort_col: str = "name"
        self._sort_reverse: bool = False

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._build_columns()

    def _col_label(self, base: str, key: str) -> str:
        if key == self._sort_col:
            return f"{base} {'▼' if self._sort_reverse else '▲'}"
        return base

    def _build_columns(self) -> None:
        self.clear(columns=True)
        for label, key in self.COLUMN_DEFS:
            self.add_column(self._col_label(label, key), key=key)

    def _sorted_pods(self) -> list[Pod]:
        pods = list(self._pods)
        if self._sort_col == "name":
            pods.sort(key=lambda p: p.name.lower(), reverse=self._sort_reverse)
        elif self._sort_col == "gpu_util":
            # None values go last regardless of direction
            if not self._sort_reverse:
                pods.sort(key=lambda p: (p.avg_gpu_util is None, p.avg_gpu_util or 0))
            else:
                pods.sort(key=lambda p: (p.avg_gpu_util is None, -(p.avg_gpu_util or 0)))
        elif self._sort_col == "cost_hr":
            pods.sort(key=lambda p: p.cost_per_hr, reverse=self._sort_reverse)
        return pods

    def update_pods(self, pods: list[Pod]) -> None:
        self._pods = list(pods)
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        """Rebuild rows in sorted order, preserving cursor position."""
        cursor_pod_id = self.get_selected_pod_id()
        sorted_pods = self._sorted_pods()
        self.clear()
        for pod in sorted_pods:
            self.add_row(*self._pod_row(pod), key=pod.id)
        self._restore_cursor(cursor_pod_id, sorted_pods)

    def _full_rebuild(self) -> None:
        """Rebuild columns (with updated sort indicators) and rows."""
        cursor_pod_id = self.get_selected_pod_id()
        self._build_columns()
        sorted_pods = self._sorted_pods()
        for pod in sorted_pods:
            self.add_row(*self._pod_row(pod), key=pod.id)
        self._restore_cursor(cursor_pod_id, sorted_pods)

    def _restore_cursor(self, pod_id: str | None, sorted_pods: list[Pod]) -> None:
        if not pod_id:
            return
        for i, pod in enumerate(sorted_pods):
            if pod.id == pod_id:
                self.move_cursor(row=i)
                return

    def on_data_table_header_selected(
        self, event: DataTable.HeaderSelected
    ) -> None:
        col_key = (
            str(event.column_key.value)
            if hasattr(event.column_key, "value")
            else str(event.column_key)
        )
        if col_key not in self.SORTABLE:
            return
        if col_key == self._sort_col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col_key
            self._sort_reverse = self.SORTABLE[col_key]
        self._full_rebuild()

    def _pod_row(self, pod: Pod) -> list:
        status_styles = {
            "green": "bold green",
            "yellow": "bold yellow",
            "red": "bold red",
            "dim": "dim",
        }
        style = status_styles.get(pod.status_color, "dim")

        return [
            pod.id[:12],
            Text(pod.name[:20], style="bold"),
            f"{pod.gpu_display_name} x{pod.gpu_count}" if pod.gpu_display_name else f"x{pod.gpu_count}",
            Text(pod.desired_status, style=style),
            self._util_text(pod.avg_gpu_util),
            self._util_text(pod.avg_mem_util),
            pod.uptime_display,
            f"${pod.cost_per_hr:.2f}",
        ]

    @staticmethod
    def _util_text(value: float | None) -> Text:
        if value is None:
            return Text("--", style="dim")
        if value < 50:
            color = "green"
        elif value < 80:
            color = "yellow"
        else:
            color = "red"
        return Text(f"{value:.0f}%", style=color)

    def get_selected_pod_id(self) -> str | None:
        if self.cursor_row is not None and self.cursor_row < self.row_count:
            try:
                keys = list(self.rows.keys())
                if self.cursor_row < len(keys):
                    key = keys[self.cursor_row]
                    return str(key.value) if hasattr(key, "value") else str(key)
            except Exception:
                pass
        return None
