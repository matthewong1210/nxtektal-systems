#!/usr/bin/env python3
"""Inspect the development environment for simulation prerequisites.

READ-ONLY: this script makes no changes to the machine. It reports whether
Isaac Sim, ROS 2, Docker, NVIDIA drivers, and a compatible GPU appear to be
available, plus Python/uv status. Stdlib only — runs before `uv sync`.

Usage:
    python3 scripts/inspect_environment.py [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from typing import Any, Optional


def _run(cmd: list[str], timeout: float = 10.0) -> Optional[str]:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout.strip() or out.stderr.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def gather() -> dict[str, Any]:
    system = platform.system()
    info: dict[str, Any] = {
        "platform": {
            "system": system,
            "release": platform.release(),
            "machine": platform.machine(),
            "is_apple_silicon": system == "Darwin" and platform.machine() == "arm64",
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "uv": {"path": _which("uv"), "version": _run(["uv", "--version"]) if _which("uv") else None},
        "docker": {
            "path": _which("docker"),
            "version": _run(["docker", "--version"]) if _which("docker") else None,
        },
        "ros2": {
            "path": _which("ros2"),
            "ros_distro_env": os.environ.get("ROS_DISTRO"),
        },
        "nvidia": {
            "nvidia_smi_path": _which("nvidia-smi"),
            "nvidia_smi": (
                _run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
                if _which("nvidia-smi")
                else None
            ),
        },
        "isaac_sim": {
            "isaac_path_env": os.environ.get("ISAAC_PATH"),
            "pip_package_isaacsim": None,
            "known_install_dirs": [],
        },
    }

    try:
        import importlib.util

        info["isaac_sim"]["pip_package_isaacsim"] = (
            importlib.util.find_spec("isaacsim") is not None
        )
    except Exception:
        info["isaac_sim"]["pip_package_isaacsim"] = False

    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, "isaacsim"),
        os.path.join(home, ".local", "share", "ov"),
        "/Applications/Omniverse Launcher.app",
        "/isaac-sim",
    ):
        if os.path.exists(candidate):
            info["isaac_sim"]["known_install_dirs"].append(candidate)

    if system == "Darwin":
        gpu = _run(["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"], timeout=20)
        chip = None
        if gpu:
            for line in gpu.splitlines():
                if "Chipset Model:" in line:
                    chip = line.split(":", 1)[1].strip()
                    break
        info["gpu"] = {"name": chip, "source": "system_profiler"}
    elif info["nvidia"]["nvidia_smi"]:
        info["gpu"] = {"name": info["nvidia"]["nvidia_smi"], "source": "nvidia-smi"}
    else:
        info["gpu"] = {"name": None, "source": "unknown"}

    nvidia_gpu = bool(info["nvidia"]["nvidia_smi"])
    isaac_present = bool(
        info["isaac_sim"]["pip_package_isaacsim"] or info["isaac_sim"]["known_install_dirs"]
    )
    info["verdict"] = {
        "isaac_sim_runnable_here": nvidia_gpu and isaac_present,
        "ros2_available": info["ros2"]["path"] is not None,
        "docker_available": info["docker"]["path"] is not None,
        "mock_adapter_runnable": True,
        "notes": [
            "The mock adapter (Phase 0) has no simulator requirements.",
            *(
                []
                if nvidia_gpu
                else [
                    "No NVIDIA GPU/driver detected: Isaac Sim cannot run on this "
                    "machine. Use a Linux/Windows workstation with an RTX GPU or a "
                    "cloud instance (see docs/isaac_sim_integration.md)."
                ]
            ),
        ],
    }
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()
    info = gather()
    if args.json:
        print(json.dumps(info, indent=2))
        return 0

    print("NXTektal Virtual Handoff Lab — environment inspection (read-only)")
    print("=" * 68)
    p = info["platform"]
    print(f"Platform      : {p['system']} {p['release']} ({p['machine']})")
    print(f"Python        : {info['python']['version']}  ({info['python']['executable']})")
    print(f"uv            : {info['uv']['version'] or 'NOT FOUND'}")
    print(f"Docker        : {info['docker']['version'] or 'NOT FOUND'}")
    ros = info["ros2"]
    print(f"ROS 2         : {ros['path'] or 'NOT FOUND'}"
          + (f" (ROS_DISTRO={ros['ros_distro_env']})" if ros["ros_distro_env"] else ""))
    print(f"NVIDIA driver : {info['nvidia']['nvidia_smi'] or 'NOT FOUND (no nvidia-smi)'}")
    print(f"GPU           : {info['gpu']['name'] or 'unknown'}")
    isaac = info["isaac_sim"]
    print(f"Isaac Sim     : pip={isaac['pip_package_isaacsim']} "
          f"installs={isaac['known_install_dirs'] or 'none'} "
          f"ISAAC_PATH={isaac['isaac_path_env'] or 'unset'}")
    print("-" * 68)
    v = info["verdict"]
    print(f"Isaac Sim runnable here : {v['isaac_sim_runnable_here']}")
    print(f"Mock adapter runnable   : {v['mock_adapter_runnable']}")
    for note in v["notes"]:
        print(f"  note: {note}")
    print("No changes were made to this machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
