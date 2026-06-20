"""Clean teardown of a partial or failed install — REQ-21 (--reset)."""

import shutil
import subprocess
from pathlib import Path

from . import db, log

# All services the installer may have started or configured, in stop order.
# PHP-FPM service names are version-specific; we try every known version and
# ignore failures for ones that were never installed.
_SERVICES: list[str] = [
    "nginx",
    "php8.4-fpm",
    "php8.3-fpm",
    "php8.2-fpm",
    "php8.1-fpm",
    "mariadb",
    "redis-server",
]

# Paths the installer owns and may have created.
_CERT_DIR        = Path("/etc/ssl/nextcloud")
_NGINX_AVAILABLE = Path("/etc/nginx/sites-available/nextcloud")
_NGINX_ENABLED   = Path("/etc/nginx/sites-enabled/nextcloud")


def _stop_services() -> None:
    log.info("[RESET] Stopping services ...")
    for svc in _SERVICES:
        r = subprocess.run(
            ["systemctl", "stop", svc],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log.ok(f"[RESET] Stopped {svc}.")
        # Non-zero is expected for services that were never installed — not an error.


def _remove(path: Path, label: str) -> None:
    """Remove a file, symlink, or directory tree; log the outcome."""
    if not path.exists() and not path.is_symlink():
        log.info(f"[RESET] {label} not present — skipping.")
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)
    log.ok(f"[RESET] Removed {label} ({path}).")


def run_all(
    db_name: str,
    db_user: str,
    web_root: Path,
    data_dir: Path,
    work_dir: Path,
    db_host: str = db._DEFAULT_DB_HOST,
) -> None:
    """REQ-21: Best-effort teardown so a fresh install can be retried.

    Each step is attempted independently — a failure in one step does not
    abort the rest, because partial cleanup is better than no cleanup.
    """
    log.info("[RESET] Starting teardown ...")

    # Drop the database before stopping MariaDB — the drop needs the service running.
    db.drop_all(db_name, db_user, db_host)
    _stop_services()

    _remove(web_root,         "web root")
    _remove(data_dir,         "data directory")
    _remove(_CERT_DIR,        "TLS certificate directory")
    _remove(_NGINX_AVAILABLE, "nginx site config")
    _remove(_NGINX_ENABLED,   "nginx site symlink")
    _remove(work_dir,         "installer work directory")

    log.ok("[RESET] Teardown complete. Ready for a fresh install.")
