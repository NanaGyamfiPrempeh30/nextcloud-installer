"""Download and SHA256-verify the Nextcloud release tarball — REQ-6, REQ-7."""

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import log
from .exitcodes import EXIT_CHECKSUM

_DOWNLOAD_BASE = "https://download.nextcloud.com/server/releases"
_GITHUB_LATEST_API = "https://api.github.com/repos/nextcloud/server/releases/latest"


def fetch_latest_version() -> str:
    """Return the latest stable Nextcloud version string, e.g. '30.0.2'."""
    log.info("[DOWNLOAD] Fetching latest Nextcloud version from GitHub releases API ...")
    req = urllib.request.Request(
        _GITHUB_LATEST_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "nextcloud-installer",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as exc:
        log.error(f"[DOWNLOAD] ERROR: Could not reach GitHub API: {exc}")
        sys.exit(EXIT_CHECKSUM)

    version = data["tag_name"].lstrip("v")
    log.info(f"[DOWNLOAD] Latest stable version: {version}")
    return version


def _tarball_name(version: str) -> str:
    return f"nextcloud-{version}.tar.bz2"


def _fetch_expected_sha256(version: str) -> str:
    """Download the published .sha256 file and return the hex digest string."""
    url = f"{_DOWNLOAD_BASE}/{_tarball_name(version)}.sha256"
    log.info(f"[DOWNLOAD] Fetching published checksum from {url}")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            content = resp.read().decode().strip()
    except urllib.error.URLError as exc:
        log.error(f"[DOWNLOAD] ERROR: Could not fetch checksum file: {exc}")
        sys.exit(EXIT_CHECKSUM)
    # Nextcloud ships sha256sum-format lines: "<hash>  <filename>"
    return content.split()[0]


def _sha256_of(path: Path) -> str:
    """Compute and return the SHA256 hex digest of a local file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_tarball(version: str, dest: Path) -> Path:
    """Stream the tarball to dest/ with a live progress indicator."""
    url = f"{_DOWNLOAD_BASE}/{_tarball_name(version)}"
    out_path = dest / _tarball_name(version)
    log.info(f"[DOWNLOAD] Downloading {url}")

    req = urllib.request.Request(url, headers={"User-Agent": "nextcloud-installer"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with out_path.open("wb") as f:
                while True:
                    chunk = resp.read(1 << 20)  # 1 MiB per read
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(
                            f"\r[DOWNLOAD] {pct:3d}%  "
                            f"{downloaded >> 20} MiB / {total >> 20} MiB",
                            end="",
                            flush=True,
                        )
    except urllib.error.URLError as exc:
        out_path.unlink(missing_ok=True)
        log.error(f"[DOWNLOAD] ERROR: Download failed: {exc}")
        sys.exit(EXIT_CHECKSUM)

    if total:
        print()  # newline after the progress bar

    log.ok(f"[DOWNLOAD] Saved {out_path.name} ({out_path.stat().st_size >> 20} MiB).")
    return out_path


def run_all(version: str | None, work_dir: Path) -> tuple[str, Path]:
    """REQ-6 + REQ-7: download and verify the Nextcloud tarball.

    Returns (resolved_version, tarball_path) on success.
    Exits with EXIT_CHECKSUM if the SHA256 check fails or the download errors.
    """
    if version is None:
        version = fetch_latest_version()

    work_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = work_dir / _tarball_name(version)

    expected = _fetch_expected_sha256(version)

    # Skip download if a valid copy is already on disk (idempotent re-runs).
    if tarball_path.exists():
        log.info(f"[DOWNLOAD] {tarball_path.name} already present — verifying checksum ...")
        if _sha256_of(tarball_path) == expected:
            log.ok("[DOWNLOAD] Existing file is valid — skipping download.")
            return version, tarball_path
        log.info("[DOWNLOAD] Existing file is corrupt or incomplete — re-downloading.")
        tarball_path.unlink()

    tarball_path = _download_tarball(version, work_dir)

    log.info("[DOWNLOAD] Verifying SHA256 ...")
    actual = _sha256_of(tarball_path)
    if actual != expected:
        # REQ-7: abort without leaving a corrupt file behind.
        tarball_path.unlink(missing_ok=True)
        log.error(
            f"[DOWNLOAD] ERROR: SHA256 mismatch for {tarball_path.name}\n"
            f"  Expected: {expected}\n"
            f"  Got:      {actual}"
        )
        sys.exit(EXIT_CHECKSUM)

    log.ok(f"[DOWNLOAD] SHA256 verified ({actual[:16]}...).")
    return version, tarball_path


def extract_tarball(tarball_path: Path, web_root: Path) -> None:
    """REQ-6: Extract the verified tarball into web_root.

    The Nextcloud tarball always contains a single top-level directory named
    'nextcloud/'. We extract to a sibling temp directory, then rename that
    subdirectory to web_root so the final path is predictable.

    Idempotent: skips if web_root/occ already exists.
    A partial web_root (directory present but occ absent) is removed and
    re-extracted cleanly.
    Sets ownership of web_root to www-data:www-data after extraction.
    """
    occ_path = web_root / "occ"

    if occ_path.exists():
        log.ok(f"[EXTRACT] {web_root} already extracted — skipping.")
        return

    if web_root.exists():
        log.info(f"[EXTRACT] Removing incomplete extraction at {web_root} ...")
        shutil.rmtree(web_root)

    web_root.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"[EXTRACT] Extracting {tarball_path.name} → {web_root} ...")

    with tempfile.TemporaryDirectory(dir=tarball_path.parent) as tmp_str:
        tmp = Path(tmp_str)
        try:
            with tarfile.open(tarball_path, "r:bz2") as tf:
                # filter="data" (Python 3.12+) blocks path-traversal members.
                if sys.version_info >= (3, 12):
                    tf.extractall(tmp, filter="data")
                else:
                    tf.extractall(tmp)
        except tarfile.TarError as exc:
            log.error(f"[EXTRACT] ERROR: extraction failed: {exc}")
            sys.exit(EXIT_CHECKSUM)

        extracted = tmp / "nextcloud"
        if not extracted.is_dir():
            log.error(
                "[EXTRACT] ERROR: expected a 'nextcloud/' directory "
                "at the top level of the tarball."
            )
            sys.exit(EXIT_CHECKSUM)

        shutil.move(str(extracted), str(web_root))

    r = subprocess.run(
        ["chown", "-R", "www-data:www-data", str(web_root)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        log.error(f"[EXTRACT] ERROR: chown failed:\n{r.stderr.strip()}")
        sys.exit(EXIT_CHECKSUM)

    log.ok(f"[EXTRACT] Extracted to {web_root} (www-data ownership set).")
