/* The guest compositor owns one physical-display model in both modes. */
static struct wl_event_source *subsystem_timer;
static int subsystem_active;
static uint32_t subsystem_sequence;
static int subsystem_input(const char *line) {
    if(!subsystem_active)return 0;
    int fd=open("/dev/qe-subsystem/input",O_WRONLY|O_NONBLOCK|O_CLOEXEC);
    if(fd>=0){char buffer[256];int n=snprintf(buffer,sizeof(buffer),"%s\n",line);
        if(n>0 && n<(int)sizeof(buffer))write(fd,buffer,n);close(fd);}
    return 1;
}
static int subsystem_tick(void *unused) {
    (void)unused;
    int owner=open("/dev/qe-subsystem/display-owner",O_RDONLY|O_CLOEXEC);
    char name[16]={0};if(owner>=0){read(owner,name,sizeof(name)-1);close(owner);}
    int active=!strncmp(name,"subsystem",9);
    if(active!=subsystem_active) {
        subsystem_active=active;subsystem_sequence=0;
        printf("QE_DISPLAY_OWNER %s\n",active?"subsystem":"camera");fflush(stdout);
    }
    if(active) {
        int fd=open("/dev/qe-subsystem/frame",O_RDONLY|O_CLOEXEC);
        uint32_t h[128];struct stat st;
        if(fd>=0 && read(fd,h,sizeof(h))==sizeof(h) && !fstat(fd,&st) &&
            h[0]==0x58463244 && h[1]==1024 && h[2]==768 && h[3]==4096 && h[4]<=1 &&
            st.st_size==(off_t)(sizeof(h)+4096*768) && h[5]!=subsystem_sequence) {
            size_t bytes=4096*768;
            void *map=mmap(NULL,bytes+sizeof(h),PROT_READ,MAP_PRIVATE,fd,0);
            if(map!=MAP_FAILED) {
                write_pixels(h[1],h[2],h[3],h[4],(char*)map+sizeof(h));
                munmap(map,bytes+sizeof(h));subsystem_sequence=h[5];
            }
        }
        if(fd>=0)close(fd);
    }
    wl_event_source_timer_update(subsystem_timer,100);
    return 0;
}
