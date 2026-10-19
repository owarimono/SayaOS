#!/usr/bin/env bash
# shellcheck disable=SC2034

iso_name="SayaOS"
iso_label="SAYAOS"
iso_publisher="SayaOS Project <https://github.com/sayaos>"
iso_application="SayaOS Live Environment"
iso_version="1.0"
iso_size="5800"
install_dir="sayaos"
buildmodes=('iso')
bootmodes=('bios.syslinux'
           'uefi.systemd-boot')
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')

file_permissions=(
    ["/etc/shadow"]="0:0:400"
    ["/root"]="0:0:750"
    ["/usr/bin/saya"]="0:0:755"
    ["/usr/bin/saya_cli.py"]="0:0:755"
    ["/usr/bin/sayafetch"]="0:0:755"
)
