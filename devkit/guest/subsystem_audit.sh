#!/system/bin/sh
echo QE_SUBSYSTEM_AUDIT_BEGIN
ns=$(sed -n 's/^pid_namespace=//p' /dev/qe-subsystem/child-evidence)
count=0
if [ -n "$ns" ]; then
    for path in /proc/[0-9]*/ns/pid; do
        if [ "$(readlink "$path" 2>/dev/null)" = "$ns" ]; then count=$((count+1)); fi
    done
else
    count=-1
fi
echo "QE_AUDIT_CHILDREN=$count"
if ! grep -q ' /data/local/tmp/qe-subsystem-root ' /proc/mounts; then echo QE_AUDIT_ROOT_UNMOUNTED; fi
cat /dev/qe-subsystem/readonly-result
cat /dev/qe-subsystem/state.json
echo QE_SUBSYSTEM_AUDIT_END
