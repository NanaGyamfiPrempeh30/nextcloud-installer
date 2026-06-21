# nextcloud-installer

A fully unattended, idempotent CLI installer for Nextcloud on native Linux stacks (nginx + PHP-FPM + MariaDB + Redis). No Docker, no web setup wizard — every step, including configuration, runs non-interactively via Nextcloud's own `occ` CLI.

## Why native stack, not Docker AIO

Nextcloud's official AIO (All-In-One) Docker image requires a web-based setup wizard for domain, add-ons, and timezone configuration on every platform it runs on — that step can't be scripted. This installer uses `occ maintenance:install --no-interaction` instead, so the entire install, including configuration, is genuinely automatable end to end.

## Status

| Platform | Status |
|---|---|
| Ubuntu 24.04 LTS | Tested and verified — full install, idempotent re-run, DB-unreachable failure path, full reset-reinstall cycle all proven |
| Ubuntu 22.04 LTS | Implemented, not yet tested |
| Debian 12 | Implemented, not yet tested |
| macOS | Scaffolded only — package/service functions are named stubs, no working implementation yet |

Not yet exercised by direct test on any platform: unsupported-OS exit (REQ-1), not-root exit (REQ-2), the PHP repository fallback path (REQ-4 — distro PHP has met the minimum on every test run so far), optional app installation (REQ-13), and the Let's Encrypt real-domain flow (REQ-15 — only self-signed has been exercised).

Full requirements and acceptance criteria: [`nextcloud-install-NGP-260616.md`](./nextcloud-install-NGP-260616.md)

## Usage

```bash
sudo python3 install.py --nextcloud-version 33.0.5
```

Database and admin passwords are auto-generated if not supplied (`--db-password`, `--admin-password`, or `NEXTCLOUD_DB_PASSWORD` / `NEXTCLOUD_ADMIN_PASSWORD` env vars). The generated admin password is shown once at the end of the run — store it immediately, it will not be shown again.

Other common flags:

```bash
--domain example.com --email admin@example.com   # real domain → triggers Let's Encrypt
--apps contacts calendar tasks                     # optional apps to enable post-install
--web-root /custom/path --data-dir /custom/data
--reset                                             # tear down this installer's own Nextcloud
                                                     # state (DB, web root, TLS, nginx config,
                                                     # data dir) for a clean retry
```

`--reset` does not remove or downgrade system dependencies (PHP, MariaDB, Redis, nginx itself) — those are left in place since other software on the host may depend on them. It only resets the Nextcloud-specific state this installer created.

## Pipeline

detect → preflight → dependencies → download + checksum verify → extract → database setup → `occ maintenance:install` → Redis cache wiring → TLS (self-signed by default, Let's Encrypt if a real domain is supplied) → verification (`occ status` + HTTP check)

Every step is idempotent — re-running on an already-completed install skips work rather than erroring or repeating it. Failures exit with distinct codes per category (dependency, checksum, database, occ install, TLS, verify) so failures are scriptable and distinguishable.

## Known design notes

- The install-state check uses a three-state model (`INSTALLED` / `NOT_INSTALLED` / `UNKNOWN`), not a boolean — a transient failure (e.g. database temporarily unreachable) reports `UNKNOWN` and aborts rather than being misread as "not installed," which would otherwise risk a destructive reinstall attempt against a working instance.
- A failed `maintenance:install` run can leave a stale `config.php` or, in a narrower window, leftover admin user files in the data directory. Both are detected on retry with an actionable error pointing to `--reset` rather than a raw, unexplained failure.

See `DEBUG_LOG.md` (generated at runtime, gitignored — contains generated credentials) for a full run log, and the spec's "Bugs Found and Fixed During Implementation" section for the real issues caught by executing this on actual hardware rather than by code review alone.
