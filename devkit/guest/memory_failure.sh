#!/system/bin/sh
stty -echo
echo
echo QE_MEMORY_FAILURE_BEGIN
cat /proc/meminfo
dmesg | tail -n 140
cat /data/local/tmp/qe-xfce-launch.log
for name in qe-xfce.log qe-vnc.log qe-xvfb.log; do
    echo "QE_LOG $name"
    tail -n 90 /data/local/tmp/qe-xfce-root/tmp/$name
done
echo QE_MEMORY_FAILURE_CAPTURED
stty echo
