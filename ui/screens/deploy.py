"""Rolling deploy screen — deploy new image with health checks and rollback."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.validation import Number
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Select,
    Static,
)

from models import Pod
from runpod_client import RunPodError
from ui.app import ConfirmActionScreen


class DeployScreen(Screen):

    _running_pods: list[Pod] = []
    _deploy_active: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="deploy-container"):
            with Vertical(classes="form-section"):
                yield Static(
                    "[bold cyan]Rolling Deploy — Update Pod Image[/bold cyan]",
                    classes="form-section-title",
                )
                yield Static(
                    "[dim]Select running pods, provide a new image, and deploy with health checks.[/dim]"
                )

                with Horizontal(classes="form-row"):
                    yield Static("Target Pods:", classes="form-label")
                    yield Select([], id="target-pod-select", prompt="Select pod to deploy...")

                with Horizontal(classes="form-row"):
                    yield Static("Deploy All:", classes="form-label")
                    yield Button("Select All Running", variant="default", id="btn-select-all")

                with Horizontal(classes="form-row"):
                    yield Static("New Image:", classes="form-label")
                    yield Input(
                        placeholder="e.g., myrepo/myimage:v2",
                        id="new-image-input",
                    )

                with Horizontal(classes="form-row"):
                    yield Static("Grace Period:", classes="form-label")
                    yield Input(
                        str(self.app.config.preferences.default_grace_period) if hasattr(self, 'app') and self.app else "15",
                        id="grace-period",
                        validators=[Number(minimum=1, maximum=60)],
                        type="integer",
                    )
                    yield Static(" minutes", classes="form-label")

                with Horizontal(classes="form-row"):
                    yield Static("Health Timeout:", classes="form-label")
                    yield Input(
                        "300",
                        id="health-timeout",
                        validators=[Number(minimum=30, maximum=900)],
                        type="integer",
                    )
                    yield Static(" seconds", classes="form-label")

            # Progress section
            with Vertical(classes="form-section"):
                yield Static("[bold]Deploy Progress[/bold]", classes="form-section-title")
                yield Static("", id="countdown-display")
                yield Static("", id="deploy-status-text")
                yield Vertical(id="deploy-progress-list")

            # Controls
            with Horizontal(classes="form-row"):
                yield Button("Start Deploy", variant="success", id="btn-start-deploy")
                yield Button("Cancel Deploy", variant="error", id="btn-cancel-deploy", disabled=True)

        yield Footer()

    def on_mount(self) -> None:
        self._load_running_pods()

    def on_screen_resume(self) -> None:
        self._load_running_pods()

    @work
    async def _load_running_pods(self) -> None:
        try:
            pods = await self.app.api_client.list_pods()
            self._running_pods = [p for p in pods if p.desired_status == "RUNNING"]
            options = [
                (f"{p.name} ({p.gpu_display_name} x{p.gpu_count}) — {p.image_name[:30]}", p.id)
                for p in self._running_pods
            ]
            select = self.query_one("#target-pod-select", Select)
            select.set_options(options)
        except RunPodError as e:
            self.notify(f"Failed to load pods: {e}", severity="error", timeout=8)

    @on(Button.Pressed, "#btn-select-all")
    def _select_all(self, event: Button.Pressed) -> None:
        self.notify(f"{len(self._running_pods)} running pod(s) will be deployed", severity="information")
        self._deploy_all = True

    @on(Button.Pressed, "#btn-start-deploy")
    def _on_start_deploy(self, event: Button.Pressed) -> None:
        new_image = self.query_one("#new-image-input", Input).value.strip()
        if not new_image:
            self.notify("Please enter a new image name", severity="warning")
            return

        # Determine target pods
        target_pods = []
        if getattr(self, "_deploy_all", False):
            target_pods = self._running_pods
        else:
            select = self.query_one("#target-pod-select", Select)
            if select.value and select.value != Select.BLANK:
                pod = next((p for p in self._running_pods if p.id == select.value), None)
                if pod:
                    target_pods = [pod]

        if not target_pods:
            self.notify("No pods selected for deployment", severity="warning")
            return

        try:
            grace = int(self.query_one("#grace-period", Input).value or 15)
        except ValueError:
            grace = 15

        msg = (
            f"[bold]Start rolling deploy?[/bold]\n\n"
            f"Pods: {len(target_pods)}\n"
            f"New image: {new_image}\n"
            f"Grace period: {grace} min\n\n"
            f"This will create new pods, health check them, then terminate old pods."
        )

        def on_confirm(result: bool) -> None:
            if result:
                self._execute_deploy(target_pods, new_image)

        self.app.push_screen(ConfirmActionScreen(msg, "Start Deploy"), callback=on_confirm)

    @on(Button.Pressed, "#btn-cancel-deploy")
    def _on_cancel_deploy(self, event: Button.Pressed) -> None:
        self.app.pod_manager.cancel_deploy()
        self.notify("Cancelling deploy — rolling back...", severity="warning")

    @work(exclusive=True)
    async def _execute_deploy(self, target_pods: list[Pod], new_image: str) -> None:
        self._deploy_active = True
        btn_start = self.query_one("#btn-start-deploy", Button)
        btn_cancel = self.query_one("#btn-cancel-deploy", Button)
        btn_start.disabled = True
        btn_cancel.disabled = False

        progress_list = self.query_one("#deploy-progress-list", Vertical)
        status_text = self.query_one("#deploy-status-text", Static)
        countdown = self.query_one("#countdown-display", Static)

        # Initialize progress entries
        pod_status_widgets: dict[str, Static] = {}
        for pod in target_pods:
            label = f"  {pod.name[:20]}  [{pod.id[:8]}]  —  PENDING"
            widget = Static(label, classes="deploy-pod-entry")
            pod_status_widgets[pod.id] = widget
            progress_list.mount(widget)

        try:
            grace = int(self.query_one("#grace-period", Input).value or 15)
        except ValueError:
            grace = 15
        try:
            health_timeout = int(self.query_one("#health-timeout", Input).value or 300)
        except ValueError:
            health_timeout = 300

        def on_state_change(old_pod_id: str, state: str, new_pod_id: str) -> None:
            state_colors = {
                "CREATE_NEW": "[yellow]CREATING[/yellow]",
                "HEALTH_CHECK": "[yellow]HEALTH CHECK[/yellow]",
                "DRAINING": "[cyan]DRAINING[/cyan]",
                "TERMINATE_OLD": "[yellow]TERMINATING OLD[/yellow]",
                "COMPLETED": "[green]COMPLETED[/green]",
                "FAILED": "[red]FAILED[/red]",
                "ROLLING_BACK": "[red]ROLLING BACK[/red]",
            }
            styled = state_colors.get(state, state)
            if old_pod_id in pod_status_widgets:
                widget = pod_status_widgets[old_pod_id]
                new_info = f"  → {new_pod_id[:8]}" if new_pod_id else ""
                widget.update(f"  {old_pod_id[:8]}{new_info}  —  {styled}")
            status_text.update(f"[bold]Current: {state}[/bold]")

        def on_progress(completed: int, total: int) -> None:
            status_text.update(
                f"[bold]Progress: {completed}/{total} pods deployed[/bold]"
            )

        def on_countdown(remaining: int) -> None:
            mins, secs = divmod(remaining, 60)
            countdown.update(f"  Grace period: {mins:02d}:{secs:02d} remaining")

        try:
            record = await self.app.pod_manager.rolling_deploy(
                target_pods=target_pods,
                new_image=new_image,
                grace_period_minutes=grace,
                health_check_timeout=health_timeout,
                on_state_change=on_state_change,
                on_progress=on_progress,
                on_countdown=on_countdown,
            )

            if record.status == "completed":
                self.notify(
                    f"Deploy completed! {record.pod_count} pods updated in {record.duration_seconds:.0f}s",
                    severity="information", timeout=15,
                )
                status_text.update("[bold green]Deploy completed successfully![/bold green]")
            elif record.status == "rolled_back":
                self.notify(
                    f"Deploy rolled back: {record.error}",
                    severity="warning", timeout=15,
                )
                status_text.update(f"[bold yellow]Rolled back: {record.error}[/bold yellow]")
            else:
                self.notify(
                    f"Deploy failed: {record.error}",
                    severity="error", timeout=15,
                )
                status_text.update(f"[bold red]Failed: {record.error}[/bold red]")

        except Exception as e:
            self.notify(f"Deploy error: {e}", severity="error", timeout=15)
            status_text.update(f"[bold red]Error: {e}[/bold red]")
        finally:
            self._deploy_active = False
            self._deploy_all = False
            btn_start.disabled = False
            btn_cancel.disabled = True
            countdown.update("")
