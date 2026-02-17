"""Deploy screen — step-by-step wizard for creating pods from templates."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.validation import Number
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Footer,
    Header,
    Input,
    ProgressBar,
    Select,
    Static,
)

from rich.text import Text

from models import CloudType, GpuType, Pod, Template
from runpod_client import RunPodError
from ui.widgets.cost_display import CostDisplay

STEP_IDS = [
    "step-template",
    "step-naming",
    "step-gpu",
    "step-count",
    "step-confirm",
    "step-execute",
]
STEP_NAMES = ["Template", "Naming", "GPU", "Count", "Confirm", "Deploy"]

# GPU table column definitions: (label, key)
GPU_COLS = [
    ("GPU Type", "gpu_type"),
    ("Memory", "memory"),
    ("Available", "available"),
    ("Stock", "stock"),
    ("$/hr", "price"),
    ("$/hr (Secure)", "price_secure"),
]

# Sortable GPU columns → default descending on first click
GPU_SORTABLE = {
    "memory": True,      # most memory first
    "available": True,   # most available first
    "price": False,      # cheapest first
}


class ScaleScreen(Screen):
    """Step-by-step wizard for deploying pods from templates."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._step = 0
        self._templates: list[Template] = []
        self._gpu_types: list[GpuType] = []
        self._selected_template: Template | None = None
        self._selected_gpu: GpuType | None = None
        self._existing_pods: list[Pod] = []
        self._data_loaded = False
        self._gpu_sort_col: str = "available"
        self._gpu_sort_reverse: bool = True  # most available first

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="step-indicator")

        with ContentSwitcher(id="wizard", initial="step-template"):
            # Step 1: Template Selection
            with VerticalScroll(id="step-template"):
                yield Static(
                    "[bold cyan]Step 1 of 5 — Select Template[/bold cyan]\n"
                    "[dim]Arrow keys to navigate, Enter to select.[/dim]",
                    id="step-1-header",
                )
                yield DataTable(id="template-table", cursor_type="row")

            # Step 2: Instance Naming
            with VerticalScroll(id="step-naming"):
                yield Static(
                    "[bold cyan]Step 2 of 5 — Instance Name[/bold cyan]\n"
                    "[dim]Set a name prefix for your pod(s).[/dim]",
                    id="step-2-header",
                )
                yield Static("", id="naming-template-info")
                with Horizontal(classes="form-row"):
                    yield Static("Name Prefix:", classes="form-label")
                    yield Input(id="name-prefix", placeholder="e.g., sd-worker-20260212")
                yield Static("", id="naming-preview")

            # Step 3: GPU Selection
            with VerticalScroll(id="step-gpu"):
                yield Static(
                    "[bold cyan]Step 3 of 5 — Select GPU[/bold cyan]\n"
                    "[dim]Arrow keys to navigate, Enter to select. Click column header to sort.[/dim]",
                    id="step-3-header",
                )
                yield Static("", id="gpu-preferred-banner")
                yield DataTable(id="gpu-table", cursor_type="row")

            # Step 4: Pod Count & Config
            with VerticalScroll(id="step-count"):
                yield Static(
                    "[bold cyan]Step 4 of 5 — Pod Count & Options[/bold cyan]",
                    id="step-4-header",
                )
                with Horizontal(classes="form-row"):
                    yield Static("Pod Count:", classes="form-label")
                    yield Input(
                        "1",
                        id="pod-count",
                        validators=[Number(minimum=1, maximum=50)],
                        type="integer",
                    )
                with Horizontal(classes="form-row"):
                    yield Static("GPUs per Pod:", classes="form-label")
                    yield Input(
                        "1",
                        id="gpu-count",
                        validators=[Number(minimum=1, maximum=8)],
                        type="integer",
                    )
                with Horizontal(classes="form-row"):
                    yield Static("Cloud Type:", classes="form-label")
                    yield Select(
                        [(ct.value, ct.value) for ct in CloudType],
                        id="cloud-select",
                        value="ALL",
                    )
                with Horizontal(classes="form-row"):
                    yield Static("Volume (GB):", classes="form-label")
                    yield Input(
                        "20",
                        id="volume-gb",
                        validators=[Number(minimum=0, maximum=1000)],
                        type="integer",
                    )
                with Horizontal(classes="form-row"):
                    yield Static("Disk (GB):", classes="form-label")
                    yield Input(
                        "20",
                        id="disk-gb",
                        validators=[Number(minimum=1, maximum=1000)],
                        type="integer",
                    )
                with Horizontal(classes="form-row"):
                    yield Static("Ports:", classes="form-label")
                    yield Input(
                        "8888/http",
                        id="ports-input",
                        placeholder="e.g., 8888/http,22/tcp",
                    )
                yield CostDisplay(id="cost-display")

            # Step 5: Confirmation
            with VerticalScroll(id="step-confirm"):
                yield Static(
                    "[bold cyan]Step 5 of 5 — Confirm Deployment[/bold cyan]",
                    id="step-5-header",
                )
                yield Static("", id="confirm-summary")

            # Step 6: Execution
            with VerticalScroll(id="step-execute"):
                yield Static(
                    "[bold cyan]Deploying Pods...[/bold cyan]",
                    id="step-6-header",
                )
                yield ProgressBar(id="deploy-progress", total=1, show_eta=False)
                yield Static("", id="execution-log")

        with Horizontal(id="wizard-nav"):
            yield Button("Back", variant="default", id="btn-back")
            yield Button("Next", variant="primary", id="btn-next")

        yield Footer()

    def on_mount(self) -> None:
        self._update_step_ui()
        self._load_data()

    def on_screen_resume(self) -> None:
        if not self._data_loaded:
            self._load_data()

    @work
    async def _load_data(self) -> None:
        try:
            self._gpu_types = await self.app.api_client.list_gpu_types()
        except RunPodError as e:
            self.notify(f"Failed to load GPU types: {e}", severity="error")

        try:
            self._templates = await self.app.api_client.list_templates()
            self._populate_template_table()
        except RunPodError as e:
            self.notify(f"Failed to load templates: {e}", severity="error")

        try:
            self._existing_pods = await self.app.api_client.list_pods()
        except RunPodError:
            pass

        self._data_loaded = True

    # --- Template Table ---

    def _populate_template_table(self) -> None:
        table = self.query_one("#template-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Template Name", "Image", "Disk", "Volume", "Preferred GPU")

        prefs = self.app.config.preferences
        for t in self._templates:
            if t.is_serverless:
                continue
            tpref = prefs.template_prefs.get(t.id)
            gpu_pref = ""
            if tpref and tpref.gpu_preferences:
                gpu_id = tpref.gpu_preferences[0]
                gpu = next((g for g in self._gpu_types if g.id == gpu_id), None)
                gpu_pref = gpu.display_name if gpu else gpu_id
            table.add_row(
                t.name,
                t.image_name[:40] if t.image_name else "",
                f"{t.container_disk_in_gb}GB",
                f"{t.volume_in_gb}GB",
                gpu_pref or "—",
                key=t.id,
            )

    # --- GPU Table ---

    def _populate_gpu_table(self) -> None:
        table = self.query_one("#gpu-table", DataTable)
        table.clear(columns=True)

        # Add columns with sort indicators
        for label, key in GPU_COLS:
            col_label = label
            if key in GPU_SORTABLE and key == self._gpu_sort_col:
                col_label = f"{label} {'▼' if self._gpu_sort_reverse else '▲'}"
            table.add_column(col_label, key=key)

        # Partition: available first (sorted), unavailable last (always by name)
        gpu_list = [g for g in self._gpu_types if g.lowest_price > 0]
        available = [g for g in gpu_list if g.is_available]
        unavailable = [g for g in gpu_list if not g.is_available]

        # Sort available GPUs by selected column
        if self._gpu_sort_col == "memory":
            available.sort(key=lambda g: g.memory_gb, reverse=self._gpu_sort_reverse)
        elif self._gpu_sort_col == "available":
            available.sort(key=lambda g: g.available_count, reverse=self._gpu_sort_reverse)
        elif self._gpu_sort_col == "price":
            available.sort(
                key=lambda g: g.community_price or g.secure_price,
                reverse=self._gpu_sort_reverse,
            )

        # Unavailable always at bottom, sorted by name
        unavailable.sort(key=lambda g: g.display_name.lower())

        for g in available + unavailable:
            # Availability display
            avail = g.available_count
            if g.stock_status == "High":
                avail_text = Text(f"{avail}", style="bold green")
                stock_text = Text("High", style="bold green")
            elif g.stock_status == "Medium":
                avail_text = Text(f"{avail}", style="bold yellow")
                stock_text = Text("Medium", style="bold yellow")
            elif g.stock_status == "Low":
                avail_text = Text(f"{avail}", style="bold red")
                stock_text = Text("Low", style="bold red")
            else:
                avail_text = Text("0", style="dim")
                stock_text = Text("None", style="dim")

            # GPU name — dim if unavailable
            if g.is_available:
                name_text = g.display_name
            else:
                name_text = Text(g.display_name, style="dim")

            table.add_row(
                name_text,
                f"{g.memory_gb}GB",
                avail_text,
                stock_text,
                f"${g.community_price:.2f}" if g.community_price > 0 else "—",
                f"${g.secure_price:.2f}" if g.secure_price > 0 else "—",
                key=g.id,
            )

    # --- Step Navigation ---

    def _update_step_ui(self) -> None:
        parts = []
        for i, name in enumerate(STEP_NAMES):
            if i < self._step:
                parts.append(f"[green]{name}[/green]")
            elif i == self._step:
                parts.append(f"[bold cyan]> {name}[/bold cyan]")
            else:
                parts.append(f"[dim]{name}[/dim]")
        self.query_one("#step-indicator", Static).update("  ".join(parts))

        back_btn = self.query_one("#btn-back", Button)
        next_btn = self.query_one("#btn-next", Button)

        back_btn.display = 0 < self._step < 5

        if self._step >= 5:
            next_btn.display = False
        elif self._step == 4:
            next_btn.label = "Deploy"
            next_btn.variant = "success"
            next_btn.display = True
        else:
            next_btn.label = "Next"
            next_btn.variant = "primary"
            next_btn.display = True

    def _go_to_step(self, step: int) -> None:
        self._step = step
        self.query_one("#wizard", ContentSwitcher).current = STEP_IDS[step]
        self._update_step_ui()
        self._on_enter_step(step)

    def _on_enter_step(self, step: int) -> None:
        if step == 1:
            self._setup_naming_step()
        elif step == 2:
            self._populate_gpu_table()
            self._setup_gpu_step()
        elif step == 3:
            self._setup_count_step()
        elif step == 4:
            self._build_confirmation()
        elif step == 5:
            self._execute_deploy()

    def _reset_wizard(self) -> None:
        self._selected_template = None
        self._selected_gpu = None
        self.query_one("#name-prefix", Input).value = ""
        self.query_one("#pod-count", Input).value = "1"
        self.query_one("#gpu-count", Input).value = "1"
        self._go_to_step(0)
        self._load_data()

    # --- Step 1: Template Selection (handled by DataTable.RowSelected) ---

    # --- Step 2: Instance Naming ---

    def _setup_naming_step(self) -> None:
        t = self._selected_template
        if not t:
            return
        info = self.query_one("#naming-template-info", Static)
        info.update(
            f"\n  Template: [bold green]{t.name}[/bold green]\n"
            f"  Image:    [cyan]{t.image_name}[/cyan]\n"
        )
        prefix_input = self.query_one("#name-prefix", Input)
        if not prefix_input.value:
            ts = datetime.now().strftime("%Y%m%d")
            prefix_input.value = f"{t.name}-{ts}"

        # Set defaults from template config
        self.query_one("#volume-gb", Input).value = str(t.volume_in_gb)
        self.query_one("#disk-gb", Input).value = str(t.container_disk_in_gb)
        if t.ports:
            self.query_one("#ports-input", Input).value = t.ports

        self._update_naming_preview()

    def _update_naming_preview(self) -> None:
        prefix = self.query_one("#name-prefix", Input).value
        preview = self.query_one("#naming-preview", Static)
        if not prefix:
            preview.update("")
            return

        preview_text = f"\n  [dim]Pods will be named:[/dim]  [bold]{prefix}-1[/bold], [bold]{prefix}-2[/bold], ..."

        # Check for name conflicts with existing pods
        existing_names = {p.name for p in self._existing_pods}
        conflicts = [f"{prefix}-{i+1}" for i in range(3) if f"{prefix}-{i+1}" in existing_names]
        if conflicts:
            preview_text += (
                f"\n  [bold yellow]Warning: name conflict with existing pods: "
                f"{', '.join(conflicts)}[/bold yellow]"
            )

        preview.update(preview_text)

    # --- Step 3: GPU Selection ---

    def _setup_gpu_step(self) -> None:
        banner = self.query_one("#gpu-preferred-banner", Static)
        if not self._selected_template:
            banner.update("")
            return

        prefs = self.app.config.preferences
        tpref = prefs.template_prefs.get(self._selected_template.id)
        if not tpref or not tpref.gpu_preferences:
            banner.update("")
            return

        gpu_id = tpref.gpu_preferences[0]
        gpu = next((g for g in self._gpu_types if g.id == gpu_id), None)
        if not gpu:
            banner.update("")
            return

        if gpu.is_available:
            banner.update(
                f"  [bold green]Preferred GPU: {gpu.display_name} "
                f"(${gpu.lowest_price:.2f}/hr) — {gpu.available_count} available[/bold green]"
            )
        else:
            banner.update(
                f"  [bold red]Preferred GPU {gpu.display_name} is unavailable — "
                f"select an alternative below[/bold red]"
            )

        # Pre-select the preferred GPU in the table
        table = self.query_one("#gpu-table", DataTable)
        keys = list(table.rows.keys())
        for i, key in enumerate(keys):
            key_str = str(key.value) if hasattr(key, "value") else str(key)
            if key_str == gpu_id:
                table.move_cursor(row=i)
                break

    # --- Step 4: Pod Count & Config ---

    def _setup_count_step(self) -> None:
        if self._selected_template:
            prefs = self.app.config.preferences
            tpref = prefs.template_prefs.get(self._selected_template.id)
            if tpref:
                self.query_one("#pod-count", Input).value = str(tpref.last_pod_count)
                self.query_one("#gpu-count", Input).value = str(tpref.last_gpu_count)
                self.query_one("#cloud-select", Select).value = tpref.last_cloud_type
        self._recalculate_cost()

    def _recalculate_cost(self) -> None:
        cost = self.query_one("#cost-display", CostDisplay)
        if not self._selected_gpu:
            cost.gpu_price = 0.0
            return

        cloud_type = str(self.query_one("#cloud-select", Select).value or "ALL")
        cost.update_from_gpu(self._selected_gpu, cloud_type)

        try:
            cost.gpu_count = int(self.query_one("#gpu-count", Input).value or 1)
        except ValueError:
            cost.gpu_count = 1
        try:
            cost.pod_count = int(self.query_one("#pod-count", Input).value or 1)
        except ValueError:
            cost.pod_count = 1

    # --- Step 5: Confirmation ---

    def _build_confirmation(self) -> None:
        t = self._selected_template
        g = self._selected_gpu
        prefix = self.query_one("#name-prefix", Input).value

        try:
            pod_count = int(self.query_one("#pod-count", Input).value or 1)
        except ValueError:
            pod_count = 1
        try:
            gpu_count = int(self.query_one("#gpu-count", Input).value or 1)
        except ValueError:
            gpu_count = 1

        cloud_type = str(self.query_one("#cloud-select", Select).value or "ALL")
        volume_gb = self.query_one("#volume-gb", Input).value
        disk_gb = self.query_one("#disk-gb", Input).value
        ports = self.query_one("#ports-input", Input).value

        if g:
            if cloud_type == "COMMUNITY":
                price = g.community_price
            elif cloud_type == "SECURE":
                price = g.secure_price
            else:
                price = g.lowest_price
            total_hr = price * gpu_count * pod_count
        else:
            price = 0.0
            total_hr = 0.0

        template_name = t.name if t else "Custom"
        gpu_name = g.display_name if g else "Unknown"
        name_preview = f"{prefix}-{{1..{pod_count}}}" if pod_count > 1 else f"{prefix}-1"

        summary = (
            f"\n"
            f"  [bold]Template:[/bold]   {template_name}\n"
            f"  [bold]Name:[/bold]       {name_preview}\n"
            f"  [bold]GPU:[/bold]        {gpu_name} x {gpu_count} per pod\n"
            f"  [bold]Pods:[/bold]       {pod_count}\n"
            f"  [bold]Cloud:[/bold]      {cloud_type}\n"
            f"  [bold]Volume:[/bold]     {volume_gb}GB\n"
            f"  [bold]Disk:[/bold]       {disk_gb}GB\n"
            f"  [bold]Ports:[/bold]      {ports}\n"
            f"\n"
            f"  [bold]Est. Cost:[/bold]  [bold green]${total_hr:.2f}/hr[/bold green] "
            f"(${price:.2f} x {gpu_count} x {pod_count})\n"
            f"              ~${total_hr * 24:.2f}/day\n"
            f"\n"
            f"  [dim]Press Deploy to confirm  |  Esc to go back[/dim]"
        )
        self.query_one("#confirm-summary", Static).update(summary)

    # --- Event Handlers ---

    def action_go_back(self) -> None:
        if self._step > 0:
            self._go_to_step(self._step - 1)
        else:
            self.app.action_switch_to("dashboard")

    @on(Button.Pressed, "#btn-next")
    def _on_next_pressed(self, event: Button.Pressed) -> None:
        self._advance_step()

    @on(Button.Pressed, "#btn-back")
    def _on_back_pressed(self, event: Button.Pressed) -> None:
        self.action_go_back()

    @on(DataTable.RowSelected, "#template-table")
    def _on_template_selected(self, event: DataTable.RowSelected) -> None:
        key_str = str(event.row_key.value) if hasattr(event.row_key, "value") else str(event.row_key)
        self._selected_template = next(
            (t for t in self._templates if t.id == key_str), None
        )
        if self._selected_template:
            self._go_to_step(1)

    @on(DataTable.HeaderSelected, "#gpu-table")
    def _on_gpu_header_selected(self, event: DataTable.HeaderSelected) -> None:
        col_key = (
            str(event.column_key.value)
            if hasattr(event.column_key, "value")
            else str(event.column_key)
        )
        if col_key not in GPU_SORTABLE:
            return
        if col_key == self._gpu_sort_col:
            self._gpu_sort_reverse = not self._gpu_sort_reverse
        else:
            self._gpu_sort_col = col_key
            self._gpu_sort_reverse = GPU_SORTABLE[col_key]
        self._populate_gpu_table()

    @on(DataTable.RowSelected, "#gpu-table")
    def _on_gpu_selected(self, event: DataTable.RowSelected) -> None:
        key_str = str(event.row_key.value) if hasattr(event.row_key, "value") else str(event.row_key)
        gpu = next((g for g in self._gpu_types if g.id == key_str), None)
        if not gpu:
            return
        if not gpu.is_available:
            self.notify(
                f"{gpu.display_name} has 0 available — pick another GPU",
                severity="error",
            )
            return
        self._selected_gpu = gpu
        self._go_to_step(3)

    def _advance_step(self) -> None:
        if self._step == 0:
            # Select template from cursor position
            table = self.query_one("#template-table", DataTable)
            if table.row_count == 0:
                self.notify("No templates loaded", severity="warning")
                return
            if table.cursor_row is None:
                self.notify("Select a template first", severity="warning")
                return
            keys = list(table.rows.keys())
            if table.cursor_row < len(keys):
                key = keys[table.cursor_row]
                key_str = str(key.value) if hasattr(key, "value") else str(key)
                self._selected_template = next(
                    (t for t in self._templates if t.id == key_str), None
                )
            if not self._selected_template:
                self.notify("Select a template first", severity="warning")
                return
            self._go_to_step(1)

        elif self._step == 1:
            prefix = self.query_one("#name-prefix", Input).value.strip()
            if not prefix:
                self.notify("Enter a name prefix", severity="warning")
                return
            self._go_to_step(2)

        elif self._step == 2:
            table = self.query_one("#gpu-table", DataTable)
            if table.row_count == 0:
                self.notify("No GPUs loaded", severity="warning")
                return
            if table.cursor_row is None:
                self.notify("Select a GPU type", severity="warning")
                return
            keys = list(table.rows.keys())
            if table.cursor_row < len(keys):
                key = keys[table.cursor_row]
                key_str = str(key.value) if hasattr(key, "value") else str(key)
                self._selected_gpu = next(
                    (g for g in self._gpu_types if g.id == key_str), None
                )
            if not self._selected_gpu:
                self.notify("Select a GPU type", severity="warning")
                return
            if not self._selected_gpu.is_available:
                self.notify(
                    f"{self._selected_gpu.display_name} has 0 available — pick another GPU",
                    severity="error",
                )
                return
            self._go_to_step(3)

        elif self._step == 3:
            try:
                count = int(self.query_one("#pod-count", Input).value or 1)
                if count < 1:
                    raise ValueError
            except ValueError:
                self.notify("Enter a valid pod count (1-50)", severity="warning")
                return
            self._go_to_step(4)

        elif self._step == 4:
            # Start deployment
            self._go_to_step(5)

    @on(Input.Changed, "#name-prefix")
    def _on_name_changed(self, event: Input.Changed) -> None:
        if self._step == 1:
            self._update_naming_preview()

    @on(Input.Changed, "#pod-count")
    def _on_count_changed(self, event: Input.Changed) -> None:
        if self._step == 3:
            self._recalculate_cost()

    @on(Input.Changed, "#gpu-count")
    def _on_gpu_count_changed(self, event: Input.Changed) -> None:
        if self._step == 3:
            self._recalculate_cost()

    @on(Select.Changed, "#cloud-select")
    def _on_cloud_changed(self, event: Select.Changed) -> None:
        if self._step == 3:
            self._recalculate_cost()

    # --- Step 6: Execution ---

    @work(exclusive=True)
    async def _execute_deploy(self) -> None:
        t = self._selected_template
        g = self._selected_gpu
        if not t or not g:
            self.notify("Missing template or GPU selection", severity="error")
            return

        prefix = self.query_one("#name-prefix", Input).value.strip()
        try:
            pod_count = int(self.query_one("#pod-count", Input).value or 1)
            gpu_count = int(self.query_one("#gpu-count", Input).value or 1)
            volume_gb = int(self.query_one("#volume-gb", Input).value or 20)
            disk_gb = int(self.query_one("#disk-gb", Input).value or 20)
        except ValueError:
            self.notify("Invalid numeric input", severity="error")
            return

        cloud_type = str(self.query_one("#cloud-select", Select).value or "ALL")
        ports = self.query_one("#ports-input", Input).value or "8888/http"

        env: list[str] | None = None
        if t.env:
            env = [f"{k}={v}" for k, v in t.env.items()]

        progress = self.query_one("#deploy-progress", ProgressBar)
        progress.total = pod_count
        progress.progress = 0

        log = self.query_one("#execution-log", Static)
        status_lines = [
            f"  {prefix}-{i+1}  [dim]Queued...[/dim]" for i in range(pod_count)
        ]
        log.update("\n".join(status_lines))

        def on_progress(created: int, total: int) -> None:
            progress.progress = created
            if 0 < created <= len(status_lines):
                status_lines[created - 1] = (
                    f"  {prefix}-{created}  [bold green]Running[/bold green]"
                )
                log.update("\n".join(status_lines))

        try:
            record = await self.app.pod_manager.scale_up(
                count=pod_count,
                name_prefix=prefix,
                image_name=t.image_name,
                gpu_type_id=g.id,
                gpu_count=gpu_count,
                cloud_type=cloud_type,
                volume_in_gb=volume_gb,
                container_disk_in_gb=disk_gb,
                ports=ports,
                template_id=t.id,
                env=env,
                on_progress=on_progress,
            )

            # Save preferences
            self.app.config.preferences.update_template_prefs(
                t.id,
                gpu_type_id=g.id,
                gpu_count=gpu_count,
                pod_count=pod_count,
                cloud_type=cloud_type,
            )
            self.app.config.preferences.save()

            if record.error:
                status_lines.append(
                    f"\n[bold yellow]Completed with errors: {record.error}[/bold yellow]"
                )
                self.notify(
                    f"Deployed with errors: {record.error}",
                    severity="warning",
                    timeout=10,
                )
            else:
                status_lines.append(
                    f"\n[bold green]All {record.pod_count} pod(s) created successfully![/bold green]"
                )
                self.notify(
                    f"Created {record.pod_count} pods",
                    severity="information",
                )

            status_lines.append(
                "\n[dim]Press 'd' to go to dashboard  |  Esc to deploy more[/dim]"
            )
            log.update("\n".join(status_lines))

        except RunPodError as e:
            status_lines.append(f"\n[bold red]Deploy failed: {e}[/bold red]")
            log.update("\n".join(status_lines))
            self.notify(f"Deploy failed: {e}", severity="error", timeout=10)
