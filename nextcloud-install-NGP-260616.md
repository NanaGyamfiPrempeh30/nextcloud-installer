# Nextcloud CLI Installer — Requirements Specification

**File:** nextcloud-install-NGP-260616.md
**Author:** Yaw (NGP)
**Date:** 2026-06-16 (updated 2026-06-20)
**Status:** v2 — implementation complete. Proven on Ubuntu 24.04 / WSL2: core install (REQ-3, REQ-5 through REQ-17, REQ-22 through REQ-26), idempotency re-run, DB-failure path (EXIT_DATABASE=40), full reset-reinstall cycle, optional app install/enable (REQ-13 — fresh install path, idempotent re-run skip, new app on already-installed instance, and single-bad-app-ID failure path all exercised), and ONLYOFFICE Document Server integration (REQ-26 — connector-only, BYO server; app install via three-state routing, config:app:set fatal on failure, connectivity check non-fatal with unconditional stdout/stderr logging; tested fresh install against unreachable server, idempotent re-run, no-flag regression). Not yet exercised by direct test: REQ-1 (unsupported-OS exit), REQ-2 (not-root exit), REQ-15 (Let's Encrypt real-domain flow — only self-signed was exercised). Proven by direct test (Ubuntu 22.04, 2025-06-26): REQ-4 (PHP PPA fallback — ondrej/php added, PHP 8.4 installed from scratch on clean 22.04 distro); REQ-24 (stale config.php recovery — _clear_partial_config() fired and cleared partial config from DB-fail run, maintenance:install proceeded cleanly).
**Collaboration:** Wilson Mar — Nextcloud CLI installer (eventual commercial install package)

## 1. Purpose

A CLI script that performs a fully unattended, idempotent install and configuration of a working Nextcloud instance on Linux, with macOS scaffolded for a later pass. No web wizard, no interactive prompts at any point in the supported path.

## 2. Architecture Decision

**Decision:** Native stack (nginx + PHP-FPM + MariaDB + Redis), installed and configured via Nextcloud's own `occ maintenance:install --no-interaction`. NOT the Docker AIO method.

**Rationale:** AIO's post-container configuration (domain, add-ons, timezone) is GUI-wizard-only on every platform it runs on, Linux included, not just macOS. That makes "automated configuration" impossible to deliver through AIO. The native stack is the only path where every step, including configuration, is scriptable end-to-end via flags and environment variables. Confirmed against Nextcloud's own AIO docs and an unresolved community request for headless AIO setup.

## 3. Language & Structure

**Python 3.9+** (ships by default on all target OSes). Bash was considered to match the style of existing K8s automation scripts, but rejected: macOS ships bash 3.2 by default (frozen since 2007, GPLv3 licensing), missing associative arrays and other features needed for cross-platform install-state tracking. That's a silent-breakage risk on first real macOS run, not a stylistic nitpick.

Structure: thin CLI entrypoint → OS-detection layer → abstracted package-manager and service-manager interfaces → numbered, idempotent install steps → verification phase → summary report. Numbered steps and explicit error messages follow the same pattern as the kubeadm Ansible playbook and Kubernetes Troubleshooting.sh conventions already in use.

## 4. Platform Support (v1)

| OS | Status |
|---|---|
| Ubuntu 24.04 LTS | **Tested and verified.** Idempotency, real DB-failure path (EXIT_DATABASE=40), and full reset-reinstall cycle all proven on Ubuntu 24.04 / WSL2. |
| Ubuntu 22.04 LTS | Implemented, not yet tested. |
| Debian 12 | Fully implemented (apt, systemd), not yet tested. |
| macOS (Apple Silicon, Homebrew) | **Scaffolded only.** OS detection exists; package/service function bodies are named stubs that exit with a clear "not yet implemented" message (see REQ-1) rather than attempting partial logic. This lets a later pass fill in Homebrew/launchd-specific bodies without restructuring control flow. Any macOS implementation must be executed and verified on real macOS hardware before being trusted — Homebrew's nginx/PHP-FPM/MariaDB paths, socket locations, and launchd service management differ enough from systemd that static review won't catch the real bugs. |

## 5. Component Defaults (override if you disagree)

- **Web server:** nginx + PHP-FPM — lower resource footprint than Apache, matters on constrained dev/free-tier environments.
- **Database:** MariaDB.
- **Cache/locking:** Redis.
- **PHP version:** resolved dynamically against the target Nextcloud release's documented minimum; falls back to the `ondrej/php` PPA on Ubuntu if the distro-default package is too old.
- **Nextcloud source:** official tarball from `download.nextcloud.com`, verified against its published SHA256 checksum before extraction. Non-negotiable — this script may run unattended on systems that will hold real user data.

## 6. Functional Requirements (EARS format)

REQ numbers reflect implementation order of addition, not pipeline position. REQ-22 through REQ-25 were added during implementation; each is placed in the subsection matching its pipeline position.

### 6.1 Pre-flight
- **REQ-1:** WHEN the script starts, THE SYSTEM SHALL detect OS, version, and architecture, and SHALL exit with a clear, distinct message before making any changes if the platform is unsupported or only scaffolded (e.g. macOS in v1).
- **REQ-2:** WHEN run without root/sudo access, THE SYSTEM SHALL exit immediately with a clear message rather than failing midway through a privileged step.

### 6.2 Dependencies
- **REQ-3:** THE SYSTEM SHALL check whether each required package (nginx, PHP-FPM + required extensions including `php{ver}-redis`, MariaDB, Redis server) is already installed and at a compatible version before attempting installation, and SHALL skip reinstallation if already satisfied. The full required package list comprises 16 packages as of v2; `php{ver}-redis` (the PHP Redis extension required by REQ-22) was added during implementation, raising the count from the original 15.
- **REQ-4:** IF the distro-default PHP version is below Nextcloud's documented minimum, THEN THE SYSTEM SHALL add the appropriate repository before installing PHP.
- **REQ-5:** WHEN installing any package, THE SYSTEM SHALL log the action and its result to the run's debug log.

### 6.3 Download, verify & extract
- **REQ-6:** THE SYSTEM SHALL download the Nextcloud release tarball for the requested (or latest stable) version and SHALL verify it against the published SHA256 checksum before extracting it.
- **REQ-7:** IF checksum verification fails, THEN THE SYSTEM SHALL abort immediately without extracting or installing anything.
- **REQ-23:** THE SYSTEM SHALL extract the verified tarball into the configured web root, set `www-data:www-data` ownership recursively, apply directory permissions 750 and file permissions 640, and SHALL be idempotent — if `{web_root}/occ` already exists, extraction SHALL be skipped without error. On extraction failure, THE SYSTEM SHALL exit with EXIT_OCC and log the result per REQ-5.

### 6.4 Database setup
- **REQ-8:** THE SYSTEM SHALL create the Nextcloud database and database user only if they do not already exist (idempotent, `IF NOT EXISTS` guards), and SHALL NOT error on re-run. If the user already exists, THE SYSTEM SHALL update its password to the current run's value via `ALTER USER` and SHALL verify the connection succeeds before proceeding.
- **REQ-9:** WHEN a database password is not supplied via flag or environment variable, THE SYSTEM SHALL generate a strong random one by default, and SHALL NOT print it to stdout after initial setup.

### 6.5 Installation (occ)
- **REQ-10:** THE SYSTEM SHALL probe install state via `occ status --output=json` before running `occ maintenance:install`, resolving to one of three states: **INSTALLED** (occ exited 0 and reported `installed: true` — skip the entire occ block and proceed to TLS), **NOT_INSTALLED** (occ exited 0 and reported `installed: false` — proceed to maintenance:install), or **UNKNOWN** (occ binary missing, non-zero exit, timeout, or unparseable JSON). On UNKNOWN, THE SYSTEM SHALL abort with EXIT_OCC and SHALL NOT treat an occ error as equivalent to "not installed". Rationale: the original boolean probe collapsed NOT_INSTALLED and UNKNOWN into a single False return, creating a real risk — a transient DB failure or PHP error during a re-run would be misread as "not installed" and trigger a destructive reinstall attempt against a working instance.
- **REQ-24:** WHEN the install state resolves to NOT_INSTALLED and `config/config.php` already exists in the web root, THE SYSTEM SHALL inspect its content before invoking maintenance:install: if `installed => true` is absent or false, THE SYSTEM SHALL remove the file so maintenance:install starts clean rather than loading stale DB credentials from it; if `installed => true` is present, THE SYSTEM SHALL abort with EXIT_OCC rather than deleting the file, since that value contradicts the NOT_INSTALLED probe and the system cannot safely determine the actual state. Background: Nextcloud's maintenance:install reads an existing config.php before applying CLI arguments, so a stale file with a mismatched `dbpassword` causes Access Denied even when the MariaDB user has the correct new password.
- **REQ-11:** THE SYSTEM SHALL run `occ maintenance:install --no-interaction` with database type, name, credentials, admin user, and admin password supplied as flags or environment variables — no interactive prompts at any point. All credential arguments SHALL use `--option=value` syntax (not `--option value`) because `secrets.token_urlsafe()` can produce values starting with `-`, which Symfony Console would misparse as option flags rather than values.
- **REQ-25:** WHEN `occ maintenance:install` exits non-zero and its stdout contains "Login is invalid because files already exist for this user", THE SYSTEM SHALL emit an actionable message directing the operator to run `--reset` before retrying, and SHALL NOT auto-delete any data-directory contents. Data-directory cleanup is intentionally manual via `--reset` only — the directory may contain real user data in other failure contexts, and silent deletion was judged unsafe. Background: Nextcloud's `createUser()` writes the admin user's home directory under the data dir before `installed: true` is set in config.php; a crash or error between those two steps leaves both a partial config.php (cleaned by REQ-24 on the next run) and leftover data-dir files (not cleaned automatically, detected by this check).
- **REQ-12:** WHEN an admin password is not supplied, THE SYSTEM SHALL generate one, display it once at the end of the run, and SHALL instruct the user to store it securely.
- **REQ-13:** AFTER core install completes, THE SYSTEM SHALL enable any requested optional apps via `occ app:enable`.
- **REQ-22:** AFTER `occ maintenance:install` succeeds on a NOT_INSTALLED install, THE SYSTEM SHALL wire Nextcloud to Redis by running `occ config:system:set` for `memcache.local` (`\OC\Memcache\Redis`), `memcache.locking` (`\OC\Memcache\Redis`), `redis host` (`127.0.0.1`), and `redis port` (`6379`, integer type). THE SYSTEM SHALL then read back `memcache.local` via `occ config:system:get` to confirm the value was applied and SHALL NOT trust the set commands' exit codes alone. **Transport:** TCP (127.0.0.1:6379) is used because target systems run Redis without a configured unix socket by default; reconfiguring Redis itself was judged unnecessary complexity. **Known limitation:** this step fires only on a fresh NOT_INSTALLED install. An instance installed before REQ-22 was added will not be retroactively configured by a later re-run, because the INSTALLED gate in REQ-10 skips the entire occ block including this step.

### 6.6 TLS / domain
- **REQ-14:** BY DEFAULT, THE SYSTEM SHALL configure a self-signed certificate for local verification, requiring no public domain.
- **REQ-15:** IF a real domain and public IP are supplied, THEN THE SYSTEM SHALL run the existing Let's Encrypt HTTP-01 flow (reusing the IONOS pattern) instead of self-signing.

### 6.7 Verification
- **REQ-16:** AFTER installation, THE SYSTEM SHALL run `occ status --output=json` and parse it to confirm `installed: true` and a matching version string.
- **REQ-17:** THE SYSTEM SHALL perform an HTTP(S) request to the configured instance and confirm a successful (2xx/3xx) response before reporting overall success.
- **REQ-18:** IF any verification check fails, THE SYSTEM SHALL report exactly which check failed and SHALL NOT report overall success.

The verification phase also enforces REQ-22's Redis wiring on every run (not just fresh installs): `occ config:system:get memcache.local` must return `\OC\Memcache\Redis` for the run to reach the success log line. This catches any regression where the Redis wiring step is removed from the install pipeline while allowing the rest of the run to proceed silently.

### 6.8 Error handling & logging
- **REQ-19:** THE SYSTEM SHALL maintain a DEBUG_LOG.md-style log of every action and its outcome, per existing project convention.
- **REQ-20:** THE SYSTEM SHALL use distinct exit codes per failure category (dependency, checksum, database, occ install, verification) rather than a single generic non-zero exit, so failures are scriptable and distinguishable.
- **REQ-21:** THE SYSTEM SHALL provide a `--reset` flag that cleanly removes a partial/failed install (stop services, drop any DB this script created, remove web root, data directory, TLS certificates, and nginx configuration) so a retry starts clean. The database drop SHALL execute before services are stopped, since MariaDB must be running to execute `DROP DATABASE`. `--reset` is scoped to this installer's own Nextcloud install state (database, web root, TLS, nginx site config, data dir) — it does not remove or downgrade system dependencies (PHP, MariaDB, Redis, nginx itself) installed by REQ-3/4, since those may be relied on by other software on the host.

## 7. Out of Scope (v1)

- Full macOS implementation (scaffolded only — Section 4)
- Windows
- Multi-node / HA Nextcloud deployments
- Docker AIO support
- Automatic post-install version upgrades

## 8. Open Risks — Verify Before/During Build

- Exact minimum PHP version for the target Nextcloud release — confirm against current release notes before locking the version matrix.
- ~~MariaDB root auth on a fresh install (`auth_socket` plugin) — confirm the non-interactive path on both Ubuntu 22.04 and 24.04; defaults differ slightly between them.~~ **CLOSED:** Confirmed working as-is on Ubuntu 24.04 via unix socket auth (`sudo mysql -u root` as root, no password needed); no special handling required.
- ~~Redis socket vs TCP default — pick one and document it, since Nextcloud's `config.php` must match.~~ **CLOSED:** Confirmed TCP-only on tested systems — default Redis install has no `unixsocket` configured in `/etc/redis/redis.conf`. Decision documented in REQ-22: TCP (127.0.0.1:6379), no Redis-side reconfiguration needed.

## 9. Acceptance Criteria

On a clean Ubuntu 22.04 and 24.04 VM, the script passes if:
1. First run completes with `installed: true` and a successful HTTP check, zero interactive prompts.
2. Second run on the same machine completes without error and without redoing already-complete steps (idempotency proof).
3. A deliberately broken run — e.g. MariaDB service masked/unreachable before the install reaches the database step — fails with EXIT_DATABASE=40, names the database step as the failure point, and does not log "Run completed successfully". Note: "wrong DB password" is not a valid test under this architecture — the installer sets the database password on first creation rather than validating against a pre-existing one, so any supplied password becomes authoritative. `--reset` cleanly returns the machine to a pre-install state: database, database user, web root, data directory, TLS certificates, and nginx configuration all removed and confirmed absent.

## 10. Bugs Found and Fixed During Implementation

All seven were discovered on real Ubuntu 24.04 / WSL2 runs, not in review.

### Bug 1 — bzip2 not present
**Root cause:** The installer originally used a shell `tar` call to extract the `.tar.bz2` tarball. Ubuntu 24.04 minimal images do not ship `bzip2`, so `tar` silently failed or exited non-zero.
**Fix:** Replaced the shell subprocess with Python's stdlib `tarfile` module, which handles bzip2 natively and requires no external binary.

### Bug 2 — MariaDB not running after package install
**Root cause:** `apt-get install mariadb-server` installs but does not always start the service, and Nextcloud's DB setup step requires a live MariaDB socket. The installer proceeded to the DB step with MariaDB stopped.
**Fix:** Added `_start_services()` in `deps.py`, called at the end of every `deps.run_all()` pass (including re-runs where packages were already present). Uses `systemctl enable --now` for mariadb, redis-server, and php{ver}-fpm.

### Bug 3 — Re-run breaks DB credentials / reset ordering
**Root cause (a):** On re-run, `_resolve_db_password()` generated a new random password. `db.run_all()` used `CREATE USER IF NOT EXISTS`, which is a no-op if the user already exists and does not update the password. The new password was written into config.php by maintenance:install but MariaDB still had the old password — causing Access Denied on every re-run.
**Fix (a):** Added `ALTER USER … IDENTIFIED BY …` after the `CREATE USER IF NOT EXISTS` statement so the MariaDB password is always synchronised to the current run's value. Added `_verify_connection()` using `MYSQL_PWD` env var (keeps password out of argv) to confirm the connection works before proceeding.

**Root cause (b):** `reset.run_all()` originally stopped services (including MariaDB) before calling `db.drop_all()`. With MariaDB stopped, the `DROP DATABASE` statement failed silently, leaving the old database and user in place. On the next install run, the stale user still had the old password, causing Access Denied.
**Fix (b):** Reordered `reset.run_all()` to call `db.drop_all()` first, then `_stop_services()`.

### Bug 4 — Boolean install-state probe
**Root cause:** `is_installed()` returned `False` for both "occ cleanly reports not installed" and "occ errored / timed out / exited non-zero". A transient DB failure during a re-run would therefore be treated as "not installed" and trigger a destructive reinstall attempt against a working instance.
**Fix:** Replaced the boolean with a three-state `InstallState` enum (INSTALLED / NOT_INSTALLED / UNKNOWN). The caller in `install.py` aborts on UNKNOWN rather than proceeding. Documented in REQ-10.

### Bug 5 — Partial config.php poisons a retry
**Root cause:** A failed `maintenance:install` run writes `config/config.php` with the DB password and `installed: false` before it aborts. Nextcloud loads this file at PHP bootstrap, before CLI arguments are applied. On the next run, maintenance:install would pick up the stale `dbpassword` from config.php instead of the new password passed via `--database-pass`, causing Access Denied.
**Fix:** Added `_clear_partial_config()` (REQ-24): called when the install state is NOT_INSTALLED, it deletes config.php if `installed => true` is absent, so maintenance:install starts from a clean slate.

### Bug 6 — Stale data-dir files produce a cryptic occ error
**Root cause:** If a previous `maintenance:install` succeeded past `createUser()` (which writes the admin home directory under the data dir) but failed before setting `installed: true`, both a partial config.php and data-dir files were left behind. `_clear_partial_config()` removed the config.php, but the data-dir files caused the next maintenance:install to fail with "Login is invalid because files already exist for this user" — with no indication of the cause or remedy.
**Fix:** In `_maintenance_install()`, the error path now checks stdout for that specific string and emits an actionable message: "stale admin user data found in the data directory from a previous incomplete install — run with --reset to clean up, then retry." The data dir is not auto-deleted (REQ-25). Documented in REQ-25.

### Bug 7 — Redis installed but never wired into Nextcloud
**Root cause:** The installer installed and started the `redis-server` package, but never added the Nextcloud-side configuration (`memcache.local`, `memcache.locking`, `redis.host`, `redis.port`) to config.php. Nextcloud ran without any cache backend.
**Fix:** Added REQ-22: a `_configure_redis()` step after maintenance:install succeeds, using `occ config:system:set` for all four keys and reading back `memcache.local` via `occ config:system:get` to confirm. Also added `php{ver}-redis` to the package list (REQ-3), raising the count from 15 to 16, since Nextcloud's `config:system:set` validates that the PHP Redis extension is loaded before accepting the memcache backend value. A verify-phase check (`check_redis()` in `verify.run_all()`) enforces the wiring on every run, so a future regression cannot silently pass.
