"""Main Textual application for RunPod Dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Static
from textual.containers import Vertical, Horizontal

from config import AppConfig
from runpod_client import RunPodClient
from pod_manager import PodManager


class HelpScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close"), ("question_mark", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-container"):
            yield Static("[bold]RunPod Dashboard — Keybindings[/bold]")
            yield Static("")
            yield Static("[bold cyan]d[/]  Dashboard — live pod status")
            yield Static("[bold cyan]s[/]  Deploy — create pods from template")
            yield Static("[bold cyan]x[/]  Stop — select and stop instances")
            yield Static("[bold cyan]r[/]  Rolling Deploy — update pod images")
            yield Static("[bold cyan]l[/]  Logs — deployment history")
            yield Static("[bold cyan]b[/]  Bulk — bulk actions on pods")
            yield Static("")
            yield Static("[bold cyan]F5[/] Force refresh (on dashboard)")
            yield Static("[bold cyan]?[/]  This help screen")
            yield Static("[bold cyan]q[/]  Quit")
            yield Static("")
            yield Static("[dim]Dashboard quick actions:[/dim]")
            yield Static("[bold cyan]x[/]  Stop focused pod")
            yield Static("[bold cyan]i[/]  Show pod info")
            yield Static("")
            yield Static("[dim]Press Escape or ? to close[/dim]")


class ConfirmQuitScreen(ModalScreen[bool]):
    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static("[bold]Quit RunPod Dashboard?[/bold]")
            with Horizontal(id="confirm-buttons"):
                yield Button("Quit", variant="error", id="confirm")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)


class ConfirmActionScreen(ModalScreen[bool]):
    def __init__(self, message: str, action_label: str = "Confirm") -> None:
        super().__init__()
        self._message = message
        self._action_label = action_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self._message)
            with Horizontal(id="confirm-buttons"):
                yield Button(self._action_label, variant="error", id="confirm")
                yield Button("Cancel", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class RunPodDashboardApp(App):
    TITLE = "RunPod Dashboard"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("d", "switch_to('dashboard')", "Dashboard", priority=True),
        Binding("s", "switch_to('scale')", "Deploy", priority=True),
        Binding("x", "switch_to('stop')", "Stop", priority=True),
        Binding("r", "switch_to('deploy')", "Rolling Deploy", priority=True),
        Binding("l", "switch_to('logs')", "Logs", priority=True),
        Binding("b", "switch_to('bulk')", "Bulk", priority=True),
        Binding("question_mark", "show_help", "Help", priority=True),
        Binding("q", "request_quit", "Quit", priority=True),
    ]

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.api_client = RunPodClient(
            config.api_key,
            graphql_url=config.graphql_url,
            rest_url=config.rest_url,
        )
        self.pod_manager = PodManager(
            self.api_client,
            history_path=Path(__file__).parent.parent / "deploy_history.json",
        )
        self._screens_installed = False

    def on_mount(self) -> None:
        from ui.screens.dashboard import DashboardScreen
        from ui.screens.scale import ScaleScreen
        from ui.screens.stop import StopScreen
        from ui.screens.deploy import DeployScreen
        from ui.screens.logs import LogsScreen
        from ui.screens.bulk import BulkScreen

        self.install_screen(DashboardScreen(), name="dashboard")
        self.install_screen(ScaleScreen(), name="scale")
        self.install_screen(StopScreen(), name="stop")
        self.install_screen(DeployScreen(), name="deploy")
        self.install_screen(LogsScreen(), name="logs")
        self.install_screen(BulkScreen(), name="bulk")
        self._screens_installed = True
        self._current_screen = "dashboard"
        self.push_screen("dashboard")

        # Truncate history if needed
        self.pod_manager.truncate_history()

    async def on_unmount(self) -> None:
        await self.api_client.close()

    def action_switch_to(self, screen_name: str) -> None:
        if not self._screens_installed:
            return
        if hasattr(self, "_current_screen") and self._current_screen == screen_name:
            return
        self._current_screen = screen_name
        self.switch_screen(screen_name)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_request_quit(self) -> None:
        def on_quit(result: bool) -> None:
            if result:
                self.exit()
        self.push_screen(ConfirmQuitScreen(), callback=on_quit)
