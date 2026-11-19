#!/bin/sh
export HOME=/root USER=root LOGNAME=root SHELL=/bin/sh
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export DISPLAY=:1 LANG=C.UTF-8 XDG_RUNTIME_DIR=/run/user/0
export XDG_CONFIG_DIRS=/etc/xdg XDG_DATA_DIRS=/usr/local/share:/usr/share
export XDG_SESSION_TYPE=x11 XDG_CURRENT_DESKTOP=XFCE XDG_SESSION_DESKTOP=xfce NO_AT_BRIDGE=1
export LD_PRELOAD=/usr/local/lib/libqe-kernel-compat.so
unset DBUS_SYSTEM_BUS_ADDRESS DBUS_SESSION_BUS_ADDRESS
mkdir -p "$XDG_RUNTIME_DIR" /tmp/.X11-unix
chmod 700 "$XDG_RUNTIME_DIR"
dbus-uuidgen --ensure=/etc/machine-id
Xvfb :1 -screen 0 1024x768x24 -nolisten tcp -ac > /tmp/qe-xvfb.log 2>&1 &
sleep 1
x11vnc -display :1 -listen 127.0.0.1 -rfbport 5900 -nopw -forever -shared -noxdamage -noshm -quiet > /tmp/qe-vnc.log 2>&1 &
python3 /opt/qe/subsystem_bridge.py > /tmp/qe-bridge.log 2>&1 &
bridge=$!
dbus-run-session -- xfce4-session > /tmp/qe-xfce.log 2>&1 &
session=$!
attempt=0
while [ "$attempt" -lt 90 ]; do
    if [ -f /dev/qe-subsystem/bridge-ready ] && pidof xfwm4 >/dev/null && pidof xfce4-panel >/dev/null && pidof xfdesktop >/dev/null; then
        {
            echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
            echo "pid_namespace=$(readlink /proc/1/ns/pid)"
            echo "mount_namespace=$(readlink /proc/1/ns/mnt)"
            echo "memtotal_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
            echo "init_name=$(cat /proc/1/comm)"
            echo "parent_marker=$(cat /dev/qe-subsystem/identity)"
            echo "firmware_version=$(cat /camera/firmware-system/etc/VERSION 2>/dev/null)"
        } > /dev/qe-subsystem/child-evidence
        if touch /camera/firmware-system/.qe-write-probe 2>/dev/null; then
            echo QE_SUBSYSTEM_FAILED_READONLY
            exit 1
        fi
        echo QE_SUBSYSTEM_READONLY_OK > /dev/qe-subsystem/readonly-result
        rm -f /dev/qe-subsystem/test-control /dev/qe-subsystem/input-result
        mkfifo /dev/qe-subsystem/test-control
        exec 8<> /dev/qe-subsystem/test-control
        touch /dev/qe-subsystem/ready
        echo QE_SUBSYSTEM_CHILD_READY
        while kill -0 "$session" && kill -0 "$bridge"; do
            request=
            if read -r -t 1 request <&8; then
                case "$request" in
                    input-test\ *)
                        xterm -title QE_Subsystem -geometry 60x12+120+160 -e /bin/sh -c 'read value; echo "$value" > /dev/qe-subsystem/input-result; sleep 60' & ;;
                    return) /usr/local/bin/qe-return-camera ;;
                esac
            fi
        done
        exit 1
    fi
    attempt=$((attempt+1))
    sleep 1
done
cat /tmp/qe-x*.log /tmp/qe-bridge.log /tmp/qe-vnc.log
exit 1
