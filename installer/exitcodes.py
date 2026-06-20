"""Installer exit codes — REQ-20.

All failure paths exit with a code from this table so the result is
scriptable and distinguishable by calling shell scripts or CI pipelines.

  Code  Name                Category
  ----  ------------------  ----------------------------------------
    10  EXIT_UNSUPPORTED_OS OS not in the supported matrix
    11  EXIT_SCAFFOLDED_OS  OS detected but installer not yet implemented
    12  EXIT_NOT_ROOT       Script not running as root/sudo
    20  EXIT_DEPENDENCY     Package install or repository operation failed
    30  EXIT_CHECKSUM       Tarball download or SHA256 verification failed
    40  EXIT_DATABASE       MariaDB create/grant operation failed
    50  EXIT_OCC            occ maintenance:install or app:enable failed
    55  EXIT_TLS            TLS certificate or nginx configuration failed
    60  EXIT_VERIFY         Post-install verification (occ status or HTTP) failed
"""

# Pre-flight
EXIT_UNSUPPORTED_OS = 10
EXIT_SCAFFOLDED_OS  = 11
EXIT_NOT_ROOT       = 12

# Package installation
EXIT_DEPENDENCY = 20

# Download and checksum verification
EXIT_CHECKSUM = 30

# Database setup
EXIT_DATABASE = 40

# Nextcloud occ install
EXIT_OCC = 50

# TLS and nginx configuration
EXIT_TLS = 55

# Post-install verification
EXIT_VERIFY = 60
