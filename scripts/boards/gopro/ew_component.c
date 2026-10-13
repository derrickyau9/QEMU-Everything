/* SPDX-License-Identifier: GPL-2.0-or-later
 * Authored, private original Embedded Wizard component harness.
 * No camera business state, source resources, glyphs or pixels are supplied.
 */
typedef unsigned int u32;
typedef unsigned char u8;
typedef struct { int x0,y0,x1,y1; } Rect;
typedef struct {
    u32 magic,stage,result,heap_next,alloc_count,free_count,failed_size;
    u32 fatal_code,exception,exception_lr,dfar,dfsr;
    u32 root,viewport,bitmap,canvas,width,height;
    Rect update;
    u32 frames,request,complete,operation,event_result;
    u32 reserved,mutex_created,mutex_wait,mutex_unlock,mutex_error;
    u32 log_bytes,log_calls,log_truncated;
    u32 display_updates,display_error,native_bitmap,native_pixels,native_stride;
    u32 pointer_x,pointer_y,fatal_caller;
} Oracle;
#define O ((volatile Oracle *)0xa9200000)
#define FN(addr,type) ((type)(addr))
#define HEAP_START 0xaa000000u
#define HEAP_END 0xae000000u
#define FRAMEBUFFER 0xaf000000u
void *lab_alloc(u32 size) {
    u32 start=(O->heap_next+63u)&~63u;
    u32 total=(size+63u)&~63u;
    if (!size || size>HEAP_END-HEAP_START || total<size || start>HEAP_END-total) {
        O->failed_size=size; return (void*)0;
    }
    O->heap_next=start+total; O->alloc_count++;
    return (void*)start;
}
void lab_free(void *ptr) { if(ptr) O->free_count++; }
void *lab_alloc_r(void* context,u32 size) { (void)context; return lab_alloc(size); }
void lab_free_r(void* context,void* ptr) { (void)context; lab_free(ptr); }
void *lab_calloc(u32 count,u32 size) {
    if(!count || !size || count>(HEAP_END-HEAP_START)/size) { O->failed_size=size;return 0; }
    u32 total=count*size;u8* ptr=lab_alloc(total);
    if(ptr)for(u32 i=0;i<total;i++)ptr[i]=0;
    return ptr;
}
void *lab_calloc_r(void* context,u32 count,u32 size) { (void)context;return lab_calloc(count,size); }
void *lab_camera_config(void) { return (void*)0xa5265e18; }
static u32 mutex_next=1;
static int object_valid(void* p) { u32 a=(u32)p;return a>=0xa4e00000u && a<=0xae000000u-40 && !(a&3u); }
int lab_mutex_create(u32* obj,const u8* name) {
    if(!object_valid(obj)) {O->mutex_error++;return -1;}
    obj[0]=mutex_next++; obj[9]=0;
    u8* dst=(u8*)(obj+1);u32 done=0;
    for(u32 i=0;i<32;i++) {u8 ch=done?0:name[i];if(!ch)done=1;dst[i]=ch;}
    O->mutex_created++;return 0;
}
int lab_mutex_wait(u32* obj,int timeout) {
    (void)timeout;
    if(!object_valid(obj) || !obj[0] || obj[9]==0xffffffffu) {O->mutex_error++;return -1;}
    /* Only CPU0 runs this component; CPU1/2 park and no guest tasks are
     * scheduled. The original mutex recursion count remains genuine. */
    obj[9]++; O->mutex_wait++;return 0;
}
int lab_mutex_unlock(u32* obj) {
    if(!object_valid(obj) || !obj[0] || !obj[9]) {O->mutex_error++;return -1;}
    obj[9]--;O->mutex_unlock++;return 0;
}
int lab_putc(int ch) {
    if(O->log_bytes>=0xffffu) {O->log_truncated++;return -1;}
    ((volatile u8*)0xa9201000)[O->log_bytes++]=(u8)ch;
    ((volatile u8*)0xa9201000)[O->log_bytes]=0;
    return ch;
}
int lab_log(const char* format,...) {
    __builtin_va_list args; __builtin_va_start(args,format);O->log_calls++;
    u32 before=O->log_bytes;
    FN(0xa0211490,void(*)(int(*)(int),const char*,__builtin_va_list))(lab_putc,format,args);
    __builtin_va_end(args);return (int)(O->log_bytes-before);
}
void lab_display_update(u32* context,int x,int y,int width,int height) {
    if(!context || context[0]!=O->width || context[1]!=O->height || context[2]!=FRAMEBUFFER || !context[3]) {
        O->display_error=1;return;
    }
    u32* native=(u32*)context[3];
    if(native[0]!=O->width || native[1]!=O->height || native[3]!=O->width*4 ||
       native[2]<HEAP_START || native[2]>HEAP_END-native[3]*native[1] ||
       x<0 || y<0 || width<0 || height<0 || x>(int)O->width-width || y>(int)O->height-height) {
        O->display_error=2;return;
    }
    O->native_bitmap=(u32)native;O->native_pixels=native[2];O->native_stride=native[3];
    const u32* src=(const u32*)native[2];u32* dst=(u32*)FRAMEBUFFER;
    /* Actual original raster output is copied to this software display.
     * No host glyph/icon/menu synthesis; use the original dirty extent. */
    for(int row=y;row<y+height;row++)for(int col=x;col<x+width;col++)
        dst[row*O->width+col]=src[row*O->width+col];
    O->display_updates++;
}
u32 lab_time(void) {
    u32 low,high; __asm__ volatile("mrrc p15, 0, %0, %1, c14" : "=r"(low), "=r"(high));
    /* Full64-bit counter, low32-bit milliseconds (usual49.7day wrap).
     * Long division avoids any unprovided compiler runtime helpers. */
    u32 rem=high%40000u,q=0;
    for(int bit=31;bit>=0;bit--) {rem=rem*2+((low>>bit)&1u);q<<=1;if(rem>=40000u){rem-=40000u;q|=1u;}}
    return q;
}
void lab_fatal(u32 code) {
    O->fatal_code=code;O->fatal_caller=(u32)__builtin_return_address(0);
    O->stage=0xf000;for(;;)__asm__ volatile("yield");
}
static void original_data_init(void) {
    const u32 *src=(const u32*)0xa3dd4940; u32 *dst=(u32*)0xa4e00000;
    for(u32 i=0;i<0x568240/4;i++) dst[i]=src[i];
    dst=(u32*)0xa5368300;
    for(u32 i=0;i<(0xa86e3878-0xa5368300)/4;i++) dst[i]=0;
}
static void drain_signals(void) {
    for(u32 pass=0;pass<64;pass++) {
        FN(0xa11d9238,u32(*)(void))();
        FN(0xa11d4e88,u32(*)(void))();
        if(!FN(0xa11d4f00,u32(*)(void))())return;
    }
    lab_fatal(6);
}
static void paint(void) {
    O->bitmap=(u32)FN(0xa11c4158,void*(*)(void*))((void*)O->viewport); O->stage=9;
    /* Original Tara creates this temporary original Canvas for each update;
     * it is detached before native collection instead of pinned forever. */
    O->canvas=(u32)FN(0xa11d2388,void*(*)(void*,u32))((void*)0xa2ae170c,0);
    O->stage=10;if(!O->bitmap || !O->canvas) lab_fatal(5);
    FN(0xa100bf28,void(*)(void*,void*))((void*)O->canvas,(void*)O->bitmap); O->stage=11;
    FN(0xa0ff7f94,void(*)(Rect*,void*,void*))((Rect*)&O->update,(void*)O->root,(void*)O->canvas);
    O->stage=12;
    FN(0xa100bf10,void(*)(void*))((void*)O->canvas); O->stage=13;
    FN(0xa11c42bc,void(*)(void*,Rect))((void*)O->viewport,*(Rect*)&O->update);
    /* Original Tara loop A0E5CA4C performs this native garbage collector
     * immediately after EndUpdate. Keep real original object lifecycle. */
    FN(0xa11d2c68,void(*)(void))();
    O->canvas=0;
    O->stage=14; O->frames++;
}
void component_main(void) {
    O->magic=0x47504557; O->stage=1; O->heap_next=HEAP_START;
    original_data_init(); O->stage=2;
    /* Original flags mask4 gates verbose printf UI-version diagnostics;
     * error mask1 is preserved. The auxiliary display has no RTOS stdout
     * heap/lock backend, so select the original non-verbose branch. */
    *(u8*)0xa5290fd0 &= (u8)~4u;
    /* Original firmware's own HERO8 static model record; the original
     * setter registers its actual version/name/table. No hardware ID flag
     * or camera business-ready state is invented. */
    FN(0xa0bce408,void(*)(void*))((void*)0xa5293118);
    /* Empty software NV device: create its actual SDK lock, leave its
     * original block table empty. Original read/erase fail and original
     * settings initializer selects its own ROM defaults/fallback. */
    lab_mutex_create((u32*)0xa7f80f04,(const u8*)0xa2866b04);
    FN(0xa0984ae8,int(*)(void))();
    FN(0xa084f3f4,void(*)(void))();
    FN(0xa084d320,void(*)(void))();
    O->width=*(u32*)0xa2a92128; O->height=*(u32*)0xa2a9212c;
    if (!O->width || !O->height || O->width>1920 || O->height>1080) lab_fatal(1);
    for(u32 i=0;i<O->width*O->height;i++) ((u32*)FRAMEBUFFER)[i]=0;
    O->stage=3;
    O->result=FN(0xa11c2c18,u32(*)(u32))(0); O->stage=4;
    if(!O->result) lab_fatal(2);
    O->root=(u32)FN(0xa11d2388,void*(*)(void*,u32))(*(void**)0xa2ad376c,0);
    O->stage=5; if(!O->root) lab_fatal(3);
    FN(0xa11d2a0c,void(*)(void*))((void*)O->root); O->stage=6;
    FN(0xa0ff7fb4,void(*)(void*,u32,u32))((void*)O->root,O->width,O->height);
    O->stage=7;
    Rect bounds={0,0,(int)O->width,(int)O->height};
    O->viewport=(u32)FN(0xa11c3e84,void*(*)(u32,u32,Rect,u32,u32,void*,void*,void*,void*))
        (O->width,O->height,bounds,0,255,(void*)FRAMEBUFFER,0,0,0);
    O->stage=8; if(!O->viewport) lab_fatal(4);
    FN(0xa11e8fc8,void(*)(void**))((void**)&O->root);
    drain_signals();paint();u32 previous_tick=lab_time();
    for(;;) {
        u32 seq=O->request;
        if(seq==O->complete) {
            u32 now=lab_time();
            if(now-previous_tick>=32u) {
                /* Tara advances CoreRoot cursor time as well as EW timers.
                 * This makes native touch/long-press state progress without
                 * waiting for an unrelated later input mailbox command. */
                FN(0xa0ff7fe0,void(*)(void*,u32))((void*)O->root,now);
                drain_signals();paint();previous_tick=now;
            }
            __asm__ volatile("yield");continue;
        }
        u32 op=O->operation,x=O->pointer_x,y=O->pointer_y;O->event_result=0;
        if(op>=1 && op<=4 && x<O->width && y<O->height) {
            O->stage=20;
            FN(0xa0ff7fe0,void(*)(void*,u32))((void*)O->root,lab_time());
            if(op==3) FN(0xa0ff7ff8,void(*)(void*,u32,u32))((void*)O->root,x,y);
            if(op==1 || op==2) O->reserved=FN(0xa0ff8024,u32(*)(void*,u32,u32,u32,u32))((void*)O->root,op==1,0,x,y);
            drain_signals();paint();
            if(!O->display_error)O->event_result=1;
        }
        __asm__ volatile("dmb sy");O->complete=seq;
    }
}
