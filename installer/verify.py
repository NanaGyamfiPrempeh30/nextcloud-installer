"""Post-install verification — REQ-16 (occ status), REQ-17 (HTTP check)."""

import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import log
from .exitcodes import EXIT_VERIFY


def check_occ_status(web_root: Path, expected_version: str) -> None:
    """REQ-16: Confirm occ reports installed=true and a version matching expected_version.

    Version matching uses a prefix check: expected_version="30.0.2" passes when occ
    reports "30.0.2.1" (Nextcloud appends a patch-level digit in the build string).
    Exits EXIT_VERIFY and logs exactly which check failed (REQ-18).
    """
    occ_path = web_root / "occ"
    log.info(f"[VERIFY] Checking occ status at {web_root} ...")

    r = subprocess.run(
        ["sudo", "-u", "www-data", "php", str(occ_path), "status", "--output=json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        log.error(
            f"[VERIFY] FAILED: occ status exited {r.returncode}:\n{r.stderr.strip()}"
        )
        sys.exit(EXIT_VERIFY)

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        log.error(
            f"[VERIFY] FAILED: occ status returned invalid JSON:\n{r.stdout[:300]}"
        )
        sys.exit(EXIT_VERIFY)

    if not data.get("installed"):
        log.error("[VERIFY] FAILED: occ status reports installed=false after install.")
        sys.exit(EXIT_VERIFY)

    # occ may return "30.0.2.1" when the tarball release is "30.0.2" — prefix-match.
    installed_ver = data.get("versionstring") or data.get("version", "")
    if not installed_ver.startswith(expected_version):
        log.error(
            f"[VERIFY] FAILED: version mismatch — "
            f"expected {expected_version!r}, occ reports {installed_ver!r}."
        )
        sys.exit(EXIT_VERIFY)

    log.ok(f"[VERIFY] occ status OK: installed=true, version={installed_ver}.")


def check_http(server_name: str) -> None:
    """REQ-17: Confirm the Nextcloud instance returns 2xx/3xx over HTTPS.

    Targets /status.php — a lightweight, auth-free endpoint that returns 200
    on any working Nextcloud install.  SSL certificate verification is disabled
    so self-signed certs (REQ-14) don't cause a spurious failure; the TLS
    configuration was already validated in Step 7.
    Exits EXIT_VERIFY and reports exactly which check failed (REQ-18).
    """
    url = f"https://{server_name}/status.php"
    log.info(f"[VERIFY] Checking HTTP response from {url} ...")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={"User-Agent": "nextcloud-installer/1"})
    code: int
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, OSError) as e:
        log.error(
            f"[VERIFY] FAILED: HTTP check — could not connect to {url}: {e}"
        )
        sys.exit(EXIT_VERIFY)

    if not (200 <= code < 400):
        log.error(
            f"[VERIFY] FAILED: HTTP check — {url} returned {code} (expected 2xx/3xx)."
        )
        sys.exit(EXIT_VERIFY)

    log.ok(f"[VERIFY] HTTP check OK: {url} returned {code}.")


def check_redis(web_root: Path) -> None:
    """REQ-22: Confirm memcache.local is wired to \\OC\\Memcache\\Redis.

    Reads back the live config value via occ rather than parsing config.php,
    so this catches both missing wiring and a wiring step that set the wrong value.
    """
    occ_path = web_root / "occ"
    log.info("[VERIFY] Checking Redis cache wiring ...")
    r = subprocess.run(
        ["sudo", "-u", "www-data", "php", str(occ_path),
         "config:system:get", "memcache.local"],
        capture_output=True, text=True, timeout=15,
    )
    value = r.stdout.strip()
    expected = r"\OC\Memcache\Redis"
    if r.returncode != 0 or value != expected:
        log.error(
            f"[VERIFY] FAILED: memcache.local is {value!r}, expected {expected!r}. "
            "Redis cache wiring is missing or incorrect."
        )
        sys.exit(EXIT_VERIFY)
    log.ok(f"[VERIFY] Redis cache wiring OK: memcache.local = {value}")


def run_all(web_root: Path, version: str, server_name: str) -> None:
    """REQ-16 + REQ-17 + REQ-22: run all post-install verification checks.

    Each check exits EXIT_VERIFY and names itself if it fails (REQ-18).
    The final success line is only reached when every check passes.
    """
    log.info("[VERIFY] Starting post-install verification ...")
    check_occ_status(web_root, version)   # REQ-16
    check_http(server_name)               # REQ-17
    check_redis(web_root)                 # REQ-22
    log.ok("[VERIFY] Post-install verification passed.")
