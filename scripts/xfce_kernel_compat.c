/* Narrow userspace fallback for GLib's existence checks on the old SDK kernel.
 * GLib requests F_OK with AT_EACCESS|AT_SYMLINK_NOFOLLOW. musl cannot emulate
 * that combination without faccessat2 (Linux 5.8). fstatat preserves both
 * effective-credential path traversal and the no-follow existence semantics.
 * All other access checks, and supported kernels, retain libc's behavior.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

int faccessat(int dirfd, const char *path, int mode, int flags)
{
    int (*original)(int, const char *, int, int) = dlsym(RTLD_NEXT, "faccessat");
    if (!original) { errno = ENOSYS; return -1; }
    int result = original(dirfd, path, mode, flags);
    if (result == -1 && (errno == EINVAL || errno == ENOSYS) && mode == F_OK &&
        flags == (AT_EACCESS | AT_SYMLINK_NOFOLLOW)) {
        struct stat info;
        return fstatat(dirfd, path, &info, AT_SYMLINK_NOFOLLOW);
    }
    return result;
}

#ifdef QE_COMPAT_CHECK
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
int main(void)
{
    char path[] = "/tmp/qe-compat-XXXXXX";
    assert(mkdtemp(path));
    int dir = open(path, O_DIRECTORY | O_RDONLY);
    assert(dir >= 0);
    assert(symlinkat("missing", dir, "link") == 0);
    int flags = AT_EACCESS | AT_SYMLINK_NOFOLLOW;
    assert(faccessat(dir, "link", F_OK, flags) == 0);
    assert(faccessat(dir, "missing", F_OK, flags) == -1 && errno == ENOENT);
    assert(faccessat(dir, "link", F_OK, 0) == -1 && errno == ENOENT);
    assert(faccessat(dir, ".", R_OK, 0) == 0);
    assert(unlinkat(dir, "link", 0) == 0);
    close(dir);
    assert(rmdir(path) == 0);
    puts("QE_COMPAT_CHECK_PASSED");
    return 0;
}
#endif
