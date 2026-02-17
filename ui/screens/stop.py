"""Stop instances screen — select and stop running pods."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Static,
)
from rich.text import Text

from models import Pod
from runpod_client import RunPodError
from ui.app import ConfirmActionScreen

# Column keys for cell updates
COL_SEL = "sel"
COL_NAME = "name"
COL_GPU = "gpu"
COL_STATUS = "status"
COL_UPTIME = "uptime"
COL_COST = "cost"

# Column definitions: (label, key, width)
STOP_COLS = [
    ("Sel", COL_SEL, 5),
    ("Name", COL_NAME, 30),
    ("GPU", COL_GPU, 20),
    ("Status", COL_STATUS, 10),
    ("Uptime", COL_UPTIME, 10),
    ("$/hr", COL_COST, 8),
]

# Sortable columns → default descending on first click
SORTABLE_STOP = {
    COL_NAME: False,
    COL_COST: True,
    COL_UPTIME: True,
}


class StopScreen(Screen):
    """Screen for selecting and stopping running pods."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
        Binding("space", "toggle_select", "Toggle", priority=True),
        Binding("a", "select_all", "Select All", priority=True),
        Binding("n", "deselect_all", "Deselect", priority=True),
        Binding("enter", "confirm_stop", "Stop Selected", priority=True),
        Binding("t", "confirm_terminate", "Terminate", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._pods: list[Pod] = []
        self._filtered_pods: list[Pod] = []
        self._selected_ids: set[str] = set()
        self._table_built = False
        self._sort_col: str = COL_NAME
        self._sort_reverse: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="stop-container"):
            yield Static(
                "[bold cyan]Stop Instances[/bold cyan]  "
                "[dim]Space: toggle | a: all | n: none | Enter: stop | t: terminate | Click header to sort[/dim]",
                id="stop-title",
            )
            with Horizontal(id="stop-filter-bar"):
                yield Input(
                    placeholder="Type to filter by name...",
                    id="filter-input",
                )
            yield Static("", id="selection-count")
            yield DataTable(id="stop-pod-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#stop-pod-table", DataTable)
        table.zebra_stripes = True
        self._table_built = True
        self._load_pods()

    def on_screen_resume(self) -> None:
        self._load_pods()

    @work
    async def _load_pods(self) -> None:
        try:
            self._pods = await self.app.api_client.list_pods()
            self._selected_ids.clear()
            self._apply_filter()
        except RunPodError as e:
            self.notify(f"Failed to load pods: {e}", severity="error")

    def _apply_filter(self) -> None:
        filter_text = self.query_one("#filter-input", Input).value.strip().lower()
        if filter_text:
            self._filtered_pods = [
                p for p in self._pods if filter_text in p.name.lower()
            ]
        else:
            self._filtered_pods = list(self._pods)
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        """Full rebuild — called on data load, filter change, or sort change."""
        table = self.query_one("#stop-pod-table", DataTable)
        cursor_pod_id = self._get_cursor_pod_id()

        table.clear(columns=True)
        for label, key, width in STOP_COLS:
            col_label = label
            if key in SORTABLE_STOP and key == self._sort_col:
                col_label = f"{label} {'▼' if self._sort_reverse else '▲'}"
            table.add_column(col_label, key=key, width=width)

        sorted_pods = self._sorted_filtered_pods()
        for pod in sorted_pods:
            table.add_row(
                self._sel_cell(pod.id),
                self._name_cell(pod.id, pod.name),
                self._gpu_text(pod),
                self._status_cell(pod),
                pod.uptime_display,
                f"${pod.cost_per_hr:.2f}",
                key=pod.id,
            )

        # Restore cursor
        if cursor_pod_id:
            for i, pod in enumerate(sorted_pods):
                if pod.id == cursor_pod_id:
                    table.move_cursor(row=i)
                    break

        self._update_selection_count()

    def _sorted_filtered_pods(self) -> list[Pod]:
        """Return filtered pods in current sort order."""
        pods = list(self._filtered_pods)
        if self._sort_col == COL_NAME:
            pods.sort(key=lambda p: p.name.lower(), reverse=self._sort_reverse)
        elif self._sort_col == COL_COST:
            pods.sort(key=lambda p: p.cost_per_hr, reverse=self._sort_reverse)
        elif self._sort_col == COL_UPTIME:
            pods.sort(
                key=lambda p: (p.runtime.uptime_seconds if p.runtime else 0),
                reverse=self._sort_reverse,
            )
        return pods

    # --- Cell helpers ---

    def _sel_cell(self, pod_id: str) -> Text:
        if pod_id in self._selected_ids:
            return Text("[x]", style="bold green")
        return Text("[ ]", style="dim")

    def _name_cell(self, pod_id: str, name: str) -> Text:
        style = "bold" if pod_id in self._selected_ids else ""
        return Text(name[:30], style=style)

    @staticmethod
    def _gpu_text(pod: Pod) -> str:
        if pod.gpu_display_name:
            return f"{pod.gpu_display_name} x{pod.gpu_count}"
        return f"x{pod.gpu_count}"

    @staticmethod
    def _status_cell(pod: Pod) -> Text:
        styles = {"green": "bold green", "yellow": "bold yellow", "red": "bold red"}
        return Text(pod.desired_status, style=styles.get(pod.status_color, "dim"))

    def _update_sel_cells(self) -> None:
        """Update only the Sel and Name columns — no cursor reset."""
        table = self.query_one("#stop-pod-table", DataTable)
        for pod in self._filtered_pods:
            try:
                table.update_cell(pod.id, COL_SEL, self._sel_cell(pod.id))
                table.update_cell(pod.id, COL_NAME, self._name_cell(pod.id, pod.name))
            except Exception:
                pass
        self._update_selection_count()

    def _update_selection_count(self) -> None:
        count = len(self._selected_ids)
        total = len(self._filtered_pods)
        cost_saved = sum(
            p.cost_per_hr for p in self._pods if p.id in self._selected_ids
        )
        label = self.query_one("#selection-count", Static)
        if count > 0:
            label.update(
                f"  [bold]{count} pod(s) selected[/bold]  |  "
                f"Cost saved: [bold green]${cost_saved:.2f}/hr[/bold green]  |  "
                f"Showing {total} of {len(self._pods)} pods"
            )
        else:
            label.update(
                f"  [dim]Showing {total} of {len(self._pods)} pods  |  No pods selected[/dim]"
            )

    def _get_cursor_pod_id(self) -> str | None:
        table = self.query_one("#stop-pod-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < table.row_count:
            keys = list(table.rows.keys())
            if table.cursor_row < len(keys):
                key = keys[table.cursor_row]
                return str(key.value) if hasattr(key, "value") else str(key)
        return None

    # --- Sort handler ---

    @on(DataTable.HeaderSelected, "#stop-pod-table")
    def _on_header_selected(self, event: DataTable.HeaderSelected) -> None:
        col_key = (
            str(event.column_key.value)
            if hasattr(event.column_key, "value")
            else str(event.column_key)
        )
        if col_key not in SORTABLE_STOP:
            return
        if col_key == self._sort_col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col_key
            self._sort_reverse = SORTABLE_STOP[col_key]
        self._rebuild_table()

    # --- Actions ---

    def action_go_back(self) -> None:
        self.app.action_switch_to("dashboard")

    def action_toggle_select(self) -> None:
        table = self.query_one("#stop-pod-table", DataTable)
        pod_id = self._get_cursor_pod_id()
        if not pod_id:
            return

        # Toggle selection
        if pod_id in self._selected_ids:
            self._selected_ids.discard(pod_id)
        else:
            self._selected_ids.add(pod_id)

        # Update only the toggled row's cells
        pod = next((p for p in self._filtered_pods if p.id == pod_id), None)
        if pod:
            try:
                table.update_cell(pod_id, COL_SEL, self._sel_cell(pod_id))
                table.update_cell(pod_id, COL_NAME, self._name_cell(pod_id, pod.name))
            except Exception:
                pass
        self._update_selection_count()

        # Move cursor down to next row for quick multi-select
        if table.cursor_row is not None and table.cursor_row < table.row_count - 1:
            table.move_cursor(row=table.cursor_row + 1)

    def action_select_all(self) -> None:
        self._selected_ids = {p.id for p in self._filtered_pods}
        self._update_sel_cells()

    def action_deselect_all(self) -> None:
        self._selected_ids.clear()
        self._update_sel_cells()

    def action_confirm_stop(self) -> None:
        self._confirm_action("stop")

    def action_confirm_terminate(self) -> None:
        self._confirm_action("terminate")

    def _confirm_action(self, action: str) -> None:
        if not self._selected_ids:
            self.notify("No pods selected — use Space to select", severity="warning")
            return

        selected_pods = [p for p in self._pods if p.id in self._selected_ids]
        total_cost = sum(p.cost_per_hr for p in selected_pods)

        pod_lines = "\n".join(
            f"  {p.name}  ({p.gpu_display_name}, {p.uptime_display})"
            for p in selected_pods[:10]
        )
        if len(selected_pods) > 10:
            pod_lines += f"\n  ... and {len(selected_pods) - 10} more"

        verb = "Stop" if action == "stop" else "Terminate"
        msg = (
            f"[bold]{verb} {len(selected_pods)} pod(s)?[/bold]\n\n"
            f"{pod_lines}\n\n"
            f"[bold green]Cost saved: ${total_cost:.2f}/hr[/bold green]"
        )
        if action == "terminate":
            msg += "\n\n[bold red]This will permanently destroy the pods and their data![/bold red]"

        def on_confirm(result: bool) -> None:
            if result:
                self._execute_action(list(self._selected_ids), action)

        self.app.push_screen(
            ConfirmActionScreen(msg, verb), callback=on_confirm
        )

    @on(Input.Changed, "#filter-input")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        self._apply_filter()

    # --- Execution ---

    @work(exclusive=True)
    async def _execute_action(self, pod_ids: list[str], action: str) -> None:
        count = len(pod_ids)
        label = self.query_one("#selection-count", Static)
        verb = "Stopping" if action == "stop" else "Terminating"
        label.update(f"  [bold yellow]{verb} {count} pod(s)...[/bold yellow]")

        try:
            record = await self.app.pod_manager.scale_down(
                pod_ids, action=action
            )
            if record.error:
                self.notify(
                    f"Errors during {action}: {record.error}",
                    severity="warning",
                    timeout=10,
                )
            else:
                past = "Stopped" if action == "stop" else "Terminated"
                self.notify(
                    f"{past} {count} pod(s)",
                    severity="information",
                )
        except RunPodError as e:
            self.notify(f"{action.title()} failed: {e}", severity="error", timeout=10)
        finally:
            self._selected_ids.clear()
            self._load_pods()
