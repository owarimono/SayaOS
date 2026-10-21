# SayaOS

An Arch-based live ISO built around one idea: install software from Arch, Debian,
Ubuntu or Kali into an isolated chroot jail under `/var/saya/jails`, then expose
each binary on the host `PATH` through a generated wrapper — without ever merging
those packages into the host filesystem. Delete the jail directory, the software
is gone.

There is no installer, no desktop and no custom repository. This is a stock
archiso profile plus one Python tool.

**Status: experimental.** Two documented CLI verbs are broken as committed — see
[Known issues](#known-issues) before you try anything.

## Layout

```
dev_lab/
  archiso_profile/            archiso profile (the bootable part)
    profiledef.sh             name, label, bootmodes, squashfs options
    packages.x86_64           30 packages in the live image
    pacman.conf               stock Arch core + extra
    grub/ efiboot/ syslinux/  BIOS + UEFI boot menus
    airootfs/
      usr/bin/saya_cli.py     the whole package manager, ~855 lines, stdlib only
      usr/bin/saya            sudo-elevating bash wrapper for mutating verbs
      usr/bin/sayafetch       fastfetch with the SayaOS config
      etc/saya/pkg_db.json    initial empty ledger
      etc/sayafetch/          live-session fastfetch config + logo
      etc/zsh/zshrc           root autologin shell, runs sayafetch
      etc/systemd/            networkd, iwd, resolved, volatile journal, no suspend
  assets/                     branding sources: themed grub.cfg, GRUB theme,
                              fastfetch config and ASCII logo
```

## How it works

State lives in a single JSON ledger, `/etc/saya/pkg_db.json`, with four keys:
`version`, `jails`, `packages`, `aliases`. Every write is preceded by a
timestamped copy into `/etc/saya/backups/` (newest 10 kept); a corrupt ledger
falls back to the newest parseable backup.

A jail is a chroot tree at `/var/saya/jails/<name>`. Entering it binds `/dev`,
`/dev/pts`, `/proc` and `/sys` from the host, copies `/etc/resolv.conf` in, and
`chroot`s. Optional cgroup v2 limits set `memory.max` and `cpu.max`. Isolation is
filesystem-level plus cgroup — **not namespaces** — so a jail shares the host's
network, PID and IPC namespaces and commands run as root inside it. This
contains package sprawl, not attackers.

`register-pkg` writes `/usr/bin/<binary>`, a small script that re-binds the
pseudo-filesystems if needed and `chroot`s into the jail to run the real binary,
forwarding arguments. `purge-pkg` only deletes a host binary whose content still
mentions `SayaOS` / `Ghost Wrapper`, so it cannot eat a real system binary.

Read-only verbs (`list-jails`, `list-pkgs`, `info`, `-Q`, `-L`) work without
root. Mutating verbs re-exec under `sudo` through `/usr/bin/saya`.

## Usage

```
sudo saya init-jail myjail arch        # create a jail (types: arch, debian, kali, ubuntu)
sudo saya bootstrap-jail myjail        # pacstrap a base into it
sudo saya exec myjail uname -a         # run one command inside
sudo saya register-pkg nmap myjail     # expose /usr/bin/nmap on the host
sudo saya register-multi myjail bin1 bin2
sudo saya purge-pkg nmap               # remove the wrapper
sudo saya remove-jail myjail
saya list-jails | saya list-pkgs | saya info
sudo saya backup | sudo saya restore <file>
```

Pacman-style flags are parsed too: `-S`, `-R`, `-Q`, `-L`, `-Syu`, plus
`-kali` / `-debian` / `-ubuntu` distro tags. Read the caveats below first — most
of those paths are not finished.

## Build

Requires `archiso` (which provides `mkarchiso`) on the build host:

```bash
mkarchiso -v -w ~/sayaos-work -o ./out dev_lab/archiso_profile
```

That is the whole build — there is no build script in the repo, and the branding
under `dev_lab/assets/` is **not** copied into the profile automatically, so a
build uses the plain GRUB menu and no theme.

Boot the result in QEMU:

```bash
qemu-system-x86_64 -m 2048 -cdrom out/sayaos-*.iso
```

Logging in lands you in zsh as root; `sayafetch` runs on start. `passwd` if you
are going to do anything real.

## Known issues

| Problem | Effect |
|---|---|
| `cmd_update_jails` is undefined (`saya_cli.py:735`) | `saya -Syu` dies with `NameError` |
| `-S <pkg>` never installs the package | It creates a *jail named after the package* and bootstraps a bare Arch base, then reports it "installed" |
| `-kali` / `-debian` / `-ubuntu` are parsed and discarded | The flags in the usage text reach nothing; `parrot` and `blackarch` are not even valid types |
| `is_mounted()` substring-scans `/proc/mounts` | A jail whose name prefixes another jail's path reads as "mounted" |
| `bootstrap-jail` needs `arch-install-scripts`, `debootstrap`, `debian-archive-keyring` | None are in `packages.x86_64`, so bootstrapping fails on the live image |
| `sayaos-setup.sh` never runs | archiso ignores it (it wants `airootfs/root/customize_airootfs.sh`) |
| `dev_lab/assets/` is not wired into the profile | The GRUB theme, its pixmaps and the themed menu never reach the ISO |

Workable today: `init-jail` + `bootstrap-jail` on a host that already has the
bootstrap tools, `exec`, `register-pkg`, `purge-pkg`, `remove-jail`, the read-only
listing verbs, and ledger backup/restore.

## License

See [LICENSE](LICENSE).
