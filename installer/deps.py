"""Dependency resolution — REQ-3 (check/install) and REQ-4 (PHP repo)."""

import re
import subprocess
import sys

from . import log
from .detect import OSInfo
from .exitcodes import EXIT_DEPENDENCY
from .pkg import AptPackageManager, PackageManager

# Open risk (spec Section 8): verify this against the target Nextcloud release notes
# before shipping. Nextcloud 28+ requires PHP 8.1; bump to 8.2 for NC 31+ if needed.
PHP_MINIMUM: tuple[int, int] = (8, 1)

# Versioned PHP package suffixes required by Nextcloud (core + recommended).
# Package names are constructed as php{ver}-{suffix}, e.g. php8.1-curl.
_PHP_EXTENSIONS: list[str] = [
    "fpm",
    "cli",
    "curl",
    "gd",
    "intl",
    "mbstring",
    "mysql",   # provides pdo_mysql + mysqli
    "xml",     # provides dom, simplexml, xmlreader, xmlwriter, libxml
    "zip",
    "bcmath",
    "gmp",
    "imagick",
    "redis",    # required for REQ-22 Redis cache wiring
]

# Non-PHP system packages required for the native stack.
_SYSTEM_PACKAGES: list[str] = [
    "nginx",
    "mariadb-server",
    "redis-server",
]

# PHP versions the installer knows about, newest-first.
# Used when probing post-PPA availability.
_KNOWN_PHP_VERSIONS: list[str] = ["8.4", "8.3", "8.2", "8.1"]


def _parse_php_ver(s: str) -> tuple[int, int] | None:
    """Extract (major, minor) from an apt version string, e.g. '2:8.1+92ubuntu1' → (8, 1)."""
    m = re.search(r"(\d+)\.(\d+)", s)
    return (int(m.group(1)), int(m.group(2))) if m else None


def resolve_php_version(pm: PackageManager, os_info: OSInfo) -> str:
    """REQ-4: Return the PHP version string to use (e.g. '8.1').

    If the default distro PHP is below PHP_MINIMUM, add the appropriate
    backport repository before returning, so the caller can install the
    versioned packages.
    """
    # Check if a sufficient PHP-FPM is already installed — prefer the newest.
    for ver in _KNOWN_PHP_VERSIONS:
        if _version_tuple(ver) < PHP_MINIMUM:
            break
        if pm.is_installed(f"php{ver}-fpm"):
            log.info(f"[DEPS] PHP {ver} already installed — skipping repo check.")
            return ver

    # Nothing installed yet — ask apt what the default candidate is.
    candidate = pm.candidate_version("php-fpm")
    ver_tuple = _parse_php_ver(candidate) if candidate else None

    if ver_tuple is None or ver_tuple < PHP_MINIMUM:
        min_str = f"{PHP_MINIMUM[0]}.{PHP_MINIMUM[1]}"
        found_str = f"{ver_tuple[0]}.{ver_tuple[1]}" if ver_tuple else "none"
        log.info(
            f"[DEPS] Distro default PHP ({found_str}) is below minimum "
            f"({min_str}) — adding backport repository (REQ-4)."
        )
        assert isinstance(pm, AptPackageManager)
        pm.add_php_repo()

        # Re-probe: find the highest version now available.
        for ver in _KNOWN_PHP_VERSIONS:
            if _version_tuple(ver) < PHP_MINIMUM:
                break
            candidate = pm.candidate_version(f"php{ver}-fpm")
            parsed = _parse_php_ver(candidate) if candidate else None
            if parsed and parsed >= PHP_MINIMUM:
                log.ok(f"[DEPS] PHP {ver} now available from backport repo.")
                return ver

        log.error(f"[DEPS] ERROR: PHP >= {min_str} not available even after adding repo.")
        sys.exit(EXIT_DEPENDENCY)

    resolved = f"{ver_tuple[0]}.{ver_tuple[1]}"
    log.info(f"[DEPS] PHP {resolved} satisfies minimum — no extra repo needed.")
    return resolved


def _version_tuple(ver_str: str) -> tuple[int, int]:
    major, minor = ver_str.split(".")
    return int(major), int(minor)


def _build_package_list(php_ver: str) -> list[str]:
    php_packages = [f"php{php_ver}-{ext}" for ext in _PHP_EXTENSIONS]
    return _SYSTEM_PACKAGES + php_packages


def run_all(pm: PackageManager, os_info: OSInfo) -> str:
    """REQ-3 + REQ-4: resolve PHP version, then check and install all required packages.

    Returns the resolved PHP version string (e.g. '8.1') so callers can derive
    version-specific paths such as the PHP-FPM socket.
    """
    php_ver = resolve_php_version(pm, os_info)
    required = _build_package_list(php_ver)

    missing = [p for p in required if not pm.is_installed(p)]

    if not missing:
        log.ok(f"[DEPS] All {len(required)} required packages already installed — nothing to do.")
        _start_services(php_ver)
        return php_ver

    already = len(required) - len(missing)
    if already:
        log.info(f"[DEPS] {already}/{len(required)} packages already installed, skipping those.")

    log.info(f"[DEPS] Installing {len(missing)} package(s): {', '.join(missing)}")
    pm.update()
    pm.install(missing)
    log.ok("[DEPS] All required packages installed.")

    _start_services(php_ver)
    return php_ver


def _start_services(php_ver: str) -> None:
    """Start and enable services required before the database and occ steps.

    Called after every package-install pass (including re-runs where packages
    were already present) so services are always up before later steps run.
    nginx is excluded here — tls.run_all() starts it after writing the config.
    """
    services = ["mariadb", "redis-server", f"php{php_ver}-fpm"]
    for svc in services:
        log.info(f"[DEPS] Enabling and starting {svc} ...")
        r = subprocess.run(
            ["systemctl", "enable", "--now", svc],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            log.error(f"[DEPS] WARNING: could not start {svc}:\n{r.stderr.strip()}")
        else:
            log.ok(f"[DEPS] {svc} is running.")
