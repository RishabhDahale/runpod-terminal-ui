"""Template selector widget."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Select, Static

from models import Template
from runpod_client import RunPodError


class TemplatePicker(Widget):

    _templates: list[Template] = []

    class TemplateSelected(Message):
        """Posted when a template is selected — carries full template data."""
        def __init__(self, template: Template | None, image_name: str) -> None:
            super().__init__()
            self.template = template
            self.image_name = image_name

    def compose(self) -> ComposeResult:
        with Vertical(id="template-picker"):
            yield Static("[bold]Deploy from Template or Custom Image[/bold]", classes="form-label")
            yield Select([], id="template-select", prompt="Choose a template...")
            yield Static("", id="template-info")
            yield Static("[dim]Or enter a custom image:[/dim]")
            yield Input(placeholder="e.g., runpod/pytorch:2.1.0", id="image-input")

    def on_mount(self) -> None:
        self._load_templates()

    @work
    async def _load_templates(self) -> None:
        try:
            self._templates = await self.app.api_client.list_templates()
            options = [
                (f"{t.name}", t.id)
                for t in self._templates
                if not t.is_serverless
            ]
            select = self.query_one("#template-select", Select)
            select.set_options(options)
        except RunPodError as e:
            self.app.notify(f"Failed to load templates: {e}", severity="warning", timeout=8)

    @on(Select.Changed, "#template-select")
    def _on_template_selected(self, event: Select.Changed) -> None:
        if event.value and event.value != Select.BLANK:
            template = next((t for t in self._templates if t.id == event.value), None)
            if template:
                image_input = self.query_one("#image-input", Input)
                image_input.value = template.image_name
                # Show template details
                info = self.query_one("#template-info", Static)
                env_count = len(template.env) if template.env else 0
                info.update(
                    f"  [bold green]{template.name}[/bold green] | "
                    f"Image: [cyan]{template.image_name[:50]}[/cyan] | "
                    f"Disk: {template.container_disk_in_gb}GB | "
                    f"Volume: {template.volume_in_gb}GB | "
                    f"Env vars: {env_count}"
                )
                self.post_message(self.TemplateSelected(template, template.image_name))

    @on(Input.Changed, "#image-input")
    def _on_image_changed(self, event: Input.Changed) -> None:
        if event.value:
            # Clear template info if user types a custom image
            select = self.query_one("#template-select", Select)
            if select.value == Select.BLANK:
                info = self.query_one("#template-info", Static)
                info.update("")
            self.post_message(self.TemplateSelected(None, event.value))

    @property
    def templates(self) -> list[Template]:
        return self._templates

    @property
    def selected_image(self) -> str:
        return self.query_one("#image-input", Input).value

    @property
    def selected_template(self) -> Template | None:
        select = self.query_one("#template-select", Select)
        if select.value and select.value != Select.BLANK:
            return next((t for t in self._templates if t.id == select.value), None)
        return None
