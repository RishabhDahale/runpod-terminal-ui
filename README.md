# RunPod Pod Scaling Dashboard

A terminal UI (TUI) application for managing and scaling GPU pods on RunPod. Built with [Textual](https://github.com/Textualize/textual).

## Features

- **Deploy Wizard** — Step-by-step flow: template → naming → GPU selection (with real-time availability) → pod count → confirmation → execution with per-pod progress
- **Stop Instances** — Filterable table with multi-select, keyboard-driven batch stop/terminate with cost savings display
- **Live Dashboard** — Auto-refreshing pod table with GPU utilization, cost, uptime, and template summary
- **Rolling Deploy** — Update pod images with zero-downtime rolling deployments
- **Bulk Actions** — Batch operations on pods (stop, terminate, restart)
- **Deployment History** — Searchable log of all deploy actions
- **Sortable Tables** — Click column headers to sort; sort indicators (▲/▼) shown on active column
- **GPU Availability** — Real-time stock status, available count, color-coded (green/yellow/red), unavailable GPUs pinned to bottom

## Setup

```bash
# Clone the repo
git clone <repo-url>
cd runpod-dashboard

# Install dependencies
pip install -r requirements.txt

# Configure API key
cp .env.example .env
# Edit .env and add your RunPod API key
```

## Usage

```bash
python main.py
```

## Keybindings

| Key | Action |
|-----|--------|
| `d` | Dashboard — live pod status |
| `s` | Deploy — create pods from template |
| `x` | Stop — select and stop instances |
| `r` | Rolling Deploy — update pod images |
| `l` | Logs — deployment history |
| `b` | Bulk — bulk actions on pods |
| `?` | Help screen |
| `q` | Quit |
| `Esc` | Go back / return to dashboard |

### Stop Screen

| Key | Action |
|-----|--------|
| `Space` | Toggle pod selection |
| `a` | Select all |
| `n` | Deselect all |
| `Enter` | Stop selected pods |
| `t` | Terminate selected pods |

### Dashboard

| Key | Action |
|-----|--------|
| `F5` | Force refresh |
| `x` | Stop focused pod |
| `i` | Show pod info |

## Requirements

- Python 3.10+
- `textual >= 0.85.0`
- `httpx >= 0.27.0`
- `python-dotenv >= 1.0.0`
- A [RunPod](https://www.runpod.io/) API key
