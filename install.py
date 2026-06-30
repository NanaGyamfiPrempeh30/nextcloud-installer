#!/usr/bin/env python3
"""Nextcloud CLI installer — entrypoint."""

import argparse
import datetime
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

from installer import db, deps, detect, download, log, occ, pkg, preflight, reset, tls, verify
from installer.exitcodes import EXIT_OCC

_WORK_DIR = Path("/tmp/nextcloud-installer")
_LOG_PATH = Path("DEBUG_LOG.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fully unattended Nextcloud installer (native stack).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--nextcloud-version",
        metavar="VERSION",
        default=None,
        help="Nextcloud version to install (e.g. 30.0.2). Defaults to latest stable.",
    )
    parser.add_argument(
        "--db-name",
        metavar="NAME",
        default=db._DEFAULT_DB_NAME,
        help=f"MariaDB database name (default: {db._DEFAULT_DB_NAME}).",
    )
    parser.add_argument(
        "--db-user",
        metavar="USER",
        default=db._DEFAULT_DB_USER,
        help=f"MariaDB user to create (default: {db._DEFAULT_DB_USER}).",
    )
    parser.add_argument(
        "--db-password",
        metavar="PASS",
        default=None,
        help=(
            "MariaDB password for the Nextcloud user. "
            "Falls back to NEXTCLOUD_DB_PASSWORD env var. "
            "A strong random password is generated when neither is supplied."
        ),
    )
    parser.add_argument(
        "--web-root",
        metavar="PATH",
        default=str(occ._DEFAULT_WEB_ROOT),
        help=f"Nextcloud installation directory (default: {occ._DEFAULT_WEB_ROOT}).",
    )
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        default=str(occ._DEFAULT_DATA_DIR),
        help=f"Nextcloud data directory, must be outside web root (default: {occ._DEFAULT_DATA_DIR}).",
    )
    parser.add_argument(
        "--admin-user",
        metavar="USER",
        default=occ._DEFAULT_ADMIN_USER,
        help=f"Nextcloud admin account username (default: {occ._DEFAULT_ADMIN_USER}).",
    )
    parser.add_argument(
        "--admin-password",
        metavar="PASS",
        default=None,
        help=(
            "Nextcloud admin account password. "
            "Falls back to NEXTCLOUD_ADMIN_PASSWORD env var. "
            "A strong random password is generated and displayed once when neither is supplied."
        ),
    )
    parser.add_argument(
        "--domain",
        metavar="DOMAIN",
        default=tls._DEFAULT_SERVER_NAME,
        help=(
            f"Hostname or domain for the Nextcloud instance "
            f"(default: {tls._DEFAULT_SERVER_NAME}). "
            "Used as the nginx server_name and TLS CN. "
            "Supplying a real domain with a public IP triggers Let's Encrypt (REQ-15)."
        ),
    )
    parser.add_argument(
        "--apps",
        nargs="*",
        metavar="APP",
        default=[],
        help="Optional Nextcloud apps to enable after install (e.g. --apps contacts calendar tasks).",
    )
    parser.add_argument(
        "--email",
        metavar="EMAIL",
        default=None,
        help=(
            "Contact e-mail address for Let's Encrypt (REQ-15). "
            "Required when --domain is a public FQDN; not needed for localhost/self-signed."
        ),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help=(
            "Remove a partial or failed install and exit (REQ-21). "
            "Stops services, drops the database and user, removes the web root, "
            "TLS certificates, nginx config, and downloaded tarball. "
            "Use before retrying a failed run."
        ),
    )
    return parser.parse_args()


def _resolve_db_password(args: argparse.Namespace) -> str:
    """REQ-9: Return the database password from flag, env var, or generated value.

    A generated password is written to the debug log only — never to stdout.
    If the script dies between the DB step and occ install, a re-run will generate
    a different password; use --reset (REQ-21) to clean up and retry cleanly.
    """
    password = args.db_password or os.environ.get("NEXTCLOUD_DB_PASSWORD")
    if password:
        return password

    password = secrets.token_urlsafe(32)
    log.info("[DB] No database password supplied — generating a strong random one (REQ-9).")
    log.secret("[DB] db_password", password)
    return password


def _resolve_admin_password(args: argparse.Namespace) -> tuple[str, str, bool]:
    """REQ-12: Return (admin_user, admin_password, was_generated).

    A generated password is written to the debug log via log.secret() and
    displayed once on stdout at the end of the run (see _print_admin_credentials).
    """
    admin_user = args.admin_user
    password = args.admin_password or os.environ.get("NEXTCLOUD_ADMIN_PASSWORD")
    if password:
        return admin_user, password, False

    password = secrets.token_urlsafe(16)
    log.info("[OCC] No admin password supplied — generating one (REQ-12).")
    log.secret("[OCC] admin_password", password)
    return admin_user, password, True


def _print_admin_credentials(admin_user: str, admin_password: str) -> None:
    """REQ-12: Display the generated admin password once, prominently, at end of run."""
    bar = "=" * 60
    print(f"""
{bar}
  IMPORTANT — Save your Nextcloud admin credentials
{bar}
  Username : {admin_user}
  Password : {admin_password}
{bar}
  This password will NOT be shown again.
  Store it in a password manager before closing this terminal.
{bar}
""")


def _write_credentials_env(db_password: Optional[str], admin_user: str, admin_password: Optional[str]) -> None:
    if db_password is None or admin_password is None:
        return
    env_path = Path("/root/.nextcloud-installer.env")
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    content = (
        "# Nextcloud installer credentials — chmod 600 — do not commit\n"
        f"# Generated: {timestamp}\n"
        f"NEXTCLOUD_DB_PASSWORD={db_password}\n"
        f"NEXTCLOUD_ADMIN_USER={admin_user}\n"
        f"NEXTCLOUD_ADMIN_PASSWORD={admin_password}\n"
    )
    try:
        env_path.write_text(content)
        env_path.chmod(0o600)
        log.info("[INSTALL] Credentials written to /root/.nextcloud-installer.env (chmod 600).")
    except OSError as e:
        log.warning("[INSTALL] WARNING: could not write credentials file — credentials displayed above. " + str(e))


def main() -> None:
    args = parse_args()

    # Step 0: open the debug log before anything else touches the system (REQ-5).
    log.init(_LOG_PATH)

    # REQ-21: --reset short-circuits the install flow entirely.
    if args.reset:
        reset.run_all(
            db_name=args.db_name,
            db_user=args.db_user,
            web_root=Path(args.web_root),
            data_dir=Path(args.data_dir),
            work_dir=_WORK_DIR,
        )
        return

    # Step 1: detect OS before touching anything.
    log.info("[DETECT] Detecting operating system ...")
    os_info = detect.detect()
    log.info(
        f"[DETECT] {os_info.system} / {os_info.distro} {os_info.version} ({os_info.arch})"
        + (" [scaffolded]" if os_info.scaffolded else "")
        + ("" if os_info.supported or os_info.scaffolded else " [unsupported]")
    )

    # Step 2: pre-flight — exits with a distinct code if checks fail (REQ-1, REQ-2).
    preflight.run_all(os_info)

    # Step 3: resolve PHP version and install required packages (REQ-3, REQ-4).
    pm = pkg.make(os_info)
    php_ver = deps.run_all(pm, os_info)

    # Step 4: download and verify the Nextcloud tarball (REQ-6, REQ-7).
    web_root = Path(args.web_root)
    version, tarball_path = download.run_all(args.nextcloud_version, _WORK_DIR)

    # Step 5: extract tarball into web root (REQ-6).
    download.extract_tarball(tarball_path, web_root)

    # Steps 6 + 7: DB setup and occ install.
    # The install state check must happen before any password is generated.
    # INSTALLED   → skip both steps; never touch the DB or config.php.
    # NOT_INSTALLED → fresh install path below.
    # UNKNOWN     → occ errored (DB down, PHP error, etc.); abort rather than
    #               silently treating an error as "not installed" and overwriting
    #               an existing instance.
    db_password = None
    install_state = occ.check_install_state(web_root)
    if install_state is occ.InstallState.INSTALLED:
        log.ok("[OCC] Nextcloud is already installed — skipping DB setup and occ install.")
        if args.db_password:
            log.info(
                "[DB] Instance already installed — --db-password supplied but ignored; "
                "existing credentials in config.php remain authoritative."
            )
        admin_user = args.admin_user
        admin_password = None
        log.info("[INSTALL] Instance already installed — credentials in config.php are authoritative. Skipping .env write.")
        admin_pw_generated = False
    elif install_state is occ.InstallState.UNKNOWN:
        log.error(
            "[OCC] ERROR: 'occ status' did not return a clean result after extraction.  "
            "The database may be unreachable, PHP may have errored, or file permissions "
            "may be wrong.  Cannot safely determine install state — aborting rather than "
            "risking a reinstall over an existing instance.  "
            "Run with --reset to tear down and retry from scratch."
        )
        sys.exit(EXIT_OCC)
    else:
        # Step 6: create the Nextcloud database and user (REQ-8, REQ-9).
        db_password = _resolve_db_password(args)
        db.run_all(args.db_name, args.db_user, db_password)

        # Step 7: run occ maintenance:install (REQ-10, REQ-11).
        admin_user, admin_password, admin_pw_generated = _resolve_admin_password(args)
        occ.run_all(
            web_root=web_root,
            db_name=args.db_name,
            db_user=args.db_user,
            db_password=db_password,
            db_host=db._DEFAULT_DB_HOST,
            admin_user=admin_user,
            admin_password=admin_password,
            data_dir=Path(args.data_dir),
        )

    # Step 7b: install or enable optional apps (REQ-13) — runs regardless of install state.
    occ.enable_apps(web_root / "occ", args.apps)

    # Step 8: configure TLS and nginx — self-signed (REQ-14) or Let's Encrypt (REQ-15).
    tls.run_all(
        web_root=web_root,
        php_ver=php_ver,
        server_name=args.domain,
        email=args.email,
    )

    # Step 9: post-install verification — occ status (REQ-16) + HTTP check (REQ-17).
    verify.run_all(web_root=web_root, version=version, server_name=args.domain)

    # Log run completion before the credential banner so the log record is clean.
    log.ok("[INSTALL] Run completed successfully.")
    _write_credentials_env(db_password, admin_user, admin_password)

    # REQ-12: display the generated admin password once at the very end of the run.
    if admin_pw_generated:
        _print_admin_credentials(admin_user, admin_password)


if __name__ == "__main__":
    main()
