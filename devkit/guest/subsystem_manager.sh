#!/system/bin/sh
# Sourced by camera_run.sh: this is the virtual camera's lifecycle owner.
subsystem_root=/data/local/tmp/qe-subsystem-root
control_root=/dev/qe-subsystem
subsystem_pid=0
subsystem_state=camera

subsystem_record() {
    total=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
    available=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    record="{\"event\":\"$1\",\"state\":\"$subsystem_state\",\"boot_id\":\"$(cat /proc/sys/kernel/random/boot_id)\",\"parent_pid_ns\":\"$(readlink /proc/1/ns/pid)\",\"camera_pid\":$camera_pid,\"subsystem_pid\":$subsystem_pid,\"memtotal_kib\":$total,\"available_kib\":$available,\"ion_reclaimed_bytes\":0,\"native_services\":\"mock\"}"
    echo "$record" > "$control_root/state.json.tmp"
    mv "$control_root/state.json.tmp" "$control_root/state.json"
    echo "$record" >> /data/local/tmp/qe-subsystem-events.jsonl
    echo "QE_SUBSYSTEM_EVENT $record"
}

subsystem_owner() {
    echo "$1" > "$control_root/display-owner.tmp"
    mv "$control_root/display-owner.tmp" "$control_root/display-owner"
}

subsystem_stop_child() {
    if [ "$subsystem_pid" -gt 1 ]; then
        kill -TERM "$subsystem_pid" 2>/dev/null
        count=0
        while kill -0 "$subsystem_pid" 2>/dev/null && [ "$count" -lt 30 ]; do
            sleep 0.1
            count=$((count+1))
        done
        kill -KILL "$subsystem_pid" 2>/dev/null
        wait "$subsystem_pid" 2>/dev/null
    fi
    subsystem_pid=0
    # Namespace mounts disappear when its init exits; only the root image mount
    # is owned by the parent. Do not lazily unmount and hide leaked references.
    if grep -q " $subsystem_root " /proc/mounts; then
        if ! umount "$subsystem_root"; then
            echo QE_SUBSYSTEM_FAILED_UNMOUNT
            return 1
        fi
    fi
    rm -f "$control_root/ready" "$control_root/bridge-ready" "$control_root/frame" "$control_root/frame.tmp" "$control_root/input"
}

subsystem_restore_camera() {
    subsystem_owner camera
    if [ "$camera_pid" -le 1 ] || ! kill -0 "$camera_pid" 2>/dev/null; then
        LD_PRELOAD=$ui_preload LD_LIBRARY_PATH=$camera_lib /mnt/x2dii/camera/camera-gui-software -platform "$ui_platform" --fullscreen $ui_simulation &
        camera_pid=$!
        sleep 12
    fi
    if ! kill -0 "$camera_pid" 2>/dev/null; then echo QE_SUBSYSTEM_FAILED_CAMERA; return 1; fi
    subsystem_state=camera
    subsystem_record camera-restored
}

subsystem_start() {
    [ "$subsystem_state" = camera ] || return
    subsystem_state=starting
    subsystem_record starting
    # Terminate and later restart the GUI, releasing its ordinary pages.
    # Mock camera services remain running. No DSP/ION release is claimed.
    kill -TERM "$camera_pid"
    sleep 1
    kill -KILL "$camera_pid" 2>/dev/null
    wait "$camera_pid" 2>/dev/null
    camera_pid=0
    subsystem_record camera-gui-stopped
    mkdir -p "$subsystem_root"
    if ! mount -t ext4 /dev/block/vdg "$subsystem_root"; then
        echo QE_SUBSYSTEM_FAILED_ROOT
        subsystem_restore_camera
        return
    fi
    rm -f "$control_root/ready" "$control_root/bridge-ready" "$control_root/child-evidence"
    /mnt/x2dii/subsystem-init "$subsystem_root" > /data/local/tmp/qe-subsystem-launch.log 2>&1 &
    subsystem_pid=$!
    attempt=0
    while [ "$attempt" -lt 100 ]; do
        if [ -f "$control_root/ready" ]; then
            subsystem_state=desktop
            subsystem_owner subsystem
            subsystem_record desktop-active
            echo QE_SUBSYSTEM_CHILD_EVIDENCE_BEGIN
            cat "$control_root/child-evidence"
            echo QE_SUBSYSTEM_CHILD_EVIDENCE_END
            return
        fi
        kill -0 "$subsystem_pid" 2>/dev/null || break
        attempt=$((attempt+1))
        sleep 1
    done
    echo QE_SUBSYSTEM_FAILED_START
    cat /data/local/tmp/qe-subsystem-launch.log "$subsystem_root"/tmp/qe-*.log
    subsystem_stop_child
    subsystem_restore_camera
}

subsystem_main() {
    mkdir -p "$control_root" /mnt/qe-firmware-system /mnt/qe-firmware-vendor
    ip link set lo up
    echo "$(cat /proc/sys/kernel/random/boot_id):camera-parent" > "$control_root/identity"
    : > /data/local/tmp/qe-subsystem-events.jsonl
    # Original offline firmware images are inspected read-only; these are not
    # substituted for the currently running SDK parent /system and /vendor.
    mount -t ext4 -o ro,noload /dev/block/vdh /mnt/qe-firmware-system || return 1
    mount -t ext4 -o ro,noload /dev/block/vdi /mnt/qe-firmware-vendor || return 1
    rm -f "$control_root/control"
    mkfifo "$control_root/control"
    exec 9<> "$control_root/control"
    subsystem_owner camera
    subsystem_record controller-ready
    while :; do
        request=
        if read -r -t 1 request <&9; then
            case "$request" in
                start) subsystem_start ;;
                stop)
                    if [ "$subsystem_state" != camera ]; then
                        subsystem_state=stopping
                        subsystem_record stopping
                        subsystem_owner camera
                        subsystem_stop_child || return 1
                        subsystem_record child-reaped
                        subsystem_restore_camera || return 1
                    fi ;;
                status) subsystem_record status ;;
                *) echo QE_SUBSYSTEM_UNKNOWN_REQUEST ;;
            esac
        fi
        if [ "$subsystem_state" = desktop ] && ! kill -0 "$subsystem_pid" 2>/dev/null; then
            echo QE_SUBSYSTEM_UNEXPECTED_EXIT
            cat /data/local/tmp/qe-subsystem-launch.log "$subsystem_root"/tmp/qe-*.log
            subsystem_owner camera
            subsystem_stop_child
            subsystem_restore_camera || return 1
        fi
    done
}
