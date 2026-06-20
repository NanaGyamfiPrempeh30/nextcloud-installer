"""Abstract package-manager interface and platform implementations."""

import os
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from . import log
from .detect import OSInfo
from .exitcodes import EXIT_DEPENDENCY

# Passed to every apt-get invocation — enforces non-interactive mode.
_APT_ENV = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}


class PackageManager(ABC):
    """Minimal interface the installer uses to query and manage packages."""

    @abstractmethod
    def is_installed(self, package: str) -> bool:
        """Return True if package is installed and in a usable state."""

    @abstractmethod
    def install(self, packages: list[str]) -> None:
        """Install packages. Exits with EXIT_DEPENDENCY on failure."""

    @abstractmethod
    def update(self) -> None:
        """Refresh the package index. Exits with EXIT_DEPENDENCY on failure."""

    @abstractmethod
    def candidate_version(self, package: str) -> str:
        """Return the installable version string for package, or '' if unavailable."""


class AptPackageManager(PackageManager):
    def __init__(self, distro: str) -> None:
        self._distro = distro  # 'ubuntu' | 'debian'

    def is_installed(self, package: str) -> bool:
        r = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            capture_output=True,
            text=True,
        )
        return "install ok installed" in r.stdout

    def install(self, packages: list[str]) -> None:
        log.info(f"[DEPS] apt-get install: {' '.join(packages)}")
        r = subprocess.run(
            ["apt-get", "install", "-y", "--no-install-recommends", *packages],
            capture_output=True,
            text=True,
            env=_APT_ENV,
        )
        if r.returncode != 0:
            log.error(f"[DEPS] ERROR: apt-get install failed:\n{r.stderr.strip()}")
            sys.exit(EXIT_DEPENDENCY)
        log.ok(f"[DEPS] Installed: {' '.join(packages)}")

    def update(self) -> None:
        log.info("[DEPS] apt-get update ...")
        r = subprocess.run(
            ["apt-get", "update", "-q"],
            capture_output=True,
            text=True,
            env=_APT_ENV,
        )
        if r.returncode != 0:
            log.error(f"[DEPS] ERROR: apt-get update failed:\n{r.stderr.strip()}")
            sys.exit(EXIT_DEPENDENCY)
        log.ok("[DEPS] Package index refreshed.")

    def candidate_version(self, package: str) -> str:
        r = subprocess.run(
            ["apt-cache", "policy", package],
            capture_output=True,
            text=True,
        )
        for line in r.stdout.splitlines():
            if line.strip().startswith("Candidate:"):
                return line.strip().removeprefix("Candidate:").strip()
        return ""

    def add_php_repo(self) -> None:
        """Add the distro-appropriate PHP backport repository (REQ-4)."""
        if self._distro == "ubuntu":
            if not self.is_installed("software-properties-common"):
                self.install(["software-properties-common"])
            log.info("[DEPS] Adding ondrej/php PPA ...")
            r = subprocess.run(
                ["add-apt-repository", "-y", "ppa:ondrej/php"],
                capture_output=True,
                text=True,
                env=_APT_ENV,
            )
            if r.returncode != 0:
                log.error(f"[DEPS] ERROR: add-apt-repository failed:\n{r.stderr.strip()}")
                sys.exit(EXIT_DEPENDENCY)

        elif self._distro == "debian":
            self._add_sury_repo()

        else:
            log.error(f"[DEPS] ERROR: no PHP repo handler for distro '{self._distro}'.")
            sys.exit(EXIT_DEPENDENCY)

        self.update()

    def _add_sury_repo(self) -> None:
        """Add packages.sury.org PHP repository for Debian (uses urllib — no curl needed)."""
        import urllib.request

        keyring = Path("/usr/share/keyrings/deb.sury.org-php.gpg")
        sources = Path("/etc/apt/sources.list.d/php.list")

        if not keyring.exists():
            log.info("[DEPS] Downloading sury.org GPG key ...")
            with urllib.request.urlopen(
                "https://packages.sury.org/php/apt.gpg"
            ) as resp:
                keyring.write_bytes(resp.read())
            keyring.chmod(0o644)

        if not sources.exists():
            r = subprocess.run(
                ["lsb_release", "-sc"], capture_output=True, text=True
            )
            codename = r.stdout.strip()
            sources.write_text(
                f"deb [signed-by={keyring}] "
                f"https://packages.sury.org/php/ {codename} main\n"
            )


class BrewPackageManager(PackageManager):
    """macOS scaffold — bodies are intentional stubs (Section 4 of spec)."""

    _MSG = (
        "macOS Homebrew support is scaffolded for a future release. "
        "Pre-flight should have exited before this point."
    )

    def is_installed(self, package: str) -> bool:
        raise NotImplementedError(self._MSG)

    def install(self, packages: list[str]) -> None:
        raise NotImplementedError(self._MSG)

    def update(self) -> None:
        raise NotImplementedError(self._MSG)

    def candidate_version(self, package: str) -> str:
        raise NotImplementedError(self._MSG)


def make(os_info: OSInfo) -> PackageManager:
    """Return the correct PackageManager for the detected OS."""
    if os_info.system == "Linux" and os_info.distro in ("ubuntu", "debian"):
        return AptPackageManager(os_info.distro)
    if os_info.distro == "macos":
        return BrewPackageManager()
    log.error(f"[DEPS] ERROR: no package-manager implementation for '{os_info.distro}'.")
    sys.exit(EXIT_DEPENDENCY)
