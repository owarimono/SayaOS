<div align="center">

<img src="https://raw.githubusercontent.com/owarimono/SayaOS/main/SayaOS-logo.png" alt="SayaOS logo" width="180">

# SayaOS

**A live Arch system where foreign-distro packages live in chroot jails, never in `/usr`.**

<p align="center">
  <a href="https://archlinux.org/"><img alt="Base: Arch Linux" src="https://img.shields.io/badge/Base-Arch_Linux-F2F2F2?style=for-the-badge&labelColor=111111&logo=archlinux&logoColor=111111"></a>
  <img alt="libc: glibc" src="https://img.shields.io/badge/libc-glibc-D6D6D6?style=for-the-badge&labelColor=111111">
  <img alt="init: systemd" src="https://img.shields.io/badge/init-systemd-B4B4B4?style=for-the-badge&labelColor=111111&logo=systemd&logoColor=111111">
  <img alt="arch: x86_64" src="https://img.shields.io/badge/Arch-x86__64-919191?style=for-the-badge&labelColor=111111&logo=linux&logoColor=111111">
  <img alt="Boot: BIOS + UEFI" src="https://img.shields.io/badge/Boot-BIOS_+_UEFI-6E6E6E?style=for-the-badge&labelColor=111111">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-2EA44F?style=for-the-badge&labelColor=111111"></a>
  <a href="#status"><img alt="Status: experimental" src="https://img.shields.io/badge/Status-experimental-E3A008?style=for-the-badge&labelColor=111111"></a>
</p>

</div>

---

## Overview

SayaOS is a stock `archiso` profile plus one tool, `saya`, which installs
packages from Arch, Debian, Ubuntu or Kali into a chroot jail under
`/var/saya/jails` and exposes individual binaries on the host `PATH` through
generated shims. Package files never enter the host root filesystem.

The distribution layer is intentionally unchanged: same kernel, same `pacman`,
same repositories, no installer, no custom packages, console-only live session.
The differentiated component is the isolation model.

## Design

A second package manager on one system is a known failure mode: `apt` and
`pacman` both write to `/usr`, file conflicts resolve silently by install order,
and removing the interloper can remove part of the base system. SayaOS instead
gives each foreign distribution its own tree and a shim interface:

| Property | Mechanism |
|---|---|
| Host filesystem untouched | Jail root is `/var/saya/jails/<name>`; no path outside it is written |
| Atomic removal | Removing a jail is one recursive delete |
| Resource ceilings | cgroup v2 `memory.max` / `cpu.max` at `/sys/fs/cgroup/sayaos-<name>` |
| Transparent invocation | Shim at `/usr/bin/<binary>`; compatible with `sudo`, scripts, `xargs`, completion |
| Reversible host changes | Shim removal is content-guarded against deleting real packages |

State is one JSON ledger, `/etc/saya/pkg_db.json`, with keys `version`, `jails`,
`packages`, `aliases`. Every mutation writes a timestamped copy to
`/etc/saya/backups/` first, keeping the newest ten; a ledger that fails to parse
falls back to the newest valid backup.

```
                   ┌──────────────────── host ────────────────────┐
  saya -S …  ───►  │  /usr/bin/saya           /usr/bin/<shim>      │
                   │      │                        │              │
                   │      ▼                        ▼              │
                   │  /etc/saya/pkg_db.json   mount --bind +      │
                   │  /etc/saya/backups/      chroot + exec       │
                   └──────┼────────────────────────┼──────────────┘
                          │                        │
                  ┌───────▼────────┐      ┌────────▼───────────┐
                  │ ledger (JSON)  │      │ /var/saya/jails/…  │
                  │ jails          │      │ arch · debian ·    │
                  │ packages       │      │ ubuntu · kali      │
                  │ aliases        │      └────────────────────┘
                  └────────────────┘
```

### Isolation boundaries

Entering a jail binds `/dev`, `/dev/pts`, `/proc` and `/sys` from the host into
the jail tree, copies `/etc/resolv.conf`, then calls `chroot(2)`.

This is a filesystem and cgroup boundary. It is **not** a security boundary:

- Network, PID and IPC namespaces are shared with the host.
- Commands execute as root inside the jail.
- No seccomp filter, no capability drop, no mount namespace.

The threat model is package sprawl and filesystem contamination. Anything
security-sensitive should not be run through a shim. Moving to `unshare` or
`bubblewrap` would require changes in two functions only (`cmd_exec_jail`,
`_create_ghost_wrapper`).

### Shim lifecycle

`register-pkg <binary> <jail>` writes `/usr/bin/<binary>` containing a script
that bind-mounts the pseudo-filesystems when absent, `chroot`s into the jail, and
forwards `sys.argv[1:]`. `purge-pkg <binary>` deletes a shim only when its
content still contains the `SayaOS` or `Ghost Wrapper` marker, so a real package
binary cannot be removed by this path.

Read-only verbs (`list-jails`, `list-pkgs`, `info`) do not require root.
Mutating verbs are re-executed under `sudo` by the `/usr/bin/saya` bash wrapper.

## Usage

```console
# saya init-jail tools arch          # create a jail (types: arch, debian, ubuntu, kali)
# saya bootstrap-jail tools          # pacstrap a base into the jail
# saya exec tools uname -a           # run one command inside
# saya register-pkg nmap tools       # expose /usr/bin/nmap on the host
$ nmap --version                     # executes inside the jail
# saya purge-pkg nmap                # remove the shim
# saya remove-jail tools             # delete the jail
```

### Command reference

| Verb | Root | Description |
|---|---|---|
| `init-jail <name> <type>` | yes | Create a jail. Types: `arch`, `debian`, `ubuntu`, `kali`. Options: `--mem <MB>`, `--cpu <%>` |
| `bootstrap-jail <name>` | yes | Install the base system into the jail (`pacstrap` / `debootstrap`) |
| `update-jail <name>` | yes | Upgrade all packages in one jail |
| `exec <name> [cmd…]` | yes | Run a command inside the jail |
| `remove-jail <name> [--force]` | yes | Delete a jail and its ledger entries |
| `register-pkg <binary> <jail>` | yes | Create a host shim for one jailed binary |
| `register-multi <jail> <bin…>` | yes | Create shims for several binaries |
| `purge-pkg <binary>` | yes | Remove a shim (content-guarded) |
| `list-jails`, `list-pkgs`, `info` | no | Inspect the ledger |
| `backup`, `restore <file>` | yes | Manual ledger snapshot and rollback |
| `-S`, `-R`, `-Q`, `-L`, `-Syu` | — | `pacman`-style aliases; partially implemented, see [Status](#status) |

## Build

Requirements: `archiso` on the build host; nothing at runtime beyond Python 3
standard library.

```sh
mkarchiso -v -w ~/sayaos-work -o ./out dev_lab/archiso_profile
qemu-system-x86_64 -m 2048 -cdrom out/sayaos-*.iso      # boot the result
```

No build script exists in the repository, and `dev_lab/assets/` is not copied
into the profile by any step — a build therefore uses the plain GRUB menu
without the theme. The live session autologins as root into `zsh` and runs
`sayafetch`; set a password with `passwd` before using it on real hardware.

## Repository layout

```
dev_lab/
  archiso_profile/            archiso profile — the bootable component
    profiledef.sh             identity: name, label, bootmodes, squashfs options
    packages.x86_64           30 packages in the live image
    pacman.conf               stock Arch core + extra
    grub/ efiboot/ syslinux/  BIOS and UEFI boot menus
    airootfs/
      usr/bin/saya_cli.py     package manager, ~855 lines, stdlib only
      usr/bin/saya            sudo-elevating wrapper for mutating verbs
      usr/bin/sayafetch       fastfetch with the SayaOS config
      etc/saya/pkg_db.json    initial empty ledger
      etc/sayafetch/          live-session fastfetch config and logo
      etc/zsh/zshrc           root autologin shell, runs sayafetch on start
      etc/systemd/            networkd, iwd, resolved, volatile journal, no suspend
  assets/                     branding sources: themed grub.cfg, GRUB theme,
                              fastfetch config, ASCII logo
```

## Status

Experimental. The jail lifecycle — create, bootstrap, exec, register, purge,
remove, list, backup/restore — is implemented. The `pacman`-style compatibility
layer is not finished; the defects below are confirmed against the committed code.

| Defect | Effect |
|---|---|
| `-S <pkg>` does not install the named package | Creates a jail named after the package and bootstraps a bare base |
| `-kali` / `-debian` / `-ubuntu` parsed then discarded | Flags reach no code path; `parrot` and `blackarch` are not valid types |
| `dev_lab/assets/` not wired to the profile | GRUB theme, its pixmaps and the themed menu never reach the ISO |

The explicit verbs listed above are the tested path; prefer them over the
`pacman`-style aliases.

## Roadmap

- [ ] Implement `-S` installation; honour or remove the distro flags
- [x] Fix `-Syu` and replace substring mount detection with exact path comparison
- [x] Ship bootstrap dependencies in the live image
- [ ] Add `build-iso.sh` that stages `dev_lab/assets/` into the profile
- [ ] Decide whether shims keep root-in-chroot semantics or move to `unshare`

## License

MIT — see [LICENSE](LICENSE). SayaOS is an independent remaster and is not
affiliated with or endorsed by Arch Linux.
