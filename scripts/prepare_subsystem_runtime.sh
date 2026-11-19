#!/bin/sh
set -eu
project=$1
base=$2
build=$3
output=$4
case "$build" in /root/qe-subsystem-*) ;; *) exit 2;; esac
test ! -e "$build" || { echo 'Choose a new build directory'; exit 2; }
mkdir -p "$build" "$output"
cp -a "$base" "$build/root"
root=$build/root
chroot "$root" /sbin/apk add --no-cache python3
mkdir -p "$root/opt/qe" "$root/camera" "$root/root/Desktop"
cp "$project/devkit/rfb.py" "$root/opt/qe/rfb.py"
cp "$project/devkit/guest/subsystem_bridge.py" "$root/opt/qe/subsystem_bridge.py"
cp "$project/devkit/guest/subsystem_session.sh" "$root/usr/local/bin/qe-subsystem-session"
cat > "$root/usr/local/bin/qe-return-camera" <<'EOF'
#!/bin/sh
printf 'stop\n' > /dev/qe-subsystem/control
EOF
chmod 755 "$root/usr/local/bin/qe-return-camera" "$root/usr/local/bin/qe-subsystem-session"
cat > "$root/root/Desktop/camera-files.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Camera files (read-only)
Exec=thunar /camera
Icon=folder
Terminal=false
EOF
cat > "$root/root/Desktop/return-camera.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Return to camera
Exec=/usr/local/bin/qe-return-camera
Icon=system-log-out
Terminal=false
EOF
chmod 755 "$root/root/Desktop/"*.desktop
truncate -s 2G "$build/rootfs.ext4"
mke2fs -q -F -t ext4 -O '^metadata_csum,^64bit,^orphan_file,^metadata_csum_seed' -L QE_SUBSYSTEM -d "$root" "$build/rootfs.ext4"
cp --sparse=always "$build/rootfs.ext4" "$output/rootfs.ext4"
sha256sum "$output/rootfs.ext4"
echo QE_SUBSYSTEM_BUILD_COMPLETE
