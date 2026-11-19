/* Guest-only PID/mount namespace supervisor. One kernel; no VM inside VM. */
#define _GNU_SOURCE
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sched.h>
#include <signal.h>
#include <unistd.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static volatile sig_atomic_t stopping;
static void stop(int sig) { (void)sig; stopping=1; }
static void die(const char *s) { perror(s); exit(1); }
static void bind_at(const char *root,const char *source,const char *target,int ro) {
    char path[PATH_MAX];
    if (snprintf(path,sizeof(path),"%s%s",root,target)>=(int)sizeof(path)) exit(2);
    if (mkdir(path,0755) && errno!=EEXIST) die(path);
    if (mount(source,path,NULL,MS_BIND,NULL)) die(source);
    if (ro && mount(NULL,path,NULL,MS_BIND|MS_REMOUNT|MS_RDONLY,NULL)) die("readonly bind");
}
int main(int argc,char **argv) {
    if (argc!=2 || strcmp(argv[1],"/data/local/tmp/qe-subsystem-root")) return 2;
    char cmdline[4096]={0}; FILE *f=fopen("/proc/cmdline","r");
    if (!f || !fgets(cmdline,sizeof(cmdline),f) || !strstr(cmdline,"androidboot.hardware=ranchu")) {
        fputs("Refusing to run outside the disposable SDK guest\n",stderr); return 2;
    }
    fclose(f);
    if (unshare(CLONE_NEWNS|CLONE_NEWPID)) die("unshare PID/mount namespace");
    if (mount(NULL,"/",NULL,MS_REC|MS_PRIVATE,NULL)) die("private mount propagation");
    struct sigaction action={0}; action.sa_handler=stop; sigemptyset(&action.sa_mask);
    sigaction(SIGTERM,&action,NULL); sigaction(SIGINT,&action,NULL);
    pid_t child=fork();
    if (child<0) die("fork");
    if (child==0) {
        prctl(PR_SET_PDEATHSIG,SIGKILL);
        char path[PATH_MAX];
        snprintf(path,sizeof(path),"%s/proc",argv[1]);
        if (mount("proc",path,"proc",MS_NOSUID|MS_NODEV|MS_NOEXEC,NULL)) die("child proc");
        bind_at(argv[1],"/sys","/sys",1);
        bind_at(argv[1],"/dev","/dev",0);
        bind_at(argv[1],"/dev/pts","/dev/pts",0);
        snprintf(path,sizeof(path),"%s/dev/shm",argv[1]);
        if (mkdir(path,01777) && errno!=EEXIST) die("child shm directory");
        if (mount("tmpfs",path,"tmpfs",MS_NOSUID|MS_NODEV,"size=64m,mode=1777")) die("child shm");
        bind_at(argv[1],"/system","/camera/parent-system",1);
        bind_at(argv[1],"/vendor","/camera/parent-vendor",1);
        bind_at(argv[1],"/mnt/x2dii/camera","/camera/firmware-ui",1);
        bind_at(argv[1],"/mnt/qe-firmware-system","/camera/firmware-system",1);
        bind_at(argv[1],"/mnt/qe-firmware-vendor","/camera/firmware-vendor",1);
        if (chdir(argv[1]) || chroot(".") || chdir("/")) die("chroot");
        clearenv();
        setenv("PATH","/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",1);
        setenv("HOME","/root",1); setenv("USER","root",1);
        setenv("TMPDIR","/tmp",1);
        setenv("LD_PRELOAD","/usr/local/lib/libqe-kernel-compat.so",1);
        pid_t app=fork();
        if (app<0) die("child session fork");
        if (!app) { execl("/bin/sh","sh","/usr/local/bin/qe-subsystem-session",NULL); die("child session"); }
        /* As PID 1, reap descendants and terminate the whole PID namespace. */
        for (;;) {
            if (stopping) { kill(-1,SIGTERM); usleep(300000); _exit(0); }
            int status; pid_t ended=waitpid(-1,&status,WNOHANG);
            if (ended==app) _exit(WIFEXITED(status)?WEXITSTATUS(status):1);
            if (ended<0 && errno==ECHILD) _exit(0);
            usleep(100000);
        }
    }
    for (;;) {
        if (stopping) { kill(child,SIGTERM); stopping=0; }
        int status; pid_t ended=waitpid(child,&status,0);
        if (ended==child) return WIFEXITED(status)?WEXITSTATUS(status):1;
        if (ended<0 && errno!=EINTR) die("wait child init");
    }
}
