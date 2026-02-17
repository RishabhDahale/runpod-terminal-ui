"""Bulk actions screen — stop all, stop by template, stop by GPU, presets."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    ProgressBar,
    Select,
    Static,
)

from models import Pod
from runpod_client import RunPodError
from ui.app import ConfirmActionScreen
from ui.widgets.pod_table import PodTable


class BulkScreen(Screen):

    _pods: list[Pod] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="bulk-container"):
            yield Static("[bold cyan]Bulk Actions[/bold cyan]", classes="screen-title")

            # Quick actions
            with Vertical(classes="form-section"):
                yield Static("[bold]Quick Actions[/bold]", classes="form-section-title")
                with Horizontal(id="bulk-actions"):
                    yield Button("Stop All Running", variant="error", id="btn-stop-all")
                    yield Button("Terminate All", variant="error", id="btn-terminate-all")

            # Filter actions
            with Vertical(classes="form-section"):
                yield Static("[bold]Stop by Filter[/bold]", classes="form-section-title")
                with Horizontal(classes="form-row"):
                    yield Static("By Template:", classes="form-label")
                    yield Select([], id="template-filter", prompt="Select template...")
                    yield Button("Stop", variant="warning", id="btn-stop-by-template")
                with Horizontal(classes="form-row"):
                    yield Static("By GPU Type:", classes="form-label")
                    yield Select([], id="gpu-filter", prompt="Select GPU type...")
                    yield Button("Stop", variant="warning", id="btn-stop-by-gpu")
                with Horizontal(classes="form-row"):
                    yield Static("By Name:", classes="form-label")
                    yield Input(placeholder="Name pattern (e.g., worker)", id="name-filter")
                    yield Button("Stop Matching", variant="warning", id="btn-stop-by-name")

            # Presets
            with Vertical(classes="form-section"):
                yield Static("[bold]Scaling Presets[/bold]", classes="form-section-title")
                yield Select([], id="preset-select", prompt="Select a saved preset...")
                with Horizontal(classes="form-row"):
                    yield Button("Apply Preset", variant="success", id="btn-apply-preset")
                    yield Button("Save Current as Preset", variant="default", id="btn-save-preset")
                yield Input(placeholder="Preset name", id="preset-name")

            # Pod list
            with Vertical(classes="form-section"):
                yield Static("[bold]Current Pods[/bold]", classes="form-section-title")
                yield PodTable(id="bulk-pod-table")
                yield Button("Refresh", variant="default", id="btn-bulk-refresh")

            yield ProgressBar(id="bulk-progress", total=1, show_eta=False)

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#bulk-progress", ProgressBar).display = False
        self._refresh_data()

    def on_screen_resume(self) -> None:
        self._refresh_data()

    @work
    async def _refresh_data(self) -> None:
        try:
            self._pods = await self.app.api_client.list_pods()
            table = self.query_one("#bulk-pod-table", PodTable)
            table.update_pods(self._pods)
            self._update_filters()
        except RunPodError as e:
            self.notify(f"Failed to load pods: {e}", severity="error", timeout=8)

    def _update_filters(self) -> None:
        # Template filter
        templates = set()
        for pod in self._pods:
            if pod.image_name:
                templates.add(pod.image_name)
        template_select = self.query_one("#template-filter", Select)
        template_select.set_options([(t[:50], t) for t in sorted(templates)])

        # GPU filter
        gpus = set()
        for pod in self._pods:
            if pod.gpu_display_name:
                gpus.add(pod.gpu_display_name)
        gpu_select = self.query_one("#gpu-filter", Select)
        gpu_select.set_options([(g, g) for g in sorted(gpus)])

        # Presets
        presets = self.app.config.preferences.scaling_presets
        preset_select = self.query_one("#preset-select", Select)
        if presets:
            preset_select.set_options([(p.get("name", "unnamed"), i) for i, p in enumerate(presets)])
        else:
            preset_select.set_options([("No presets saved", -1)])

    # --- Stop All ---

    @on(Button.Pressed, "#btn-stop-all")
    def _on_stop_all(self, event: Button.Pressed) -> None:
        running = [p for p in self._pods if p.desired_status == "RUNNING"]
        if not running:
            self.notify("No running pods to stop", severity="information")
            return
        msg = f"[bold red]Stop ALL {len(running)} running pods?[/bold red]\n\nThis will stop but not destroy them."
        def on_confirm(result: bool) -> None:
            if result:
                self._execute_bulk_stop([p.id for p in running], "stop")
        self.app.push_screen(ConfirmActionScreen(msg, "Stop All"), callback=on_confirm)

    @on(Button.Pressed, "#btn-terminate-all")
    def _on_terminate_all(self, event: Button.Pressed) -> None:
        running = [p for p in self._pods if p.desired_status == "RUNNING"]
        if not running:
            self.notify("No running pods", severity="information")
            return
        msg = (
            f"[bold red]TERMINATE ALL {len(running)} running pods?[/bold red]\n\n"
            f"[red]This will permanently destroy all pods and their data![/red]"
        )
        def on_confirm(result: bool) -> None:
            if result:
                self._execute_bulk_stop([p.id for p in running], "terminate")
        self.app.push_screen(ConfirmActionScreen(msg, "Terminate All"), callback=on_confirm)

    # --- Stop by Filter ---

    @on(Button.Pressed, "#btn-stop-by-template")
    def _on_stop_by_template(self, event: Button.Pressed) -> None:
        select = self.query_one("#template-filter", Select)
        if not select.value or select.value == Select.BLANK:
            self.notify("Select a template/image first", severity="warning")
            return
        image = str(select.value)
        matching = [p for p in self._pods if p.image_name == image and p.desired_status == "RUNNING"]
        if not matching:
            self.notify("No running pods with that image", severity="information")
            return
        msg = f"[bold]Stop {len(matching)} pods with image {image[:40]}?[/bold]"
        def on_confirm(result: bool) -> None:
            if result:
                self._execute_bulk_stop([p.id for p in matching], "stop")
        self.app.push_screen(ConfirmActionScreen(msg, "Stop"), callback=on_confirm)

    @on(Button.Pressed, "#btn-stop-by-gpu")
    def _on_stop_by_gpu(self, event: Button.Pressed) -> None:
        select = self.query_one("#gpu-filter", Select)
        if not select.value or select.value == Select.BLANK:
            self.notify("Select a GPU type first", severity="warning")
            return
        gpu = str(select.value)
        matching = [p for p in self._pods if p.gpu_display_name == gpu and p.desired_status == "RUNNING"]
        if not matching:
            self.notify("No running pods with that GPU type", severity="information")
            return
        msg = f"[bold]Stop {len(matching)} pods with GPU {gpu}?[/bold]"
        def on_confirm(result: bool) -> None:
            if result:
                self._execute_bulk_stop([p.id for p in matching], "stop")
        self.app.push_screen(ConfirmActionScreen(msg, "Stop"), callback=on_confirm)

    @on(Button.Pressed, "#btn-stop-by-name")
    def _on_stop_by_name(self, event: Button.Pressed) -> None:
        pattern = self.query_one("#name-filter", Input).value.strip().lower()
        if not pattern:
            self.notify("Enter a name pattern", severity="warning")
            return
        matching = [
            p for p in self._pods
            if pattern in p.name.lower() and p.desired_status == "RUNNING"
        ]
        if not matching:
            self.notify(f"No running pods matching '{pattern}'", severity="information")
            return
        msg = f"[bold]Stop {len(matching)} pods matching '{pattern}'?[/bold]"
        def on_confirm(result: bool) -> None:
            if result:
                self._execute_bulk_stop([p.id for p in matching], "stop")
        self.app.push_screen(ConfirmActionScreen(msg, "Stop"), callback=on_confirm)

    @work(exclusive=True)
    async def _execute_bulk_stop(self, pod_ids: list[str], action: str) -> None:
        progress = self.query_one("#bulk-progress", ProgressBar)
        progress.display = True
        progress.total = len(pod_ids)
        progress.progress = 0

        def on_progress(done: int, total: int) -> None:
            progress.progress = done

        try:
            record = await self.app.pod_manager.scale_down(
                pod_ids, action=action, on_progress=on_progress
            )
            verb = "Stopped" if action == "stop" else "Terminated"
            if record.error:
                self.notify(f"{verb} with errors: {record.error}", severity="warning", timeout=10)
            else:
                self.notify(f"{verb} {len(pod_ids)} pods", severity="information")
        except RunPodError as e:
            self.notify(f"Bulk action failed: {e}", severity="error", timeout=10)
        finally:
            progress.display = False
            self._refresh_data()

    # --- Presets ---

    @on(Button.Pressed, "#btn-save-preset")
    def _on_save_preset(self, event: Button.Pressed) -> None:
        name = self.query_one("#preset-name", Input).value.strip()
        if not name:
            self.notify("Enter a preset name", severity="warning")
            return
        # Snapshot current running pods as a preset
        running = [p for p in self._pods if p.desired_status == "RUNNING"]
        entries = []
        for pod in running:
            entries.append({
                "image_name": pod.image_name,
                "gpu_type_id": pod.gpu_display_name,
                "gpu_count": pod.gpu_count,
                "pod_count": 1,
            })
        # Group by image+gpu
        grouped: dict[str, dict] = {}
        for e in entries:
            key = f"{e['image_name']}|{e['gpu_type_id']}"
            if key in grouped:
                grouped[key]["pod_count"] += 1
            else:
                grouped[key] = dict(e)
        preset = {"name": name, "entries": list(grouped.values())}
        self.app.config.preferences.scaling_presets.append(preset)
        self.app.config.preferences.save()
        self.notify(f"Preset '{name}' saved with {len(grouped)} configurations", severity="information")
        self._update_filters()

    @on(Button.Pressed, "#btn-apply-preset")
    def _on_apply_preset(self, event: Button.Pressed) -> None:
        select = self.query_one("#preset-select", Select)
        if not select.value or select.value == Select.BLANK or select.value == -1:
            self.notify("Select a preset first", severity="warning")
            return
        idx = int(select.value)
        presets = self.app.config.preferences.scaling_presets
        if idx < 0 or idx >= len(presets):
            self.notify("Invalid preset", severity="error")
            return
        preset = presets[idx]
        entries = preset.get("entries", [])
        total_pods = sum(e.get("pod_count", 1) for e in entries)
        msg = (
            f"[bold]Apply preset '{preset.get('name', 'unnamed')}'?[/bold]\n\n"
            f"Will create {total_pods} pods across {len(entries)} configurations."
        )
        def on_confirm(result: bool) -> None:
            if result:
                self._execute_preset(entries)
        self.app.push_screen(ConfirmActionScreen(msg, "Apply Preset"), callback=on_confirm)

    @work(exclusive=True)
    async def _execute_preset(self, entries: list[dict]) -> None:
        total_created = 0
        errors = []
        for entry in entries:
            try:
                record = await self.app.pod_manager.scale_up(
                    count=entry.get("pod_count", 1),
                    name_prefix="preset",
                    image_name=entry.get("image_name", ""),
                    gpu_type_id=entry.get("gpu_type_id", ""),
                    gpu_count=entry.get("gpu_count", 1),
                )
                total_created += record.pod_count
                if record.error:
                    errors.append(record.error)
            except RunPodError as e:
                errors.append(str(e))

        if errors:
            self.notify(f"Created {total_created} pods with errors: {'; '.join(errors)}", severity="warning", timeout=10)
        else:
            self.notify(f"Preset applied — created {total_created} pods", severity="information")
        self._refresh_data()

    # --- Refresh ---

    @on(Button.Pressed, "#btn-bulk-refresh")
    def _on_refresh(self, event: Button.Pressed) -> None:
        self._refresh_data()
