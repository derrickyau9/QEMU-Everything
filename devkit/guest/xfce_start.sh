#!/system/bin/sh
# Runs only inside the disposable SDK guest, never on a physical camera.
root=/data/local/tmp/qe-xfce-root
mkdir -p "$root"
if [ ! -f "$root/usr/local/bin/qe-xfce-session" ]; then
    mount -t ext4 /dev/block/vdg "$root" || { echo QE_XFCE_FAILED_MOUNT; exit 1; }
    mount -t proc proc "$root/proc" || exit 1
    mount -t sysfs sysfs "$root/sys" || exit 1
    mount -o bind /dev "$root/dev" || exit 1
    mkdir -p "$root/dev/pts"
    mount -o bind /dev/pts "$root/dev/pts" || exit 1
    mkdir -p "$root/dev/shm"
    mount -t tmpfs -o size=64m tmpfs "$root/dev/shm" || exit 1
fi
ip link set eth0 up
ip address add 10.0.2.15/24 dev eth0 2>/dev/null
ip rule add pref 100 lookup main 2>/dev/null
unset LD_LIBRARY_PATH LD_PRELOAD DBUS_SYSTEM_BUS_ADDRESS DBUS_SESSION_BUS_ADDRESS
# Android's inherited TMPDIR/XDG paths do not exist in the Linux root.
chroot "$root" /usr/bin/env -i HOME=/root USER=root PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin TMPDIR=/tmp XDG_CONFIG_DIRS=/etc/xdg XDG_DATA_DIRS=/usr/local/share:/usr/share NO_AT_BRIDGE=1 /usr/local/bin/qe-xfce-session > /data/local/tmp/qe-xfce-launch.log 2>&1 &
launcher=$!
attempt=0
while [ "$attempt" -lt 90 ]; do
    if [ -n "$(pidof xfwm4)" ] && [ -n "$(pidof xfce4-panel)" ] && [ -n "$(pidof xfdesktop)" ]; then
        echo QE_XFCE_USERSPACE_READY
        wait "$launcher"
        exit $?
    fi
    if [ "$attempt" -eq 25 ]; then
        echo QE_XFCE_DIAGNOSTIC_BEGIN
        cat /data/local/tmp/qe-xfce-launch.log "$root/tmp/qe-xfce.log"
        ps -A | grep -E 'xfce|xfwm|xfdesktop|Xvfb|dbus'
        echo QE_XFCE_DIAGNOSTIC_END
    fi
    attempt=$((attempt+1))
    sleep 1
done
cat /data/local/tmp/qe-xfce-launch.log "$root/tmp/qe-xfce.log"
ps -A | grep -E 'xfce|xfwm|xfdesktop|Xvfb|dbus'
echo QE_XFCE_FAILED_COMPONENTS
exit 1
