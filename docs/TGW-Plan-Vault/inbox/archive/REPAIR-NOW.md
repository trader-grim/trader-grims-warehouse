# Boot Repair — Apply These Commands First (from April ISO, as root)

```bash
# 1. Symlinks
cd /media/db/rootMX25
ln -s boot/initrd.img-6.12.90+deb13.1-amd64 initrd.img
ln -s boot/vmlinuz-6.12.90+deb13-amd64 vmlinuz.old

# 2. Bind mounts
mount -t proc proc /media/db/rootMX25/proc
mount --rbind /sys /media/db/rootMX25/sys
mount --rbind /dev /media/db/rootMX25/dev
mount --rbind /run /media/db/rootMX25/run

# 3. Chroot — disable SDDM (the hang), set text mode
chroot /media/db/rootMX25 bash
systemctl disable sddm
systemctl set-default multi-user.target
exit

# 4. Remove network-online conflict
rm /media/db/rootMX25/etc/systemd/system/network-online.target.wants/ifupdown-wait-online.service

# 5. Unmount
umount -l /media/db/rootMX25/proc /media/db/rootMX25/sys /media/db/rootMX25/dev /media/db/rootMX25/run
```

# After reboot — expect text login on TTY1

journalctl -xb   # see what else failed

# Then choose:
# A) Repair KDE:  apt-get install --reinstall task-kde-desktop plasma-desktop sddm
#                 systemctl set-default graphical.target && systemctl enable sddm && reboot
# B) NixOS:       proceed to Phase 5 (change /dev/sda → nvme0n1 in disko config first)
