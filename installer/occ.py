"""Nextcloud occ command interface — REQ-10 (install check), REQ-11 (install)."""

import enum
import json
import re
import subprocess
import sys
from pathlib import Path

from . import log
from .exitcodes import EXIT_OCC


class InstallState(enum.Enum):
    INSTALLED = "installed"        # occ status returned installed=true
    NOT_INSTALLED = "not_installed"  # occ status returned installed=false
    UNKNOWN = "unknown"            # occ missing, errored, or returned unparseable output

_DEFAULT_WEB_ROOT  = Path("/var/www/nextcloud")
_DEFAULT_ADMIN_USER = "admin"
_DEFAULT_DATA_DIR  = Path("/var/nextcloud/data")


def check_install_state(web_root: Path) -> InstallState:
    """REQ-10: Probe whether Nextcloud is installed at web_root.

    Returns:
      INSTALLED     — occ status ran cleanly and reported installed=true.
      NOT_INSTALLED — occ status ran cleanly and reported installed=false.
      UNKNOWN       — occ binary missing (fresh extraction not done yet), occ
                      exited non-zero (DB unreachable, PHP error, etc.), timed
                      out, or returned unparseable JSON.  The caller must decide
                      what to do; this function never silently equates an error
                      with "not installed".
    """
    occ = web_root / "occ"
    if not occ.exists():
        return InstallState.UNKNOWN

    try:
        r = subprocess.run(
            ["sudo", "-u", "www-data", "php", str(occ), "status", "--output=json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return InstallState.UNKNOWN

    if r.returncode != 0:
        return InstallState.UNKNOWN

    try:
        data = json.loads(r.stdout)
        installed = bool(data.get("installed", False))
        return InstallState.INSTALLED if installed else InstallState.NOT_INSTALLED
    except (json.JSONDecodeError, AttributeError):
        return InstallState.UNKNOWN


def _maintenance_install(
    occ_path: Path,
    db_name: str,
    db_user: str,
    db_password: str,
    db_host: str,
    admin_user: str,
    admin_password: str,
    data_dir: Path,
) -> None:
    """REQ-11: Run occ maintenance:install --no-interaction with all params as flags.

    Credentials pass through argv (occ provides no stdin/env-var interface for
    maintenance:install). They are visible in the process list for the duration
    of the subprocess — acceptable in a dedicated install environment.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    # data_dir was just created as root; www-data must own it to write session files.
    subprocess.run(
        ["chown", "www-data:www-data", str(data_dir)],
        capture_output=True,
        text=True,
    )

    cmd = [
        "sudo", "-u", "www-data",
        "php", str(occ_path),
        "maintenance:install",
        "--no-interaction",
        "--database=mysql",
        f"--database-name={db_name}",
        f"--database-user={db_user}",
        f"--database-pass={db_password}",
        f"--database-host={db_host}",
        f"--admin-user={admin_user}",
        f"--admin-pass={admin_password}",
        f"--data-dir={data_dir}",
    ]

    # Log the intent without exposing credentials.
    log.info(
        f"[OCC] Running maintenance:install "
        f"(database={db_name}, admin-user={admin_user}, data-dir={data_dir}) ..."
    )

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        if "files already exist for this user" in r.stdout:
            log.error(
                "[OCC] Install failed: stale admin user data found in the data "
                "directory from a previous incomplete install.  "
                "Run with --reset to clean up, then retry."
            )
        else:
            log.error(f"[OCC] ERROR: maintenance:install failed:\n{r.stderr.strip()}")
            if r.stdout.strip():
                log.error(f"[OCC] stdout:\n{r.stdout.strip()}")
        sys.exit(EXIT_OCC)

    log.ok("[OCC] maintenance:install completed successfully.")


_REDIS_SETTINGS: list[tuple[list[str], list[str]]] = [
    (["memcache.local"],   [r"--value=\OC\Memcache\Redis"]),
    (["memcache.locking"], [r"--value=\OC\Memcache\Redis"]),
    (["redis", "host"],    ["--value=127.0.0.1"]),
    (["redis", "port"],    ["--value=6379", "--type=integer"]),
]


def _configure_redis(occ_path: Path) -> None:
    """REQ-22: Wire Nextcloud to Redis for local cache and distributed file locking."""
    base = ["sudo", "-u", "www-data", "php", str(occ_path), "config:system:set"]
    for key_parts, extra_args in _REDIS_SETTINGS:
        label = " ".join(key_parts)
        log.info(f"[OCC] Setting {label} ...")
        r = subprocess.run(base + key_parts + extra_args, capture_output=True, text=True)
        if r.returncode != 0:
            log.error(f"[OCC] ERROR: config:system:set {label} failed:\n{r.stderr.strip()}")
            if r.stdout.strip():
                log.error(f"[OCC] stdout:\n{r.stdout.strip()}")
            sys.exit(EXIT_OCC)
        log.ok(f"[OCC] Set {label}.")

    # Read back memcache.local — don't trust set exit codes alone.
    r = subprocess.run(
        ["sudo", "-u", "www-data", "php", str(occ_path),
         "config:system:get", "memcache.local"],
        capture_output=True, text=True,
    )
    value = r.stdout.strip()
    expected = r"\OC\Memcache\Redis"
    if r.returncode != 0 or value != expected:
        log.error(
            f"[OCC] ERROR: memcache.local read-back failed — "
            f"expected {expected!r}, got {value!r}."
        )
        sys.exit(EXIT_OCC)
    log.ok(f"[OCC] Redis cache wiring confirmed: memcache.local = {value}")


def enable_apps(occ_path: Path, apps: list[str]) -> None:
    """REQ-13: Enable optional apps via occ app:enable after core install."""
    if not apps:
        return
    log.info(f"[OCC] Enabling apps: {', '.join(apps)} ...")
    r = subprocess.run(
        ["sudo", "-u", "www-data", "php", str(occ_path), "app:enable", *apps],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        log.error(f"[OCC] ERROR: app:enable failed:\n{r.stderr.strip()}")
        if r.stdout.strip():
            log.error(f"[OCC] stdout:\n{r.stdout.strip()}")
        sys.exit(EXIT_OCC)
    log.ok(f"[OCC] Apps enabled: {', '.join(apps)}")


def _clear_partial_config(web_root: Path) -> None:
    """Remove config/config.php left by a previous failed maintenance:install run.

    If config.php exists with 'installed' => true but occ status failed, something
    more serious is wrong (DB down, credentials changed externally).  In that case
    we refuse to proceed rather than silently overwriting a working installation.
    """
    config_php = web_root / "config" / "config.php"
    if not config_php.exists():
        return

    try:
        content = config_php.read_text()
    except OSError:
        content = ""

    if re.search(r"'installed'\s*=>\s*true", content):
        log.error(
            "[OCC] config/config.php has 'installed => true' but 'occ status' "
            "failed.  The database may be unreachable or its credentials have "
            "changed externally.  Run with --reset to tear down and retry."
        )
        sys.exit(EXIT_OCC)

    # installed=false or key absent — partial run.  Remove so maintenance:install
    # starts clean rather than loading a stale dbpassword from this file.
    log.info("[OCC] Removing partial config.php from a previous failed run ...")
    config_php.unlink()


def run_all(
    web_root: Path,
    db_name: str,
    db_user: str,
    db_password: str,
    db_host: str,
    admin_user: str,
    admin_password: str,
    data_dir: Path,
    apps: list[str] = (),
) -> None:
    """REQ-10 guard + REQ-11 install + REQ-13 app enable.

    Skips the entire occ phase if Nextcloud is already installed — this is what
    makes the second-run acceptance criterion (spec Section 9 item 2) pass.
    enable_apps() is only called after a fresh install, not on the skip path.
    """
    log.info(f"[OCC] Checking install status at {web_root} ...")

    state = check_install_state(web_root)
    if state is InstallState.INSTALLED:
        log.ok("[OCC] Nextcloud is already installed — skipping maintenance:install.")
        return
    if state is InstallState.UNKNOWN:
        log.error(
            "[OCC] ERROR: 'occ status' failed or returned unparseable output.  "
            "The database may be unreachable, PHP may have errored, or the web "
            "root may be corrupt.  Cannot safely determine install state — aborting.  "
            "Run with --reset to tear down and retry from scratch."
        )
        sys.exit(EXIT_OCC)
    # state is NOT_INSTALLED — fall through to maintenance:install.

    # A previous failed maintenance:install run may have written a partial config.php
    # that contains the old DB password.  If occ loads it before applying --database-pass
    # from the CLI, the initial DB connection uses the stale password and fails with
    # "Access Denied" even though the MariaDB user has the correct new password.
    _clear_partial_config(web_root)

    occ_path = web_root / "occ"
    if not occ_path.exists():
        log.error(
            f"[OCC] ERROR: {occ_path} not found — "
            "the extraction step did not populate the web root correctly."
        )
        sys.exit(EXIT_OCC)

    _maintenance_install(
        occ_path=occ_path,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        admin_user=admin_user,
        admin_password=admin_password,
        data_dir=data_dir,
    )

    # Re-assert www-data ownership after install; occ or PHP may have written
    # files as root (e.g. opcache or lock files) that would block later www-data runs.
    subprocess.run(
        ["chown", "-R", "www-data:www-data", str(web_root)],
        capture_output=True,
        text=True,
    )

    # REQ-22: wire Redis for local cache and distributed file locking.
    _configure_redis(occ_path)

    enable_apps(occ_path, list(apps))
