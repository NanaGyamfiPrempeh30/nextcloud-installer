"""OS detection layer."""

import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class OSInfo:
    system: str          # 'Linux' | 'Darwin'
    distro: str          # 'ubuntu' | 'debian' | 'macos' | 'unknown'
    version: str         # e.g. '22.04', '12', '14.5'
    arch: str            # 'x86_64' | 'arm64'
    supported: bool
    scaffolded: bool     # detected but not yet fully implemented


def _parse_os_release() -> tuple[str, str]:
    """Return (distro_id, version_id) from /etc/os-release."""
    path = Path("/etc/os-release")
    if not path.exists():
        return "unknown", "unknown"
    fields: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            fields[key.strip()] = val.strip().strip('"')
    return fields.get("ID", "unknown").lower(), fields.get("VERSION_ID", "unknown")


_SUPPORTED: dict[str, set[str]] = {
    "ubuntu": {"22.04", "24.04"},
    "debian": {"12"},
}

_SCAFFOLDED: set[str] = {"macos"}


def detect() -> OSInfo:
    system = platform.system()   # 'Linux' | 'Darwin' | 'Windows' | ...
    arch = platform.machine()    # 'x86_64' | 'arm64' | 'aarch64'

    if system == "Darwin":
        mac_ver = platform.mac_ver()[0]   # e.g. '14.5.0'
        version = mac_ver.rsplit(".", 1)[0] if mac_ver else "unknown"
        return OSInfo(
            system=system,
            distro="macos",
            version=version,
            arch=arch,
            supported=False,
            scaffolded=True,
        )

    if system == "Linux":
        distro, version = _parse_os_release()
        supported_versions = _SUPPORTED.get(distro, set())
        return OSInfo(
            system=system,
            distro=distro,
            version=version,
            arch=arch,
            supported=version in supported_versions,
            scaffolded=False,
        )

    return OSInfo(
        system=system,
        distro="unknown",
        version="unknown",
        arch=arch,
        supported=False,
        scaffolded=False,
    )
