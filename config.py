"""Configuration and preferences management."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BASE_DIR = Path(__file__).parent


@dataclass
class TemplatePrefs:
    gpu_preferences: list[str] = field(default_factory=list)  # Ordered GPU IDs
    last_region: str = ""
    last_gpu_count: int = 1
    last_pod_count: int = 1
    last_cloud_type: str = "ALL"


@dataclass
class Preferences:
    refresh_interval: float = 12.0
    default_cloud_type: str = "ALL"
    default_volume_gb: int = 20
    default_container_disk_gb: int = 20
    default_ports: str = "8888/http"
    default_volume_mount_path: str = "/workspace"
    default_grace_period: int = 15  # minutes
    template_prefs: dict[str, TemplatePrefs] = field(default_factory=dict)
    scaling_presets: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> Preferences:
        path = path or BASE_DIR / "preferences.json"
        if not path.exists():
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return cls()
        prefs = cls(
            refresh_interval=data.get("refresh_interval", 12.0),
            default_cloud_type=data.get("default_cloud_type", "ALL"),
            default_volume_gb=data.get("default_volume_gb", 20),
            default_container_disk_gb=data.get("default_container_disk_gb", 20),
            default_ports=data.get("default_ports", "8888/http"),
            default_volume_mount_path=data.get("default_volume_mount_path", "/workspace"),
            default_grace_period=data.get("default_grace_period", 15),
            scaling_presets=data.get("scaling_presets", []),
        )
        for tid, tprefs in data.get("template_prefs", {}).items():
            prefs.template_prefs[tid] = TemplatePrefs(
                gpu_preferences=tprefs.get("gpu_preferences", []),
                last_region=tprefs.get("last_region", ""),
                last_gpu_count=tprefs.get("last_gpu_count", 1),
                last_pod_count=tprefs.get("last_pod_count", 1),
                last_cloud_type=tprefs.get("last_cloud_type", "ALL"),
            )
        return prefs

    def save(self, path: Path | None = None) -> None:
        path = path or BASE_DIR / "preferences.json"
        data: dict[str, Any] = {
            "refresh_interval": self.refresh_interval,
            "default_cloud_type": self.default_cloud_type,
            "default_volume_gb": self.default_volume_gb,
            "default_container_disk_gb": self.default_container_disk_gb,
            "default_ports": self.default_ports,
            "default_volume_mount_path": self.default_volume_mount_path,
            "default_grace_period": self.default_grace_period,
            "scaling_presets": self.scaling_presets,
            "template_prefs": {},
        }
        for tid, tprefs in self.template_prefs.items():
            data["template_prefs"][tid] = {
                "gpu_preferences": tprefs.gpu_preferences,
                "last_region": tprefs.last_region,
                "last_gpu_count": tprefs.last_gpu_count,
                "last_pod_count": tprefs.last_pod_count,
                "last_cloud_type": tprefs.last_cloud_type,
            }
        # Atomic write
        dir_path = path.parent
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_path, suffix=".tmp", delete=False
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, path)

    def get_template_prefs(self, template_id: str) -> TemplatePrefs:
        if template_id not in self.template_prefs:
            self.template_prefs[template_id] = TemplatePrefs()
        return self.template_prefs[template_id]

    def update_template_prefs(
        self, template_id: str, gpu_type_id: str = "", region: str = "",
        gpu_count: int = 0, pod_count: int = 0, cloud_type: str = "",
    ) -> None:
        tp = self.get_template_prefs(template_id)
        if gpu_type_id:
            if gpu_type_id in tp.gpu_preferences:
                tp.gpu_preferences.remove(gpu_type_id)
            tp.gpu_preferences.insert(0, gpu_type_id)
        if region:
            tp.last_region = region
        if gpu_count > 0:
            tp.last_gpu_count = gpu_count
        if pod_count > 0:
            tp.last_pod_count = pod_count
        if cloud_type:
            tp.last_cloud_type = cloud_type


@dataclass
class AppConfig:
    api_key: str
    graphql_url: str = "https://api.runpod.io/graphql"
    rest_url: str = "https://rest.runpod.io/v1"
    preferences: Preferences = field(default_factory=Preferences)

    @classmethod
    def load(cls) -> AppConfig:
        load_dotenv(BASE_DIR / ".env")
        api_key = os.getenv("RUNPOD_API_KEY", "")
        if not api_key:
            raise ValueError(
                "RUNPOD_API_KEY not found. Set it in your .env file:\n"
                "  echo 'RUNPOD_API_KEY=your_key_here' > .env"
            )
        preferences = Preferences.load()
        return cls(api_key=api_key, preferences=preferences)
