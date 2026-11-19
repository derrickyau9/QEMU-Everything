#!/system/bin/sh
case "$1" in camera|desktop|returned) ;; *) exit 2;; esac
snapshot=/data/local/tmp/qe-memory-$1.txt
stty -echo
{
echo "QE_MEMORY_BEGIN $1"
echo 'QE_MEMORY_SECTION boot_id'
cat /proc/sys/kernel/random/boot_id
echo 'QE_MEMORY_SECTION online'
cat /sys/devices/system/cpu/online
echo 'QE_MEMORY_SECTION cpuinfo'
cat /proc/cpuinfo
echo 'QE_MEMORY_SECTION synthetic_headroom'
if [ -n "$(pidof qe-memory-headroom)" ] && [ -f /data/local/tmp/qe-headroom.json ]; then cat /data/local/tmp/qe-headroom.json; else echo null; fi
echo 'QE_MEMORY_SECTION meminfo'
cat /proc/meminfo
echo 'QE_MEMORY_SECTION iomem'
cat /proc/iomem
echo 'QE_MEMORY_SECTION memblock_reserved'
mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null
cat /sys/kernel/debug/memblock/reserved
echo 'QE_MEMORY_SECTION gui'
for pid in $(pidof camera-gui-software); do cat /proc/$pid/status; done
echo 'QE_MEMORY_SECTION dtb'
base64 /sys/firmware/fdt
echo 'QE_MEMORY_END'
} > "$snapshot" 2>&1
echo
echo "QE_PROBE_BEGIN $1"
base64 "$snapshot" | sed 's/^/QE_PROBE_DATA:/'
echo "QE_PROBE_SHA $(sha256sum "$snapshot" | cut -d ' ' -f 1)"
echo QE_PROBE_END
stty echo
