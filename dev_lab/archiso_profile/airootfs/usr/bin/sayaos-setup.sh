#!/usr/bin/env bash
# ============================================================
# SayaOS Setup Script — runs inside airootfs during build
# Sets up saya_cli, creates needed directories, configures
# ============================================================

set -uo pipefail

# Create saya_cli directories
mkdir -p /etc/saya/backups
mkdir -p /var/saya/jails

# Make saya_cli executable
chmod 755 /usr/bin/saya_cli.py 2>/dev/null || true

# Create /etc/saya/pkg_db.json with empty initial state
cat > /etc/saya/pkg_db.json << 'DBEOF'
{
    "version": "1.0.0",
    "jails": {},
    "packages": {},
    "aliases": {}
}
DBEOF

# Add saya alias for convenience
cat > /usr/bin/saya << 'ALIAS'
#!/usr/bin/env python3
import sys, subprocess
args = ["saya_cli.py"] + sys.argv[1:]
sys.exit(subprocess.run(args).returncode)
ALIAS
chmod 755 /usr/bin/saya

echo "[sayaos-setup] SayaOS setup complete."
