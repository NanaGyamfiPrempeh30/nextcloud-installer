"""Pre-flight checks — run before any system changes are made."""

import os
import sys

from . import log
from .detect import OSInfo
from .exitcodes import EXIT_NOT_ROOT, EXIT_SCAFFOLDED_OS, EXIT_UNSUPPORTED_OS


def check_root() -> None:
    """REQ-2: exit immediately if not running as root/sudo."""
    if os.geteuid() != 0:
        log.error(
            "[PREFLIGHT] ERROR: This script must be run as root or with sudo.\n"
            "  Re-run as:  sudo python3 install.py"
        )
        sys.exit(EXIT_NOT_ROOT)


def check_os(info: OSInfo) -> None:
    """REQ-1: exit before making any changes if the OS is unsupported or scaffolded-only."""
    if info.scaffolded:
        log.error(
            f"[PREFLIGHT] ERROR: {info.distro} {info.version} ({info.arch}) is detected but "
            "not yet implemented in this version of the installer.\n"
            "  macOS support is scaffolded for a future release. No changes have been made."
        )
        sys.exit(EXIT_SCAFFOLDED_OS)

    if not info.supported:
        log.error(
            f"[PREFLIGHT] ERROR: {info.distro} {info.version} ({info.arch}) is not a supported platform.\n"
            "  Supported: Ubuntu 22.04, Ubuntu 24.04, Debian 12\n"
            "  No changes have been made."
        )
        sys.exit(EXIT_UNSUPPORTED_OS)


def run_all(info: OSInfo) -> None:
    """Run every pre-flight check in order. Exits on first failure."""
    check_root()
    check_os(info)
    log.ok(f"[PREFLIGHT] OK — {info.distro} {info.version} ({info.arch}), running as root.")
