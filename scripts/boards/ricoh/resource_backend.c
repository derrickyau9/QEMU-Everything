/* SPDX-License-Identifier: GPL-2.0-or-later
 * Authored read-only backend for the original GR IV Resource class.
 * ABI independently verified from GR IV 1.11 original code. This is an
 * auxiliary development filesystem, not a camera storage/device response.
 * Files are exact verified user-supplied RES bundle bytes; no assets here.
 */
typedef unsigned int u32;
typedef unsigned char u8;
struct item { const char *name; const u8 *data; u32 size; };
struct image { u32 count, version, reserved[2]; struct item files[]; };
#define IMAGE ((const struct image *)0x54400000u)
struct state { volatile u32 read_count, read_records[64][4]; u32 cursors[512], opened[512]; };
struct backend { void **vtable; struct state *state; };
static u8 lower(u8 c) { return c >= 'A' && c <= 'Z' ? c+32 : c; }
static int equal(const char *a, const char *b) {
    u32 n;
    if (!a || !b) return 0;
    for (n=0; n<32; n++) {
        if (lower((u8)a[n]) != lower((u8)b[n])) return 0;
        if (!a[n]) return 1;
    }
    return 0;
}
static void log(const char *s) {
    while (*s) *(volatile unsigned short *)0x1de00004u = (u8)*s++;
}
static int find(const char *name) {
    u32 i;
    if (IMAGE->version != 1 || !IMAGE->count || IMAGE->count > 512) return -1;
    for (i=0; i<IMAGE->count; i++) if (equal(name, IMAGE->files[i].name)) return i;
    return -1;
}
static int unsupported(void) { return 0; }
static int ro_stat(void *self, const char *path, u32 *info, u32 *flags) {
    int i = find(path); (void)self;
    if (i < 0 || !info) return 0;
    info[6] = IMAGE->files[i].size; info[7] = 0;
    if (flags) *flags = 0;
    log("[ROFS stat] "); log(IMAGE->files[i].name); log("\n");
    return 1;
}
static int ro_open(void *self, const char *path, u32 mode, u32 *handle, u32 *err) {
    int i = find(path); struct state *state=((struct backend *)self)->state;
    if (i < 0 || mode != 1 || !handle) { if (err) *err = 2; return 0; }
    if (state->opened[i]) { if (err) *err = 16; return 0; }
    state->opened[i] = 1; state->cursors[i] = 0; *handle = (u32)i+1;
    if (err) *err = 0;
    log("[ROFS open] "); log(IMAGE->files[i].name); log("\n");
    return 1;
}
static int ro_close(void *self, u32 handle, u32 *err) {
    u32 i = handle-1; struct state *state=((struct backend *)self)->state;
    if (!handle || i >= IMAGE->count || !state->opened[i]) { if (err) *err=9; return 0; }
    state->opened[i] = state->cursors[i] = 0; if (err) *err = 0; return 1;
}
static int ro_read(void *self, u32 handle, u8 *dst, u32 bytes, u32 *err) {
    u32 i=handle-1, n, at; struct state *state=((struct backend *)self)->state;
    if (!handle || i >= IMAGE->count || !state->opened[i] || !dst) { if (err) *err=9; return 0; }
    at=state->cursors[i];
    if (at > IMAGE->files[i].size || bytes > IMAGE->files[i].size-at) { if (err) *err=5; return 0; }
    for (n=0; n<bytes; n++) dst[n] = IMAGE->files[i].data[at+n];
    if (state->read_count < 64) {
        u32 record=state->read_count;
        state->read_records[record][0]=i;
        state->read_records[record][1]=(u32)dst;
        state->read_records[record][2]=bytes;
        state->read_records[record][3]=at;
        state->read_count=record+1;
    }
    state->cursors[i] += bytes; if (err) *err = 0;
    log("[ROFS read exact] "); log(IMAGE->files[i].name); log("\n");
    return 1;
}
void *resource_backend_vtable[32] = {
    [0 ... 31] = unsupported,
    [8] = ro_open, [9] = ro_close, [10] = ro_read, [21] = ro_stat
};

/* Independent word-addressed read-only development storage for ICONBIN.
 * Original DataRom ceil/division and read, SystemControl load, and original
 * memory-area validity setter are retained. This is not a NAND geometry or
 * device ACK model. A successful return follows an actual bounded copy.
 */
struct icon_image { u32 magic, version, bytes, block_bytes; u8 pixels[]; };
#define ICON_IMAGE ((const struct icon_image *)0x52000000u)
struct icon_storage { void **vtable; volatile u32 reads, destination, bytes, start_block;
    volatile u32 command_sequence, completed_sequence, operation, status, current_controller, current_canvas, current_id; };
static u32 icon_block_bytes(void *self) { (void)self; return 4; }
static int icon_read_blocks(void *self, u32 start, u32 count, u8 *destination) {
    struct icon_storage *storage = self; u32 i, offset, bytes;
    if (ICON_IMAGE->magic != 0x4e4f4349u || ICON_IMAGE->version != 1 ||
        ICON_IMAGE->block_bytes != 4 || !ICON_IMAGE->bytes ||
        ICON_IMAGE->bytes > 128u*1024u*1024u || (ICON_IMAGE->bytes & 3) ||
        !count || !destination || start > ICON_IMAGE->bytes/4 ||
        count > ICON_IMAGE->bytes/4-start) return 0;
    offset = start*4; bytes=count*4;
    for (i=0; i<bytes; i++) destination[i] = ICON_IMAGE->pixels[offset+i];
    storage->destination=(u32)destination; storage->bytes=bytes;
    storage->start_block=start; storage->reads++;
    log("[ICON storage read exact]\n");
    return 1;
}
void *icon_storage_vtable[32] = {
    [0 ... 31] = unsupported,
    [12] = icon_read_blocks, [19] = icon_block_bytes
};

void native_page_checkpoint(u32 stage) {
    if(stage==1)log("[NATIVE PAGE constructor complete]\n");
    if(stage==2)log("[NATIVE PAGE Show begin]\n");
    if(stage==3)log("[NATIVE PAGE Show complete]\n");
    if(stage==4)log("[NATIVE PAGE render complete]\n");
    if(stage==5)log("[NATIVE PAGE draw complete]\n");
}

/* Commands affect the original controller only through its original handler.
 * The host pauses all CPUs before submitting a new sequence. Completed frames
 * are captured only after the native handler and native Canvas draw return.
 */
void ui_component_loop(u32 *controller, struct icon_storage *storage) {
    void (*settle)(void *)=(void *)0x535713bcu;
    void (*dirty)(void *)=(void *)0x535899fcu;
    void (*draw)(void *)=(void *)0x5356dde4u;
    void *surface=(void *)(controller+3);
    void *(*app)(void)=(void *)0x5323ea84u;
    void *(*manager_get)(void *)=(void *)0x5323ebf8u;
    u32 *(*current)(void *)=(void *)0x5325c858u;
    void (*resize)(void *,int *)=(void *)0x53587c3cu;
    void (*bind)(void *,void *)=(void *)0x5356dd00u;
    int bounds[4]={0,0,720,480};
    storage->current_controller=(u32)controller;
    storage->current_canvas=controller[1];
    storage->current_id=*(u32 *)manager_get(app());
    log("[UI COMPONENT READY]\n");
    for (;;) {
        u32 sequence=storage->command_sequence, operation; u32 body[3]={0,0,0};void *render_manager=0;
        if (sequence==storage->completed_sequence) { __asm__ volatile("yield"); continue; }
        operation=storage->operation;
        if (operation<1 || operation>45) { storage->status=2; storage->completed_sequence=sequence; continue; }
        if (operation==4) { storage->status=1; storage->completed_sequence=sequence; return; }
        /* Explicit assisted component entries use original factory/Show APIs.
         * Playback has no image/storage task in this profile. Its menu parent
         * alias 0x04000000 would enter Single and wait for image loading; the
         * adapter returns to the already verified native empty component.
         * This is recorded as an assisted return, never a camera key claim. */
        u32 assisted_empty_return=operation==6 && controller[0x150/4]==0x04000000u;
        if((operation>=11 && operation<=13) || assisted_empty_return) {
            u32 target=assisted_empty_return || operation==12 ? 0x0400000fu :
                       operation==11 ? 0x08000003u : 0x02000060u;
            void *manager=manager_get(app());
            ((void (*)(void *,u32))0x538dfe24u)((u8 *)manager+0x38,0xffffffffu);
            void *factory=((void *(*)(void))0x53185f14u)();
            controller=((u32 *(*)(void *,u32))0x53185f7cu)(factory,target);
            ((void (*)(void *,u32))0x5325ca0cu)(manager,target);
            ((void (*)(void *,u32,u32,u32))0x53184558u)(controller,target,0x10000000u,0);
            ((void (*)(void *))(((u32 *)controller[0])[11]))(controller);
            ((void (*)(void *))(((u32 *)controller[0])[12]))(controller);
            storage->current_controller=(u32)controller;
            storage->current_id=*(u32 *)manager;
            storage->current_canvas=controller[1];
            ((void (*)(void *))0x538dff78u)((u8 *)manager+0x38);
            operation=3;
            log("[UI original assisted component entry complete]\n");
        }
        if(operation>=15) {
            /* Original AppMain raw-key handler consumes native key type/count.
             * The adapter submits presses/releases, never a target page or UI
             * selection. Dial pulses are single original raw events. */
            static const u8 keys[]={9,10,11,12,8,27,20,29,31,32,33,34,35,36,37,2,2,3,1,5,4,39,38};
            u32 key=operation>=38 ? 39 : keys[operation-15];
            void *manager=manager_get(app());
            ((void (*)(void *,u32))0x538dfe24u)((u8 *)manager+0x38,0xffffffffu);
            if(key==39) {
                void *keyboard=((void *(*)(void *))0x5323eef8u)(app());
                ((void (*)(void *,u32,u32,u32))0x532710c0u)(keyboard,key,operation>=38 ? operation-38 : 0,0);
            } else {
                u32 parameter[2]={1,0};body[0]=key;body[1]=(u32)parameter;
                void *main=((void *(*)(void *))0x5323ead8u)(app());
                if(operation!=31)((void (*)(void *,u32 *))0x53255db4u)(main,body);
                if((key<31 || key==35) && operation!=30) {
                    parameter[0]=2;((void (*)(void *,u32 *))0x53255db4u)(main,body);
                }
            }
            ((void (*)(void *))0x538dff78u)((u8 *)manager+0x38);
            /* Give the original queued dispatch time to run, then acquire its
             * native mutex before capturing a completed current controller. */
            u32 lo,hi,start,now;
            __asm__ volatile("mrrc p15,0,%0,%1,c14":"=r"(lo),"=r"(hi));start=lo;
            do {__asm__ volatile("yield");__asm__ volatile("mrrc p15,0,%0,%1,c14":"=r"(now),"=r"(hi));} while(now-start<20000000u);
            ((void (*)(void *,u32))0x538dfe24u)((u8 *)manager+0x38,0xffffffffu);
            controller=current(manager);
            storage->current_controller=(u32)controller;storage->current_id=*(u32 *)manager;storage->current_canvas=controller[1];
            if(controller[0]==0x53da2af8u)settle((void *)controller[0x174/4]);
            if(controller[0]==0x53da5e00u)settle((void *)controller[0x17c/4]);
            if(controller[0]==0x53d9f6a0u)settle((void *)controller[0x1f8/4]);
            if(controller[0]==0x53d9c3f8u)settle((void *)controller[0x1ac/4]);
            if(controller[0]==0x53da2c70u)settle((void *)controller[0x198/4]);
            render_manager=manager;
        }
        /* On a selected TopMenu row, native Right enters the selected page
         * through the same asynchronous AppMain transition as Confirm. */
        u32 right_enters_page=operation==8 && controller[0]==0x53d9c3f8u &&
                              ((volatile u8 *)controller)[0x1f0]==2;
        if (operation==5 || operation==6 || operation==9 || operation==14 || right_enters_page) {
            void *manager=manager_get(app());
            u32 previous_id=*(volatile u32 *)manager;
            if(operation==6 && controller[0x150/4]==0x10000000u){storage->status=2;storage->completed_sequence=sequence;continue;}
            if (operation==9 || operation==14) {
                if(operation==14 && controller[0]!=0x53d950e8u){storage->status=2;storage->completed_sequence=sequence;continue;}
                body[0]=operation==14 ? 0x8e : 0x8f;
                if(controller[0]==0x53d950e8u) {
                    void *op=((void *(*)(void *))0x5323f49cu)(app());
                    ((void (*)(void *,u32 *))0x5325b644u)(op,body);
                } else ((void (*)(void *,u32 *))(((u32 *)controller[0])[10]))(controller,body);
            } else if (operation==5) {
                body[0]=0xe0;
                ((void (*)(void *,u32 *))(((u32 *)controller[0])[10]))(controller,body);
            } else if (operation==6) {
                ((void (*)(void *))0x53182e9cu)(controller);
            } else {
                body[0]=0xd5;
                ((void (*)(void *,u32 *))(((u32 *)controller[0])[10]))(controller,body);
            }
            log("[UI native transition requested]\n");
            /* The original AppMain consumes its real transition queue. */
            u32 lo,hi,start,now;
            __asm__ volatile("mrrc p15,0,%0,%1,c14":"=r"(lo),"=r"(hi));start=lo;
            do {__asm__ volatile("yield");__asm__ volatile("mrrc p15,0,%0,%1,c14":"=r"(now),"=r"(hi));} while(*(volatile u32 *)manager==previous_id && now-start<200000000u);
            if(*(volatile u32 *)manager==previous_id){storage->status=3;storage->completed_sequence=sequence;continue;}
            /* Original UIManager task holds its native dispatch mutex across
             * transition/Show. Acquiring/releasing it waits for real completion. */
            ((void (*)(void *,u32))0x538dfe24u)((u8 *)manager+0x38,0xffffffffu);
            ((void (*)(void *))0x538dff78u)((u8 *)manager+0x38);
            controller=current(manager);
            storage->current_controller=(u32)controller;
            storage->current_id=*(u32 *)manager;
            storage->current_canvas=controller[1];
            log("[UI original current obtained]\n");
        }
        if (operation==1 || operation==2 || operation==7 || (operation==8 && !right_enters_page)) {
            body[0]=operation==1 ? 0xd7 : operation==2 ? 0xd6 : operation==7 ? 0xd4 : 0xd5;
            ((void (*)(void *,u32 *))(((u32 *)controller[0])[10]))(controller,body);
            /* Original completion/cancellation primitive used by the next
             * real key. TopMenu stores its list at +0x1ac; SelectMenu's
             * original Up/Down handlers operate on the list at +0x198.
             * ADJ Mode Settings has a third original list at +0x170.
             * Full RTOS animation timing is not modeled here. */
            if(controller[0]==0x53d9c3f8u)settle((void *)controller[0x1ac/4]);
            if(controller[0]==0x53da2c70u)settle((void *)controller[0x198/4]);
            if(controller[0]==0x53da2af8u)settle((void *)controller[0x174/4]);
            if(controller[0]==0x53da5e00u)settle((void *)controller[0x17c/4]);
            if(controller[0]==0x53d9f6a0u)settle((void *)controller[0x1f8/4]);
            if(controller[0]==0x53da6680u)settle((void *)controller[0x170/4]);
        }
        if(operation==10) {
            if(controller[0]!=0x53d950e8u){storage->status=2;storage->completed_sequence=sequence;continue;}
            render_manager=manager_get(app());
            ((void (*)(void *,u32))0x538dfe24u)((u8 *)render_manager+0x38,0xffffffffu);
            body[0]=0xb9;
            void *op=((void *(*)(void *))0x5323f49cu)(app());
            ((void (*)(void *,u32 *))0x5325b644u)(op,body);
        }
        if(controller[0]==0x53d950e8u) {
            /* Assisted UI timer tick: original callback with its own original
             * timer handle, then original Update. No dirty flag is written. */
            ((void (*)(void *,u32))0x5318973cu)(controller,controller[0x2d8/4]);
            ((void (*)(void *))(((u32 *)controller[0])[11]))(controller);
        }
        for(u32 layer=0;layer<4;layer++) {
            void *canvas=(void *)controller[(4+layer*48)/4];
            if(!canvas)continue;
            resize(canvas,bounds);bind(canvas,surface);dirty(canvas);draw(canvas);
        }
        if(render_manager)((void (*)(void *))0x538dff78u)((u8 *)render_manager+0x38);
        storage->status=1; storage->completed_sequence=sequence;
        log("[UI NATIVE EVENT COMPLETE]\n");
    }
}
