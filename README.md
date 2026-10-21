<div align="center">

<img src="https://raw.githubusercontent.com/owarimono/SayaOS/main/SayaOS-logo.png" alt="SayaOS logo" width="180">

# SayaOS

**An Arch-based live system where foreign packages live in jails, not in your filesystem.**

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

SayaOS ships one tool worth talking about: **`saya`**, a package manager that
installs software from Arch, Debian, Ubuntu or Kali into an isolated chroot jail
under `/var/saya/jails` and exposes each binary on the host `PATH` as a generated
wrapper. Nothing foreign is ever merged into the host root filesystem — delete
the jail directory and the software is gone, with no leftovers to hunt.

Everything else is deliberately plain: a stock `archiso` profile with branding, a
console-only live session, and no installer. The interesting part is the
isolation model, not the distro wrapped around it.

## Why jails

A second package manager on a single system is a known way to break it: `apt`
and `pacman` write to the same `/usr`, file conflicts are silent, and removing
the interloper risks taking the base system with it.

SayaOS takes the position that a foreign distro's tooling should stay in its own
directory tree, with a thin shim on `PATH` so it still behaves like a normal
command:

- **Host purity** — `/usr` belongs to `pacman`. A jail never writes to it.
- **Atomic removal** — one jail, one directory. `rm -rf` is a valid uninstall.
- **Per-jail ceilings** — cgroup v2 `memory.max` / `cpu.max` per jail.
- **Zero friction** — a wrapped binary is a real executable at `/usr/bin/<name>`,
  so `sudo`, scripts, `xargs` and shell completion keep working.

## How it works

State is a single JSON ledger, `/etc/saya/pkg_db.json` (`version`, `jails`,
`packages`, `aliases`). Every write is preceded by a timestamped copy into
`/etc/saya/backups/`, newest ten kept; a corrupt ledger falls back to the newest
parseable backup.

A jail is a chroot tree at `/var/saya/jails/<name>`. Entering it binds `/dev`,
`/dev/pts`, `/proc` and `/sys` from the host, copies `/etc/resolv.conf` in, and
`chroot`s. Optional cgroup v2 limits are applied at `/sys/fs/cgroup/sayaos-<name>`.

> **Isolation boundary:** filesystem plus cgroup, *not* namespaces. A jail shares
> the host network, PID and IPC namespaces, and commands run as root inside it.
> This contains package sprawl. It does not contain an attacker.

`register-pkg` writes `/usr/bin/<binary>` — a small script that bind-mounts the
pseudo-filesystems if missing, `chroot`s into the jail, and forwards arguments.
`purge-pkg` deletes a host binary only if its content still mentions `SayaOS` or
`Ghost Wrapper`, so it cannot eat a real system binary.

```
                   ┌──────────────────── host ────────────────────┐
  saya -S …  ───►  │  /usr/bin/saya           /usr/bin/<shim>      │
                   │      │                        │              │
                   │      ▼                        ▼              │
                   │  /etc/saya/pkg_db.json   mount --bind +      │
                   │  /etc/saya/backups/      chroot + exec       │
                   │      │                        │              │
                   └──────┼────────────────────────┼──────────────┘
                          │                        │
                  ┌───────▼────────┐      ┌────────▼───────────┐
                  │ ledger (JSON)  │      │ /var/saya/jails/…  │
                  │ jails          │      │ arch · debian ·    │
                  │ packages       │      │ ubuntu · kali      │
                  │ aliases        │      └────────────────────┘
                  └────────────────┘
```

## Quick start

Boot the ISO (see [building](#building-the-iso)), then:

```console
# saya init-jail tools arch          # create a jailed Arch tree
# saya bootstrap-jail tools          # pacstrap a base into it
# saya exec tools uname -a           # run one command inside
# saya register-pkg nmap tools       # expose /usr/bin/nmap on the host
$ nmap --version                     # runs from inside the jail
# saya purge-pkg nmap                # drop the shim
# saya remove-jail tools             # drop everything
```

Read-only verbs need no root. Mutating verbs re-exec through `sudo` via the
`/usr/bin/saya` bash wrapper.

## CLI reference

| Verb | Root | Description |
|---|---|---|
| `init-jail <name> <type>` | yes | Create a jail — types `arch`, `debian`, `ubuntu`, `kali`; options `--mem <MB>`, `--cpu <%>` |
| `bootstrap-jail <name>` | yes | Install the base OS into the jail (`pacstrap` / `debootstrap`) |
| `update-jail <name>` | yes | Upgrade all packages in one jail |
| `exec <name> [cmd…]` | yes | Run a command inside the jail |
| `remove-jail <name> [--force]` | yes | Remove a jail and its registry entries |
| `register-pkg <binary> <jail>` | yes | Write a host shim for one jailed binary |
| `register-multi <jail> <bin…>` | yes | Same, for several binaries |
| `purge-pkg <binary>` | yes | Remove a shim (content-guarded) |
| `list-jails`, `list-pkgs`, `info` | no | Inspect the ledger |
| `backup`, `restore <file>` | yes | Manual ledger snapshot / rollback |
| `-S`, `-R`, `-Q`, `-L`, `-Syu` | — | Pacman-style aliases — partially implemented, see [Status](#status) |

## Project layout

```
dev_lab/
  archiso_profile/            archiso profile — the bootable half
    profiledef.sh             name, label, bootmodes, squashfs options
    packages.x86_64           30 packages in the live image
    pacman.conf               stock Arch core + extra
    grub/ efiboot/ syslinux/  BIOS and UEFI boot menus
    airootfs/
      usr/bin/saya_cli.py     the entire package manager, stdlib only
      usr/bin/saya            sudo-elevating wrapper for mutating verbs
      usr/bin/sayafetch       fastfetch with the SayaOS config
      etc/saya/pkg_db.json    initial empty ledger
      etc/sayafetch/          live-session fastfetch config and logo
      etc/zsh/zshrc           root autologin shell, runs sayafetch on start
      etc/systemd/            networkd, iwd, resolved, volatile journal, no suspend
  assets/                     branding sources: themed grub.cfg, GRUB theme,
                              fastfetch config and ASCII logo
```

Python 3 only — no third-party imports anywhere in the tool.

## Building the ISO

Needs `archiso` on the build host:

```sh
mkarchiso -v -w ~/sayaos-work -o ./out dev_lab/archiso_profile
```

Then boot it:

```sh
qemu-system-x86_64 -m 2048 -cdrom out/sayaos-*.iso
```

There is no build script in the repo, and `dev_lab/assets/` is **not** copied into
the profile automatically — a build uses the plain GRUB menu and no theme. You
land in `zsh` as root and `sayafetch` runs on start; set a password with `passwd`
before doing anything real.

## Status

Experimental. The jail lifecycle — create, bootstrap, exec, register, purge,
remove, list, backup/restore — is implemented, and the ledger is defensive. The
pacman-style compatibility layer is not finished:

| Issue | Effect |
|---|---|
| `cmd_update_jails` undefined (`saya_cli.py:735`) | `saya -Syu` exits with `NameError` |
| `-S <pkg>` does not install the package | It creates a jail *named after the package* and bootstraps a bare base |
| `-kali` / `-debian` / `-ubuntu` parsed then discarded | The flags reach nothing; `parrot` and `blackarch` are not valid types |
| `is_mounted()` substring-matches `/proc/mounts` | A jail whose name prefixes another jail's path reads as mounted |
| Bootstrap deps missing from `packages.x86_64` | Needs `arch-install-scripts`, `debootstrap`, `debian-archive-keyring` |
| `sayaos-setup.sh` is never invoked | archiso wants `airootfs/root/customize_airootfs.sh` |
| `dev_lab/assets/` not wired into the profile | The GRUB theme, its pixmaps and the themed menu never reach the ISO |

Prefer the explicit verbs above — that is the tested path.

## Roadmap

- [ ] Make `-S` install for real, and either honour or drop the distro flags
- [ ] Fix `-Syu` and the mount-detection check
- [ ] Ship the bootstrap dependencies in the live image
- [ ] Add `build-iso.sh` that wires `dev_lab/assets/` into the profile
- [ ] Decide whether ghost wrappers stay root-in-chroot or move to `unshare`

## License

MIT — see [LICENSE](LICENSE). SayaOS is an independent remaster, not an official
Arch Linux project.
