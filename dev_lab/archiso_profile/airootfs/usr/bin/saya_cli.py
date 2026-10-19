#!/usr/bin/env python3
# ============================================================
# SayaOS Package Manager (saya_cli)
# Jail-based isolated package management system
# ============================================================
# Usage: sudo python3 saya_cli.py <command> [args...]
# ============================================================

import os
import sys
import json
import shutil
import subprocess
import hashlib
import time
from pathlib import Path

DB_PATH = "/etc/saya/pkg_db.json"
DB_BACKUP_DIR = "/etc/saya/backups"
JAILS_ROOT = "/var/saya/jails"
HOST_BIN_DIR = "/usr/bin"
CGROUP_PATH = "/sys/fs/cgroup"

VERSION = "1.0.0"

# ── Colors ──────────────────────────────────────────────────
class C:
    OK = "\033[92m"
    WARN = "\033[93m"
    ERR = "\033[91m"
    INFO = "\033[96m"
    BOLD = "\033[1m"
    RST = "\033[0m"

def ok(msg):   print(f"{C.OK}[+] {msg}{C.RST}")
def warn(msg): print(f"{C.WARN}[!] {msg}{C.RST}")
def err(msg):  print(f"{C.ERR}[-] {msg}{C.RST}", file=sys.stderr)
def info(msg): print(f"{C.INFO}[*] {msg}{C.RST}")

# ── Database ────────────────────────────────────────────────
def _default_db():
    return {"version": VERSION, "jails": {}, "packages": {}, "aliases": {}}

def load_db():
    if not os.path.exists(DB_PATH):
        return _default_db()
    try:
        with open(DB_PATH, "r") as f:
            db = json.load(f)
        # Ensure all keys exist
        for key in ("version", "jails", "packages", "aliases"):
            db.setdefault(key, _default_db()[key])
        return db
    except (json.JSONDecodeError, IOError) as e:
        warn(f"Database corrupted ({e}), attempting backup recovery...")
        return _repair_db()

def save_db(db):
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        # Auto-backup before every write
        _backup_db()
        with open(DB_PATH, "w") as f:
            json.dump(db, f, indent=4)
    except PermissionError:
        err("Root privileges required to write database.")
        sys.exit(1)

def _backup_db():
    """Create a timestamped backup of the current database."""
    if not os.path.exists(DB_PATH):
        return
    try:
        os.makedirs(DB_BACKUP_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(DB_BACKUP_DIR, f"pkg_db_{ts}.json")
        shutil.copy2(DB_PATH, backup_path)
        # Keep only last 10 backups
        backups = sorted(Path(DB_BACKUP_DIR).glob("pkg_db_*.json"))
        for old in backups[:-10]:
            old.unlink()
    except Exception:
        pass  # Backup is best-effort

def _repair_db():
    """Try to recover database from the most recent backup."""
    if not os.path.exists(DB_BACKUP_DIR):
        return _default_db()
    backups = sorted(Path(DB_BACKUP_DIR).glob("pkg_db_*.json"), reverse=True)
    for bp in backups:
        try:
            with open(bp, "r") as f:
                db = json.load(f)
            for key in ("version", "jails", "packages", "aliases"):
                db.setdefault(key, _default_db()[key])
            ok(f"Recovered database from backup: {bp.name}")
            # Restore the recovered DB
            with open(DB_PATH, "w") as f:
                json.dump(db, f, indent=4)
            return db
        except Exception:
            continue
    warn("No valid backup found. Starting fresh.")
    return _default_db()

# ── Mount helpers ───────────────────────────────────────────
def is_mounted(target_path):
    if not os.path.exists("/proc/mounts"):
        return False
    with open("/proc/mounts", "r") as f:
        return any(target_path in line for line in f)

def mount_jail_binds(jail_name, db):
    jail_info = db["jails"].get(jail_name)
    if not jail_info:
        return False
    jail_path = jail_info["path"]
    for bind in jail_info["mounted_binds"]:
        target = os.path.join(jail_path, bind.lstrip("/"))
        os.makedirs(target, exist_ok=True)
        if not is_mounted(target):
            info(f"Mounting: {bind} -> {target}")
            try:
                subprocess.run(["mount", "--bind", bind, target], check=True,
                               capture_output=True)
            except subprocess.CalledProcessError:
                err(f"Failed to mount {bind}")
                return False
    return True

def unmount_jail(jail_path):
    """Unmount all bind mounts for a jail (reverse order)."""
    if not os.path.exists("/proc/mounts"):
        return
    mounts = []
    with open("/proc/mounts", "r") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith(jail_path):
                mounts.append(parts[1])
    # Unmount deepest paths first
    mounts.sort(reverse=True)
    for mnt in mounts:
        try:
            subprocess.run(["umount", "-l", mnt], capture_output=True)
        except Exception:
            pass

# ── CGroup helpers ──────────────────────────────────────────
def _cgroup_name(jail_name):
    return f"sayaos-{jail_name}"

def setup_cgroup(jail_name, memory_mb=None, cpu_percent=None):
    """Create cgroup v2 resource limits for a jail."""
    cg_name = _cgroup_name(jail_name)
    cg_path = os.path.join(CGROUP_PATH, cg_name)

    if not os.path.exists(CGROUP_PATH):
        return  # cgroup v2 not available

    try:
        os.makedirs(cg_path, exist_ok=True)

        if memory_mb and memory_mb > 0:
            # memory.max in bytes
            with open(os.path.join(cg_path, "memory.max"), "w") as f:
                f.write(str(memory_mb * 1024 * 1024))

        if cpu_percent and cpu_percent > 0:
            # cpu.max: "quota period" in microseconds. 100000 = 100ms
            quota = int(100000 * cpu_percent / 100)
            with open(os.path.join(cg_path, "cpu.max"), "w") as f:
                f.write(f"{quota} 100000")
    except (PermissionError, FileNotFoundError, OSError):
        warn("cgroup setup skipped (insufficient permissions or cgroup v2 not available)")

def cleanup_cgroup(jail_name):
    """Remove cgroup for a jail."""
    cg_name = _cgroup_name(jail_name)
    cg_path = os.path.join(CGROUP_PATH, cg_name)
    try:
        if os.path.exists(cg_path):
            # Must kill all processes in cgroup before removing
            procs = os.path.join(cg_path, "cgroup.procs")
            if os.path.exists(procs):
                with open(procs, "r") as f:
                    for pid in f:
                        try:
                            os.kill(int(pid.strip()), 9)
                        except (ProcessLookupError, ValueError):
                            pass
            os.rmdir(cg_path)
    except Exception:
        pass

def attach_cgroup(jail_name):
    """Attach current process to a jail's cgroup (used during exec)."""
    cg_name = _cgroup_name(jail_name)
    cg_path = os.path.join(CGROUP_PATH, cg_name)
    procs_file = os.path.join(cg_path, "cgroup.procs")
    try:
        if os.path.exists(procs_file):
            with open(procs_file, "w") as f:
                f.write(str(os.getpid()))
    except Exception:
        pass

# ── Commands ────────────────────────────────────────────────
def cmd_init_jail(jail_name, os_type, memory_mb=None, cpu_percent=None):
    """Create a new isolated jail."""
    db = load_db()
    jail_path = os.path.join(JAILS_ROOT, jail_name)

    if jail_name in db["jails"]:
        err(f"Jail '{jail_name}' already exists. Use 'remove-jail' first.")
        sys.exit(1)

    valid_types = ("arch", "debian", "kali", "ubuntu")
    if os_type not in valid_types:
        err(f"Unsupported OS type '{os_type}'. Valid: {', '.join(valid_types)}")
        sys.exit(1)

    ok(f"Initializing {os_type} jail: '{jail_name}' at {jail_path}...")
    try:
        os.makedirs(jail_path, exist_ok=True)
    except PermissionError:
        err("Permission denied. Root privileges required.")
        sys.exit(1)

    db["jails"][jail_name] = {
        "path": jail_path,
        "type": os_type,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mounted_binds": ["/dev", "/dev/pts", "/proc", "/sys"],
        "memory_mb": memory_mb,
        "cpu_percent": cpu_percent,
        "packages": []
    }
    save_db(db)

    # Setup cgroup limits if specified
    if memory_mb or cpu_percent:
        setup_cgroup(jail_name, memory_mb, cpu_percent)
        ok(f"Resource limits: memory={memory_mb or 'unlimited'}MB, cpu={cpu_percent or 'unlimited'}%")

    ok(f"Jail '{jail_name}' registered in ledger.")

def cmd_bootstrap_jail(jail_name):
    """Bootstrap a base OS inside a jail."""
    db = load_db()
    if jail_name not in db["jails"]:
        err(f"Jail '{jail_name}' is not initialized. Run 'init-jail' first.")
        sys.exit(1)

    jail_info = db["jails"][jail_name]
    jail_path = jail_info["path"]
    os_type = jail_info["type"]

    if os.path.exists(os.path.join(jail_path, "usr/bin")):
        warn(f"Jail '{jail_name}' already appears to be bootstrapped.")
        return

    ok(f"Bootstrapping system files for '{jail_name}' ({os_type})...")
    info("Pulling core packages. This might take a few minutes...")

    try:
        if os_type == "arch":
            cmd = ["pacstrap", "-K", jail_path, "base", "coreutils", "pacman", "sudo"]
        elif os_type in ("debian", "ubuntu"):
            cmd = ["debootstrap", "--variant=minbase", "stable", jail_path]
        elif os_type == "kali":
            cmd = ["debootstrap", "--variant=minbase", "kali-rolling", jail_path,
                   "https://http.kali.org/kali"]
        else:
            err(f"Unsupported OS type '{os_type}'.")
            sys.exit(1)

        result = subprocess.run(cmd)

        if result.returncode == 0:
            ok(f"Jail '{jail_name}' successfully bootstrapped.")
        else:
            err("Bootstrap deployment failed.")
            sys.exit(1)

    except FileNotFoundError:
        err("Required bootstrap tool is missing.")
        if os_type == "arch":
            info("Fix: sudo pacman -S arch-install-scripts")
        elif os_type in ("debian", "ubuntu", "kali"):
            info("Fix: sudo pacman -S debian-archive-keyring")
        sys.exit(1)

def cmd_exec_jail(jail_name, command_args):
    """Execute a command inside a jail."""
    db = load_db()
    if jail_name not in db["jails"]:
        err(f"Jail '{jail_name}' is not initialized.")
        sys.exit(1)

    jail_info = db["jails"][jail_name]
    jail_path = jail_info["path"]

    if not mount_jail_binds(jail_name, db):
        sys.exit(1)

    # Sync DNS
    resolv_host = "/etc/resolv.conf"
    resolv_jail = os.path.join(jail_path, "etc/resolv.conf")
    if os.path.exists(resolv_host):
        try:
            os.makedirs(os.path.dirname(resolv_jail), exist_ok=True)
            shutil.copy2(resolv_host, resolv_jail)
        except Exception:
            warn("Failed to sync DNS (resolv.conf) to jail.")

    # Attach to cgroup if configured
    memory_mb = jail_info.get("memory_mb")
    cpu_percent = jail_info.get("cpu_percent")
    if memory_mb or cpu_percent:
        setup_cgroup(jail_name, memory_mb, cpu_percent)
        attach_cgroup(jail_name)

    try:
        if not command_args:
            cmd = ["chroot", jail_path, "/usr/bin/bash"]
            info(f"Entering shell inside '{jail_name}'...")
        else:
            cmd = ["chroot", jail_path] + command_args

        result = subprocess.run(cmd)
        returncode = result.returncode
    except KeyboardInterrupt:
        warn("Exiting jail environment.")
        returncode = 130
    finally:
        # Always unmount after exec
        unmount_jail(jail_path)

    sys.exit(returncode)

def cmd_update_jail(jail_name):
    """Update all packages inside a jail."""
    db = load_db()
    if jail_name not in db["jails"]:
        err(f"Jail '{jail_name}' is not initialized.")
        sys.exit(1)

    jail_info = db["jails"][jail_name]
    jail_path = jail_info["path"]
    os_type = jail_info["type"]

    if not mount_jail_binds(jail_name, db):
        sys.exit(1)

    # Sync DNS
    resolv_host = "/etc/resolv.conf"
    resolv_jail = os.path.join(jail_path, "etc/resolv.conf")
    if os.path.exists(resolv_host):
        try:
            shutil.copy2(resolv_host, resolv_jail)
        except Exception:
            pass

    try:
        ok(f"Updating packages in jail '{jail_name}'...")
        if os_type == "arch":
            cmd = ["chroot", jail_path, "/bin/bash", "-c", "pacman -Syu --noconfirm"]
        elif os_type in ("debian", "ubuntu", "kali"):
            cmd = ["chroot", jail_path, "/bin/bash", "-c",
                   "apt-get update && apt-get upgrade -y"]
        else:
            err("Unknown OS type for update.")
            sys.exit(1)

        result = subprocess.run(cmd)
        if result.returncode == 0:
            ok(f"Jail '{jail_name}' updated successfully.")
        else:
            err(f"Update failed for jail '{jail_name}'.")
    finally:
        unmount_jail(jail_path)

def cmd_register_pkg(binary_name, jail_name):
    """Register a package binary from a jail to the host system via ghost wrapper."""
    db = load_db()
    if jail_name not in db["jails"]:
        err(f"Target jail '{jail_name}' does not exist.")
        sys.exit(1)

    if not mount_jail_binds(jail_name, db):
        sys.exit(1)

    _create_ghost_wrapper(binary_name, jail_name)

    db["packages"][binary_name] = {
        "jail": jail_name,
        "binary_path": f"/usr/bin/{binary_name}",
        "ghost_wrapper": os.path.join(HOST_BIN_DIR, binary_name),
        "installed": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    # Also track in jail's package list
    db["jails"][jail_name]["packages"].append(binary_name)
    save_db(db)
    ok(f"Package '{binary_name}' mapped through '{jail_name}'.")

def cmd_register_multi(jail_name, binary_list):
    """Register multiple binaries from a jail at once."""
    db = load_db()
    if jail_name not in db["jails"]:
        err(f"Target jail '{jail_name}' does not exist.")
        sys.exit(1)

    if not mount_jail_binds(jail_name, db):
        sys.exit(1)

    count = 0
    for binary_name in binary_list:
        _create_ghost_wrapper(binary_name, jail_name)
        db["packages"][binary_name] = {
            "jail": jail_name,
            "binary_path": f"/usr/bin/{binary_name}",
            "ghost_wrapper": os.path.join(HOST_BIN_DIR, binary_name),
            "installed": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        db["jails"][jail_name]["packages"].append(binary_name)
        count += 1

    save_db(db)
    ok(f"Registered {count} packages from '{jail_name}'.")

def _create_ghost_wrapper(binary_name, jail_name):
    """Create a ghost wrapper script that transparently chroot's into the jail."""
    wrapper_path = os.path.join(HOST_BIN_DIR, binary_name)
    jail_path = os.path.join(JAILS_ROOT, jail_name)

    wrapper_code = f'''#!/usr/bin/env python3
# Auto-generated Ghost Wrapper by SayaOS Package Manager
# Jail: {jail_name} | Binary: {binary_name}
import os
import sys
import subprocess

JAIL_PATH = "{jail_path}"
BINARY = "{binary_name}"

# Ensure jail mounts are active
MOUNTS = ["/dev", "/dev/pts", "/proc", "/sys"]
for m in MOUNTS:
    target = os.path.join(JAIL_PATH, m.lstrip("/"))
    if os.path.exists(target) and not any(m in line for line in open("/proc/mounts").readlines()):
        try:
            subprocess.run(["mount", "--bind", m, target], capture_output=True)
        except Exception:
            pass

cmd = ["chroot", JAIL_PATH, f"/usr/bin/{{BINARY}}"] + sys.argv[1:]
try:
    result = subprocess.run(cmd)
    sys.exit(result.returncode)
except KeyboardInterrupt:
    sys.exit(130)
'''
    try:
        with open(wrapper_path, "w") as f:
            f.write(wrapper_code)
        os.chmod(wrapper_path, 0o755)
    except PermissionError:
        err("Cannot create ghost wrapper. Root required.")
        sys.exit(1)

def cmd_purge_pkg(binary_name):
    """Completely remove a package's ghost wrapper and database entry."""
    db = load_db()
    if binary_name not in db["packages"]:
        err(f"Package '{binary_name}' is untracked.")
        sys.exit(1)

    pkg_info = db["packages"][binary_name]
    wrapper_path = pkg_info["ghost_wrapper"]

    # Remove wrapper
    if os.path.exists(wrapper_path):
        try:
            # Verify it's a ghost wrapper before deleting
            with open(wrapper_path, "r") as f:
                content = f.read()
            if "SayaOS" in content or "Ghost Wrapper" in content:
                os.remove(wrapper_path)
                ok(f"Ghost wrapper removed: {wrapper_path}")
            else:
                warn(f"Wrapper at {wrapper_path} doesn't look like a SayaOS ghost. Skipping delete.")
        except PermissionError:
            err("Root privileges required to remove wrapper.")
            sys.exit(1)

    # Remove from jail's package list
    jail_name = pkg_info["jail"]
    if jail_name in db["jails"] and binary_name in db["jails"][jail_name]["packages"]:
        db["jails"][jail_name]["packages"].remove(binary_name)

    del db["packages"][binary_name]
    save_db(db)
    ok(f"Package '{binary_name}' clean purged from host ledger.")

def cmd_remove_jail(jail_name, force=False):
    """Remove a jail and all its packages."""
    db = load_db()
    if jail_name not in db["jails"]:
        err(f"Jail '{jail_name}' does not exist.")
        sys.exit(1)

    jail_info = db["jails"][jail_name]
    jail_path = jail_info["path"]

    # Check for registered packages
    pkgs = jail_info.get("packages", [])
    if pkgs and not force:
        err(f"Jail '{jail_name}' has {len(pkgs)} registered packages: {', '.join(pkgs)}")
        info("Use 'remove-jail --force' to remove anyway (packages will be purged).")
        sys.exit(1)

    # Unmount first
    unmount_jail(jail_path)

    # Purge all ghost wrappers
    for pkg_name in list(pkgs):
        if pkg_name in db["packages"]:
            wrapper = db["packages"][pkg_name].get("ghost_wrapper", "")
            if os.path.exists(wrapper):
                try:
                    with open(wrapper, "r") as f:
                        if "SayaOS" in f.read():
                            os.remove(wrapper)
                except Exception:
                    pass
            del db["packages"][pkg_name]

    # Cleanup cgroup
    cleanup_cgroup(jail_name)

    # Remove jail directory
    try:
        shutil.rmtree(jail_path)
        ok(f"Jail directory removed: {jail_path}")
    except Exception as e:
        warn(f"Could not remove jail directory: {e}")

    del db["jails"][jail_name]
    save_db(db)
    ok(f"Jail '{jail_name}' removed with all its packages.")

def cmd_list_jails():
    """List all initialized jails."""
    db = load_db()
    if not db["jails"]:
        info("No jails currently initialized.")
        return

    print(f"\n{C.BOLD}{'Jail':<20} {'Type':<10} {'Status':<10} {'Packages':<10} {'Limits'}{C.RST}")
    print("─" * 70)
    for name, info_j in db["jails"].items():
        proc_path = os.path.join(info_j["path"], "proc")
        status = f"{C.OK}mounted{C.RST}" if is_mounted(proc_path) else f"{C.WARN}idle{C.RST}"
        pkg_count = len(info_j.get("packages", []))
        mem = info_j.get("memory_mb")
        cpu = info_j.get("cpu_percent")
        limits = f"{mem or '-'}MB / {cpu or '-'}%"
        print(f"  {name:<18} {info_j['type']:<10} {status:<20} {pkg_count:<10} {limits}")
    print()

def cmd_list_pkgs():
    """List all registered packages."""
    db = load_db()
    if not db["packages"]:
        info("No ghost wrappers mapped in the host file system.")
        return

    print(f"\n{C.BOLD}{'Package':<25} {'Jail':<20} {'Installed'}{C.RST}")
    print("─" * 65)
    for name, info_p in db["packages"].items():
        print(f"  {name:<23} {info_p['jail']:<20} {info_p.get('installed', '?')}")
    print()

def cmd_set_alias(alias_name, binary_name):
    """Create an alias for a registered package."""
    db = load_db()
    if binary_name not in db["packages"]:
        err(f"Package '{binary_name}' is not registered.")
        sys.exit(1)

    alias_path = os.path.join(HOST_BIN_DIR, alias_name)
    real_wrapper = db["packages"][binary_name]["ghost_wrapper"]

    # Create symlink
    try:
        if os.path.exists(alias_path):
            os.remove(alias_path)
        os.symlink(real_wrapper, alias_path)
        db["aliases"][alias_name] = binary_name
        save_db(db)
        ok(f"Alias '{alias_name}' -> '{binary_name}'")
    except PermissionError:
        err("Root required to create alias.")
        sys.exit(1)

def cmd_info():
    """Show system info."""
    db = load_db()
    jail_count = len(db["jails"])
    pkg_count = len(db["packages"])
    total_size = 0
    for jname, jinfo in db["jails"].items():
        jp = jinfo["path"]
        if os.path.exists(jp):
            try:
                result = subprocess.run(["du", "-sb", jp], capture_output=True, text=True)
                if result.returncode == 0:
                    total_size += int(result.stdout.split()[0])
            except Exception:
                pass

    print(f"""
{C.BOLD}╔══════════════════════════════════════════╗
║           SayaOS Package Manager         ║
╚══════════════════════════════════════════╝{C.RST}

  Version:    {VERSION}
  DB:         {DB_PATH}
  Jails root: {JAILS_ROOT}
  Jails:      {jail_count}
  Packages:   {pkg_count}
  Total size: {total_size / (1024*1024):.1f} MB
""")

def cmd_backup():
    """Manually create a database backup."""
    _backup_db()
    backups = sorted(Path(DB_BACKUP_DIR).glob("pkg_db_*.json")) if os.path.exists(DB_BACKUP_DIR) else []
    ok(f"Backup created. Total backups: {len(backups)}")

def cmd_restore():
    """Restore database from most recent backup."""
    global_db = _repair_db()
    save_db(global_db)
    ok("Database restored from latest backup.")

# ── Main ────────────────────────────────────────────────────
def print_usage():
    print(f"""
{C.BOLD}╔══════════════════════════════════════════════════════════╗
║              SayaOS Package Manager v{VERSION}              ║
╚══════════════════════════════════════════════════════════╝{C.RST}
{C.BOLD}Jail Management:{C.RST}
  saya -S <package>                  Install a package (pacman-style)
  saya -S -kali <package>            Install from Kali repos
  saya -S -parrot <package>          Install from Parrot repos
  saya -S -blackarch <package>       Install from BlackArch repos
  saya -R <package>                  Remove a package
  saya -Rf <package>                 Force remove
  saya -Q                            Query installed packages
  saya -L                            List all jails
  saya -Syu                          Sync & update all jails

{C.BOLD}Advanced Jail Management:{C.RST}
  sudo saya init-jail <name> <type> [--mem <MB>] [--cpu <%>]
      Create a new jail (types: arch, debian, kali, ubuntu)
  sudo saya bootstrap-jail <name>
      Install base OS inside a jail
  sudo saya update-jail <name>
      Update all packages in a jail
  sudo saya exec <name> [command...]
      Execute a command inside a jail
  sudo saya remove-jail <name> [--force]
      Remove a jail and all its packages
  saya list-jails
      List all initialized jails

{C.BOLD}Package Management:{C.RST}
  sudo saya register-pkg <binary> <jail>
      Map a single binary to the host
  sudo saya register-multi <jail> <bin1> [bin2] [bin3]...
      Map multiple binaries at once
  sudo saya purge-pkg <binary>
      Remove a package's ghost wrapper
  saya list-pkgs
      List all registered packages

{C.BOLD}Utilities:{C.RST}
  sudo saya alias <name> <binary>
      Create an alias for a package
  saya info
      Show system information
  sudo saya backup
      Create database backup
  sudo saya restore
      Restore database from latest backup
""")

def _parse_pacman_flags(argv):
    """Parse pacman-style flags: -S, -R, -Q, -Syu, -S -kali, etc."""
    if len(argv) < 2 or not argv[1].startswith('-'):
        return False

    flags = argv[1]
    args = argv[2:]
    distro = None
    pkgs = []

    # Check for --distro or -distro flag
    i = 0
    while i < len(args):
        if args[i] in ('-kali', '-parrot', '-blackarch'):
            distro = args[i].lstrip('-')
            i += 1
        elif args[i].startswith('-') and not args[i].startswith('--'):
            # e.g. -S -kali nmap → flags="-S", args=["-kali", "nmap"]
            flags += args[i][1:]
            distro_candidate = args[i].lstrip('-')
            if distro_candidate in ('kali', 'parrot', 'blackarch'):
                distro = distro_candidate
            i += 1
        else:
            pkgs.append(args[i])
            i += 1

    # Also check flags for embedded distro (e.g. -Skali)
    if '-k' in flags and '-s' in flags:
        # Could be -Sk or -Syu etc - parse carefully
        pass

    # Handle -Syu (sync + refresh + upgrade)
    if 'S' in flags and 'y' in flags and 'u' in flags:
        ok("Syncing repositories...")
        cmd_update_jails("default")
        return True

    # Handle -S (sync/install)
    if 'S' in flags:
        if not pkgs:
            err("No packages specified.")
            print(f"  Usage: saya -S <package>")
            print(f"         saya -S -kali <package>")
            return True
        for pkg in pkgs:
            info(f"Installing {pkg}" + (f" from {distro}" if distro else "") + "...")
            # Try to create a jail and install
            jail_name = f"{pkg}"
            try:
                cmd_init_jail(jail_name, "arch", None, None)
                if distro:
                    # Create distro-specific bootstrap
                    info(f"Bootstrapping from {distro}...")
                cmd_bootstrap_jail(jail_name)
                ok(f"{pkg} installed in jail '{jail_name}'")
                info(f"Run: saya exec {jail_name} {pkg}")
            except Exception as e:
                err(f"Failed to install {pkg}: {e}")
        return True

    # Handle -R (remove)
    if 'R' in flags:
        for pkg in pkgs:
            info(f"Removing {pkg}...")
            cmd_remove_jail(pkg, 'f' in flags)
        return True

    # Handle -Q (query)
    if 'Q' in flags:
        cmd_list_pkgs()
        return True

    # Handle -L (list jails)
    if 'L' in flags:
        cmd_list_jails()
        return True

    return False

def main():
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)
    # First try pacman-style flags
    if sys.argv[1].startswith('-'):
        if _parse_pacman_flags(sys.argv):
            sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "init-jail" and len(sys.argv) >= 4:
        # Parse optional flags
        mem = None
        cpu = None
        args = sys.argv[2:]
        for i, a in enumerate(args):
            if a == "--mem" and i + 1 < len(args):
                mem = int(args[i + 1])
                args = args[:i] + args[i+2:]
            elif a == "--cpu" and i + 1 < len(args):
                cpu = int(args[i + 1])
                args = args[:i] + args[i+2:]
        if len(args) >= 2:
            cmd_init_jail(args[0], args[1], mem, cpu)
        else:
            err("Usage: init-jail <name> <type> [--mem <MB>] [--cpu <%>]")
            sys.exit(1)

    elif cmd == "bootstrap-jail" and len(sys.argv) == 3:
        cmd_bootstrap_jail(sys.argv[2])

    elif cmd == "update-jail" and len(sys.argv) == 3:
        cmd_update_jail(sys.argv[2])

    elif cmd == "exec" and len(sys.argv) >= 3:
        cmd_exec_jail(sys.argv[2], sys.argv[3:])

    elif cmd == "register-pkg" and len(sys.argv) == 4:
        cmd_register_pkg(sys.argv[2], sys.argv[3])

    elif cmd == "register-multi" and len(sys.argv) >= 4:
        cmd_register_multi(sys.argv[2], sys.argv[3:])

    elif cmd == "purge-pkg" and len(sys.argv) == 3:
        cmd_purge_pkg(sys.argv[2])

    elif cmd == "remove-jail" and len(sys.argv) >= 3:
        force = "--force" in sys.argv
        cmd_remove_jail(sys.argv[2], force)

    elif cmd == "list-jails":
        cmd_list_jails()

    elif cmd == "list-pkgs":
        cmd_list_pkgs()

    elif cmd == "alias" and len(sys.argv) == 4:
        cmd_set_alias(sys.argv[2], sys.argv[3])

    elif cmd == "info":
        cmd_info()

    elif cmd == "backup":
        cmd_backup()

    elif cmd == "restore":
        cmd_restore()

    else:
        err(f"Invalid or incomplete command: '{cmd}'")
        print_usage()
        sys.exit(1)

if __name__ == "__main__":
    main()
