"""MariaDB setup — REQ-8."""

import os
import subprocess
import sys

from . import log
from .exitcodes import EXIT_DATABASE

_DEFAULT_DB_NAME = "nextcloud"
_DEFAULT_DB_USER = "nextcloud"
_DEFAULT_DB_HOST = "localhost"


def _escape(s: str) -> str:
    """Minimal MySQL string escaping — backslash then single-quote."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _mysql(sql: str) -> subprocess.CompletedProcess:
    """Run SQL as root via unix socket (MariaDB default on Ubuntu/Debian).

    SQL is passed via stdin so credentials never appear in the process list.
    Open risk (spec Section 8): auth_socket behaviour differs slightly between
    Ubuntu 22.04 and 24.04 — confirm on each target before shipping.
    """
    return subprocess.run(
        ["mysql", "-u", "root", "--batch", "--skip-column-names"],
        input=sql,
        capture_output=True,
        text=True,
    )


def _verify_connection(
    db_name: str,
    db_user: str,
    db_password: str,
    db_host: str,
) -> None:
    """Test that the created user can actually connect with the given password.

    Uses MYSQL_PWD so the password doesn't appear in the process list.
    Exits EXIT_DATABASE immediately if the connection fails, so a password
    mismatch surfaces here rather than deep inside occ maintenance:install.
    """
    env = {**os.environ, "MYSQL_PWD": db_password}
    r = subprocess.run(
        ["mysql", "-u", db_user, "-h", db_host, db_name, "-e", "SELECT 1"],
        env=env,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        log.error(
            f"[DB] ERROR: connection test as '{db_user}'@'{db_host}' failed "
            f"(password mismatch or auth issue):\n{r.stderr.strip()}"
        )
        sys.exit(EXIT_DATABASE)
    log.ok(f"[DB] Connection test as '{db_user}'@'{db_host}' passed.")


def run_all(
    db_name: str,
    db_user: str,
    db_password: str,
    db_host: str = _DEFAULT_DB_HOST,
) -> None:
    """REQ-8: Create the Nextcloud database and user with IF NOT EXISTS guards.

    Safe to call on a machine where the setup has already completed — the
    IF NOT EXISTS clauses make every statement a no-op when the object exists.
    GRANT is idempotent in MariaDB; FLUSH PRIVILEGES is always safe to run.
    """
    log.info(
        f"[DB] Setting up database '{db_name}' and user '{db_user}'@'{db_host}' ..."
    )

    sql = (
        f"CREATE DATABASE IF NOT EXISTS `{db_name}`"
        f" CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;\n"
        f"CREATE USER IF NOT EXISTS '{_escape(db_user)}'@'{_escape(db_host)}'"
        f" IDENTIFIED BY '{_escape(db_password)}';\n"
        # ALTER USER ensures the password is current even when the user pre-existed
        # (CREATE USER IF NOT EXISTS is a no-op on existing users, not a password update).
        f"ALTER USER '{_escape(db_user)}'@'{_escape(db_host)}'"
        f" IDENTIFIED BY '{_escape(db_password)}';\n"
        f"GRANT ALL PRIVILEGES ON `{db_name}`.*"
        f" TO '{_escape(db_user)}'@'{_escape(db_host)}';\n"
        "FLUSH PRIVILEGES;\n"
    )

    r = _mysql(sql)
    if r.returncode != 0:
        log.error(f"[DB] ERROR: MariaDB setup failed:\n{r.stderr.strip()}")
        sys.exit(EXIT_DATABASE)

    _verify_connection(db_name, db_user, db_password, db_host)
    log.ok(
        f"[DB] Database '{db_name}' and user '{db_user}'@'{db_host}' are ready."
    )


def drop_all(
    db_name: str,
    db_user: str,
    db_host: str = _DEFAULT_DB_HOST,
) -> None:
    """REQ-21: Drop the database and user created by run_all().

    Uses IF EXISTS guards so it is safe to call on a partially-created setup.
    Logs a warning on failure but does not exit — reset should continue even
    if the DB step fails (e.g. MariaDB not yet installed).
    """
    log.info(
        f"[DB] Dropping database '{db_name}' and user '{db_user}'@'{db_host}' ..."
    )
    sql = (
        f"DROP DATABASE IF EXISTS `{db_name}`;\n"
        f"DROP USER IF EXISTS '{_escape(db_user)}'@'{_escape(db_host)}';\n"
        "FLUSH PRIVILEGES;\n"
    )
    r = _mysql(sql)
    if r.returncode != 0:
        log.error(f"[DB] WARNING: drop failed (continuing reset):\n{r.stderr.strip()}")
    else:
        log.ok(f"[DB] Database '{db_name}' and user '{db_user}'@'{db_host}' removed.")
