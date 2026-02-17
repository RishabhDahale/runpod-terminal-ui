"""Deployment history viewer."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Select, Static
from rich.text import Text

from models import DeployRecord


class LogsScreen(Screen):

    _records: list[DeployRecord] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="logs-container"):
            yield Static("[bold cyan]Deployment History[/bold cyan]", classes="screen-title")
            with Vertical(id="logs-filter-bar"):
                yield Select(
                    [
                        ("All Actions", "all"),
                        ("Scale Up", "scale_up"),
                        ("Scale Down", "scale_down"),
                        ("Rolling Deploy", "rolling_deploy"),
                        ("Rollback", "rollback"),
                        ("Bulk Stop", "bulk_stop"),
                    ],
                    id="action-filter",
                    value="all",
                    prompt="Filter by action...",
                )
            yield DataTable(id="logs-table")
            yield Static("", id="log-detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#logs-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_column("Time", key="time", width=20)
        table.add_column("Action", key="action", width=16)
        table.add_column("Status", key="status", width=14)
        table.add_column("Pods", key="pods", width=6)
        table.add_column("GPU", key="gpu", width=24)
        table.add_column("Duration", key="duration", width=10)
        table.add_column("Error", key="error", width=30)
        self._load_records()

    def on_screen_resume(self) -> None:
        self._load_records()

    def _load_records(self, action_filter: str = "all") -> None:
        self._records = self.app.pod_manager.load_history()
        if action_filter != "all":
            self._records = [r for r in self._records if r.action == action_filter]
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#logs-table", DataTable)
        table.clear()
        for record in self._records:
            status_style = {
                "completed": "bold green",
                "failed": "bold red",
                "rolled_back": "bold yellow",
                "in_progress": "bold cyan",
            }.get(record.status, "dim")

            # Format timestamp
            ts = record.timestamp
            if "T" in ts:
                ts = ts.replace("T", " ")[:19]

            # Format duration
            dur = record.duration_seconds
            if dur > 0:
                dur_str = f"{dur:.0f}s" if dur < 60 else f"{dur/60:.1f}m"
            else:
                dur_str = "--"

            table.add_row(
                ts,
                record.action.replace("_", " "),
                Text(record.status, style=status_style),
                str(record.pod_count),
                record.gpu_type[:22] if record.gpu_type else "--",
                dur_str,
                Text(record.error[:28] if record.error else "", style="red" if record.error else "dim"),
                key=record.deploy_id,
            )

    @on(Select.Changed, "#action-filter")
    def _on_filter_changed(self, event: Select.Changed) -> None:
        filter_val = str(event.value) if event.value and event.value != Select.BLANK else "all"
        self._load_records(action_filter=filter_val)

    @on(DataTable.RowSelected, "#logs-table")
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        row_key = str(event.row_key.value) if hasattr(event.row_key, "value") else str(event.row_key)
        record = next((r for r in self._records if r.deploy_id == row_key), None)
        if not record:
            return

        detail = self.query_one("#log-detail", Static)
        lines = [
            f"[bold]Deploy ID:[/bold] {record.deploy_id}",
            f"[bold]Action:[/bold] {record.action}  |  [bold]Status:[/bold] {record.status}",
            f"[bold]Timestamp:[/bold] {record.timestamp}",
            f"[bold]Pod IDs:[/bold] {', '.join(record.pod_ids[:5]) or 'N/A'}",
        ]
        if record.old_image:
            lines.append(f"[bold]Old Image:[/bold] {record.old_image}")
        if record.new_image:
            lines.append(f"[bold]New Image:[/bold] {record.new_image}")
        if record.error:
            lines.append(f"[bold red]Error:[/bold red] {record.error}")
        if record.notes:
            lines.append(f"[bold]Notes:[/bold] {record.notes}")
        detail.update("\n".join(lines))
