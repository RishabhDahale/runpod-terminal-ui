"""RunPod API client — GraphQL for pods/GPUs, REST for templates."""

from __future__ import annotations

from typing import Any

import httpx

from models import (
    GpuMetrics,
    GpuType,
    Pod,
    PodRuntime,
    PortMapping,
    Template,
)


# --- Exceptions ---


class RunPodError(Exception):
    """Base exception for all RunPod API errors."""


class RunPodConnectionError(RunPodError):
    """Network connectivity issue."""


class RunPodTimeoutError(RunPodError):
    """Request timed out."""


class RunPodAuthError(RunPodError):
    """Authentication failure (invalid API key)."""


class RunPodAPIError(RunPodError):
    """GraphQL or REST API returned an error."""

    def __init__(self, errors: list[dict] | str):
        if isinstance(errors, list):
            messages = [e.get("message", str(e)) for e in errors]
            self.errors = errors
            super().__init__("; ".join(messages))
        else:
            self.errors = []
            super().__init__(errors)


# --- GraphQL Query Constants ---


LIST_PODS_QUERY = """
query {
  myself {
    pods {
      id
      name
      imageName
      desiredStatus
      costPerHr
      gpuCount
      volumeInGb
      containerDiskInGb
      volumeMountPath
      templateId
      machineId
      env
      ports
      runtime {
        uptimeInSeconds
        ports {
          ip
          isIpPublic
          privatePort
          publicPort
          type
        }
        gpus {
          gpuUtilPercent
          memoryUtilPercent
        }
      }
      machine {
        gpuDisplayName
      }
    }
  }
}
"""

GET_POD_QUERY = """
query Pod($input: PodFilter!) {
  pod(input: $input) {
    id
    name
    imageName
    desiredStatus
    costPerHr
    gpuCount
    volumeInGb
    containerDiskInGb
    volumeMountPath
    templateId
    machineId
    env
    ports
    runtime {
      uptimeInSeconds
      ports {
        ip
        isIpPublic
        privatePort
        publicPort
        type
      }
      gpus {
        gpuUtilPercent
        memoryUtilPercent
      }
    }
    machine {
      gpuDisplayName
    }
  }
}
"""

CREATE_POD_MUTATION = """
mutation CreatePod($input: PodFindAndDeployOnDemandInput!) {
  podFindAndDeployOnDemand(input: $input) {
    id
    name
    imageName
    desiredStatus
    costPerHr
    machineId
    machine {
      gpuDisplayName
    }
  }
}
"""

STOP_POD_MUTATION = """
mutation StopPod($input: PodStopInput!) {
  podStop(input: $input) {
    id
    desiredStatus
  }
}
"""

TERMINATE_POD_MUTATION = """
mutation TerminatePod($input: PodTerminateInput!) {
  podTerminate(input: $input)
}
"""

RESUME_POD_MUTATION = """
mutation ResumePod($input: PodResumeInput!) {
  podResume(input: $input) {
    id
    desiredStatus
    imageName
    machineId
  }
}
"""

GPU_TYPES_QUERY = """
query {
  gpuTypes {
    id
    displayName
    memoryInGb
    securePrice
    communityPrice
    secureCloud
    communityCloud
    maxGpuCount
    maxGpuCountCommunityCloud
    maxGpuCountSecureCloud
    lowestPrice(input: { gpuCount: 1 }) {
      stockStatus
      maxUnreservedGpuCount
      availableGpuCounts
      totalCount
      rentedCount
      rentalPercentage
      uninterruptablePrice
    }
  }
}
"""


# --- Client ---


class RunPodClient:
    def __init__(self, api_key: str, graphql_url: str = "https://api.runpod.io/graphql",
                 rest_url: str = "https://rest.runpod.io/v1"):
        self._api_key = api_key
        self._gql_url = f"{graphql_url}?api_key={api_key}"
        self._rest_url = rest_url
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    # --- Internal helpers ---

    async def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        try:
            resp = await self._client.post(self._gql_url, json=payload)
        except httpx.ConnectError:
            raise RunPodConnectionError("Cannot reach RunPod API. Check your internet connection.")
        except httpx.TimeoutException:
            raise RunPodTimeoutError("RunPod API request timed out (30s).")

        if resp.status_code == 401:
            raise RunPodAuthError("Invalid API key. Check RUNPOD_API_KEY in .env.")

        # GraphQL APIs may return errors in the body even with non-200 status
        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()
            return {}

        if "errors" in body:
            raise RunPodAPIError(body["errors"])

        if resp.status_code >= 400:
            raise RunPodAPIError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        return body.get("data", {})

    async def _rest_get(self, path: str, params: dict | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            resp = await self._client.get(
                f"{self._rest_url}{path}", headers=headers, params=params
            )
        except httpx.ConnectError:
            raise RunPodConnectionError("Cannot reach RunPod API. Check your internet connection.")
        except httpx.TimeoutException:
            raise RunPodTimeoutError("RunPod API request timed out (30s).")

        if resp.status_code == 401:
            raise RunPodAuthError("Invalid API key. Check RUNPOD_API_KEY in .env.")
        resp.raise_for_status()
        return resp.json()

    # --- Parsing helpers ---

    @staticmethod
    def _parse_runtime(raw: dict | None) -> PodRuntime | None:
        if not raw:
            return None
        gpus = [
            GpuMetrics(
                gpu_util_percent=g.get("gpuUtilPercent", 0.0) or 0.0,
                memory_util_percent=g.get("memoryUtilPercent", 0.0) or 0.0,
            )
            for g in (raw.get("gpus") or [])
        ]
        ports = [
            PortMapping(
                ip=p.get("ip", ""),
                is_ip_public=p.get("isIpPublic", False),
                private_port=p.get("privatePort", 0),
                public_port=p.get("publicPort", 0),
                port_type=p.get("type", ""),
            )
            for p in (raw.get("ports") or [])
        ]
        return PodRuntime(
            uptime_seconds=raw.get("uptimeInSeconds", 0) or 0,
            gpus=gpus,
            ports=ports,
        )

    @staticmethod
    def _parse_pod(raw: dict) -> Pod:
        machine = raw.get("machine") or {}
        return Pod(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            image_name=raw.get("imageName", ""),
            desired_status=raw.get("desiredStatus", ""),
            cost_per_hr=float(raw.get("costPerHr", 0) or 0),
            gpu_count=raw.get("gpuCount", 0) or 0,
            gpu_display_name=machine.get("gpuDisplayName", ""),
            volume_in_gb=raw.get("volumeInGb", 0) or 0,
            container_disk_in_gb=raw.get("containerDiskInGb", 0) or 0,
            volume_mount_path=raw.get("volumeMountPath", "/workspace"),
            template_id=raw.get("templateId", "") or "",
            machine_id=raw.get("machineId", "") or "",
            runtime=RunPodClient._parse_runtime(raw.get("runtime")),
            env=raw.get("env") or [],
            ports_config=raw.get("ports", "") or "",
        )

    @staticmethod
    def _parse_gpu_type(raw: dict) -> GpuType:
        lp = raw.get("lowestPrice") or {}
        return GpuType(
            id=raw.get("id", ""),
            display_name=raw.get("displayName", ""),
            memory_gb=raw.get("memoryInGb", 0) or 0,
            secure_price=float(raw.get("securePrice", 0) or 0),
            community_price=float(raw.get("communityPrice", 0) or 0),
            max_gpu_count=raw.get("maxGpuCount", 0) or 0,
            max_gpu_count_community=raw.get("maxGpuCountCommunityCloud", 0) or 0,
            max_gpu_count_secure=raw.get("maxGpuCountSecureCloud", 0) or 0,
            stock_status=lp.get("stockStatus") or "",
            available_gpu_counts=lp.get("availableGpuCounts") or [],
            max_unreserved_gpu_count=lp.get("maxUnreservedGpuCount") or 0,
            total_count=lp.get("totalCount") or 0,
            rented_count=lp.get("rentedCount") or 0,
            rental_percentage=float(lp.get("rentalPercentage") or 0),
            secure_cloud=raw.get("secureCloud") or False,
            community_cloud=raw.get("communityCloud") or False,
        )

    @staticmethod
    def _parse_template(raw: dict) -> Template:
        raw_env = raw.get("env") or {}
        # REST returns env as {"KEY": "VALUE"} dict
        if isinstance(raw_env, list):
            env = {item.get("key", ""): item.get("value", "") for item in raw_env if isinstance(item, dict)}
        elif isinstance(raw_env, dict):
            env = raw_env
        else:
            env = {}
        return Template(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            image_name=raw.get("imageName", ""),
            category=raw.get("category", ""),
            container_disk_in_gb=raw.get("containerDiskInGb", 5) or 5,
            volume_in_gb=raw.get("volumeInGb", 0) or 0,
            volume_mount_path=raw.get("volumeMountPath", "/workspace"),
            docker_start_cmd=raw.get("dockerStartCmd", "") or "",
            env=env,
            ports=",".join(raw["ports"]) if isinstance(raw.get("ports"), list) else (raw.get("ports", "") or ""),
            is_public=raw.get("isPublic", False),
            is_serverless=raw.get("isServerless", False),
        )

    # --- Pod operations ---

    async def list_pods(self) -> list[Pod]:
        data = await self._gql(LIST_PODS_QUERY)
        myself = data.get("myself") or {}
        raw_pods = myself.get("pods") or []
        return [self._parse_pod(p) for p in raw_pods]

    async def get_pod(self, pod_id: str) -> Pod:
        data = await self._gql(GET_POD_QUERY, {"input": {"podId": pod_id}})
        raw = data.get("pod")
        if not raw:
            raise RunPodAPIError(f"Pod {pod_id} not found")
        return self._parse_pod(raw)

    async def create_pod(
        self,
        name: str,
        image_name: str,
        gpu_type_id: str,
        gpu_count: int = 1,
        cloud_type: str = "ALL",
        volume_in_gb: int = 20,
        container_disk_in_gb: int = 20,
        ports: str = "8888/http",
        volume_mount_path: str = "/workspace",
        env: list[str] | None = None,  # ["KEY=VALUE", ...]
        template_id: str | None = None,
        min_vcpu_count: int = 2,
        min_memory_in_gb: int = 15,
    ) -> Pod:
        input_data: dict[str, Any] = {
            "name": name,
            "imageName": image_name,
            "gpuTypeId": gpu_type_id,
            "gpuCount": gpu_count,
            "cloudType": cloud_type,
            "volumeInGb": volume_in_gb,
            "containerDiskInGb": container_disk_in_gb,
            "ports": ports,
            "volumeMountPath": volume_mount_path,
            "minVcpuCount": min_vcpu_count,
            "minMemoryInGb": min_memory_in_gb,
        }
        if env:
            # API expects EnvironmentVariableInput objects: {key: "K", value: "V"}
            input_data["env"] = [
                {"key": item.split("=", 1)[0], "value": item.split("=", 1)[1]}
                if "=" in item else {"key": item, "value": ""}
                for item in env
            ]
        if template_id:
            input_data["templateId"] = template_id

        data = await self._gql(CREATE_POD_MUTATION, {"input": input_data})
        raw = data.get("podFindAndDeployOnDemand")
        if not raw:
            raise RunPodAPIError("Pod creation returned no data")
        return self._parse_pod(raw)

    async def stop_pod(self, pod_id: str) -> dict:
        data = await self._gql(STOP_POD_MUTATION, {"input": {"podId": pod_id}})
        return data.get("podStop", {})

    async def terminate_pod(self, pod_id: str) -> None:
        await self._gql(TERMINATE_POD_MUTATION, {"input": {"podId": pod_id}})

    async def resume_pod(self, pod_id: str, gpu_count: int = 1) -> dict:
        data = await self._gql(
            RESUME_POD_MUTATION,
            {"input": {"podId": pod_id, "gpuCount": gpu_count}},
        )
        return data.get("podResume", {})

    # --- GPU types ---

    async def list_gpu_types(self) -> list[GpuType]:
        data = await self._gql(GPU_TYPES_QUERY)
        raw_types = data.get("gpuTypes") or []
        gpus = [self._parse_gpu_type(g) for g in raw_types]
        return sorted(gpus, key=lambda g: g.lowest_price)

    # --- Templates ---

    async def list_templates(self, include_public: bool = False) -> list[Template]:
        params = {}
        if include_public:
            params["includePublicTemplates"] = "true"
        data = await self._rest_get("/templates", params=params)
        raw_templates = data if isinstance(data, list) else data.get("templates", data.get("data", []))
        if not isinstance(raw_templates, list):
            raw_templates = []
        return [self._parse_template(t) for t in raw_templates]
