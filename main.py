#!/usr/bin/env python3
"""RunPod Pod Scaling Dashboard — Entry Point."""

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from config import AppConfig
from ui.app import RunPodDashboardApp


def main() -> None:
    try:
        config = AppConfig.load()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    app = RunPodDashboardApp(config)
    app.run()


if __name__ == "__main__":
    main()
