#!/bin/sh
# WSL build helper. Creates an isolated ARM64 userspace image on this PC.
# Does not access a camera, a physical disk, or existing guest userdata.
set -eu
source_archive=$1
output_dir=$2
build_dir=$3
expected_archive=9bf70a7f18ea44094cbb5f70c58f9af129c8214745743db0e68e5502cc2ce773
printf '%s  %s\n' "$expected_archive" "$source_archive" | sha256sum -c -
case "$build_dir" in /root/qe-xfce-arm64-*) ;; *) echo 'Unexpected build directory' >&2; exit 1;; esac
test ! -e "$build_dir" || { echo 'Build directory already exists; choose a fresh task directory' >&2; exit 1; }
mkdir -p "$build_dir/root" "$output_dir"
root="$build_dir/root"
tar -xzf "$source_archive" -C "$root"
cp /etc/resolv.conf "$root/etc/resolv.conf"
printf '%s\n' 'https://dl-cdn.alpinelinux.org/alpine/v3.24/main' 'https://dl-cdn.alpinelinux.org/alpine/v3.24/community' > "$root/etc/apk/repositories"
echo 'Preparing signed ARM64 XFCE packages in an isolated userspace root'
chroot "$root" /sbin/apk add --no-cache xfce4-session xfce4-panel xfdesktop xfwm4 thunar xfce4-settings dbus-x11 xvfb x11vnc xterm xwininfo font-dejavu adwaita-icon-theme
# The classic loader avoids libglycin 2.1's synchronous GTK thread-pool
# deadlock and dependencies on kernel features absent from the SDK kernel.
chroot "$root" /sbin/apk add --no-cache --repository https://dl-cdn.alpinelinux.org/alpine/v3.22/main gdk-pixbuf=2.42.12-r1
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
chroot "$root" /sbin/apk add --no-cache --virtual .qe-build gcc musl-dev
mkdir -p "$root/usr/local/lib"
cp "$script_dir/xfce_kernel_compat.c" "$root/tmp/qe-compat.c"
chroot "$root" /usr/bin/gcc -O2 -fPIC -shared /tmp/qe-compat.c -o /usr/local/lib/libqe-kernel-compat.so -ldl
chroot "$root" /usr/bin/gcc -O2 -DQE_COMPAT_CHECK /tmp/qe-compat.c -o /usr/local/bin/qe-compat-check -ldl
chroot "$root" /usr/local/bin/qe-compat-check
rm "$root/tmp/qe-compat.c"
chroot "$root" /sbin/apk del .qe-build
mkdir -p "$root/root/.config/xfce4/xfconf/xfce-perchannel-xml" "$root/root/Desktop" "$root/run" "$root/proc" "$root/sys" "$root/dev/shm" "$root/tmp"
chmod 1777 "$root/tmp" "$root/dev/shm"
if test -f "$root/etc/xdg/xfce4/panel/default.xml"; then
  cp "$root/etc/xdg/xfce4/panel/default.xml" "$root/root/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml"
fi
cat > "$root/root/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0"><property name="general" type="empty"><property name="use_compositing" type="bool" value="false"/></property></channel>
EOF
cat > "$root/usr/local/bin/qe-xfce-session" <<'EOF'
#!/bin/sh
export HOME=/root USER=root LOGNAME=root SHELL=/bin/sh
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export DISPLAY=:1 LANG=C.UTF-8 XDG_RUNTIME_DIR=/run/user/0
export XDG_SESSION_TYPE=x11 XDG_CURRENT_DESKTOP=XFCE XDG_SESSION_DESKTOP=xfce
unset LD_LIBRARY_PATH LD_PRELOAD DBUS_SYSTEM_BUS_ADDRESS DBUS_SESSION_BUS_ADDRESS
export LD_PRELOAD=/usr/local/lib/libqe-kernel-compat.so
/usr/local/bin/qe-compat-check || exit 1
mkdir -p "$XDG_RUNTIME_DIR" /tmp/.X11-unix
chmod 700 "$XDG_RUNTIME_DIR"
dbus-uuidgen --ensure=/etc/machine-id
Xvfb :1 -screen 0 1024x768x24 -nolisten tcp -ac >/tmp/qe-xvfb.log 2>&1 &
xvfb=$!
trap 'kill "$xvfb" 2>/dev/null || true' EXIT INT TERM
sleep 1
x11vnc -display :1 -listen 0.0.0.0 -rfbport 5900 -nopw -forever -shared -noxdamage -noshm -quiet >/tmp/qe-vnc.log 2>&1 &
vnc=$!
dbus-run-session -- xfce4-session >/tmp/qe-xfce.log 2>&1 &
session=$!
sleep 3
kill -0 "$xvfb" && kill -0 "$vnc" && kill -0 "$session" || { cat /tmp/qe-x*.log /tmp/qe-vnc.log; exit 1; }
echo QE_XFCE_USERSPACE_READY
wait "$session"
kill "$vnc" 2>/dev/null || true
EOF
chmod 755 "$root/usr/local/bin/qe-xfce-session"
cat > "$root/root/Desktop/terminal.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Terminal
Exec=xterm
Icon=utilities-terminal
Terminal=false
EOF
chmod 755 "$root/root/Desktop/terminal.desktop"
chroot "$root" /sbin/apk info -v > "$output_dir/packages.txt"
test ! -e "$output_dir/rootfs.ext4" || { echo 'Output image already exists' >&2; exit 1; }
echo 'Creating a dedicated ext4 guest image'
truncate -s 2G "$build_dir/rootfs.ext4"
mke2fs -q -F -t ext4 -O '^metadata_csum,^64bit,^orphan_file,^metadata_csum_seed' -L QE_XFCE -d "$root" "$build_dir/rootfs.ext4"
cp --sparse=always "$build_dir/rootfs.ext4" "$output_dir/rootfs.ext4"
sha256sum "$output_dir/rootfs.ext4" > "$output_dir/rootfs.sha256"
echo QE_XFCE_BUILD_COMPLETE
