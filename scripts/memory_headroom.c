/* Offline guest workload: approximate captured MemAvailable with resident
 * anonymous pages. This is NOT an emulator for camera services or ION. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static unsigned long read_kib(const char *field) {
    char line[256], key[80]; unsigned long value;
    FILE *f = fopen("/proc/meminfo", "r");
    if (!f) return 0;
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "%79s %lu", key, &value) == 2 && !strcmp(key, field)) {
            fclose(f); return value;
        }
    }
    fclose(f); return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    char *end; errno = 0;
    unsigned long target = strtoul(argv[1], &end, 10);
    unsigned long available = read_kib("MemAvailable:"), total = read_kib("MemTotal:");
    if (errno || *end || target < 192*1024 || !total || !available) return 3;
    unsigned long hold = available > target ? available-target : 0;
    if (hold > total*3/4) return 4;
    size_t size = hold*1024;
    long page = sysconf(_SC_PAGESIZE);
    volatile unsigned char *buffer = size ? malloc(size) : NULL;
    if (size && !buffer) return 5;
    for (size_t i=0; i<size; i+=(size_t)page) buffer[i] = (unsigned char)(i/page);
    /* Force residency, not just virtual address reservation. Swap is absent
     * in this lab, and mlock makes the synthetic load explicit and persistent. */
    if (size && mlock((const void *)buffer,size)) return 6;
    for (size_t i=0; i<size; i+=(size_t)page)
        if (buffer[i] != (unsigned char)(i/page)) return 7;
    FILE *report = fopen("/data/local/tmp/qe-headroom.json", "w");
    if (!report) return 8;
    fprintf(report, "{\"synthetic\":true,\"pid\":%ld,\"target_available_kib\":%lu,"
                    "\"before_available_kib\":%lu,\"held_kib\":%lu,\"after_available_kib\":%lu}\n",
                    (long)getpid(), target, available, hold, read_kib("MemAvailable:"));
    fclose(report);
    puts("QE_HEADROOM_READY"); fflush(stdout);
    for (;;) pause();
}
