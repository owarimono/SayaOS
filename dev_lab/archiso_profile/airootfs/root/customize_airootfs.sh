#!/usr/bin/env bash
# SayaOS build hook: archiso runs this inside the airootfs chroot.
# Creates runtime directories and makes the CLI executable.
# The ledger and the /usr/bin/saya wrapper are committed files, not
# generated here — one source per file.

set -uo pipefail

mkdir -p /etc/saya/backups /var/saya/jails
chmod 755 /usr/bin/saya_cli.py /usr/bin/saya
