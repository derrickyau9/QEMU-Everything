/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Exact X3 v1.1.4.1 GUI platform adapter. Original file is never modified. */
.syntax unified
.cpu cortex-a15
.fpu neon-vfpv4
.arm
.section .task_id,"ax"
movw r0,#0x1234
bx lr
.section .lcd_buffer,"ax"
ldr pc,[pc,#-4]
.word platform_lcd
.section .lcd_present,"ax"
ldr pc,[pc,#-4]
.word platform_present
.section .get_time,"ax"
ldr r0,1f
ldr r0,[r0]
bx lr
1: .word 0x1ffe0000
.section .delay,"ax"
ldr pc,[pc,#-4]
.word platform_delay
.section .gui_send,"ax"
ldr pc,[pc,#-4]
.word gui_dispatch
.section .gui_send_wait,"ax"
ldr pc,[pc,#-4]
.word gui_dispatch
.section .app_send,"ax"
b 0x7208cc
.section .bootstrap,"ax"
.global _start
_start:
cpsid if
ldr sp,=0x1fff0000
mrc p15,0,r0,c1,c0,2
orr r0,r0,#0xf00000
mcr p15,0,r0,c1,c0,2
isb
mov r0,#0x40000000
vmsr fpexc,r0
ldr r0,=0x1ffa0000
mcr p15,0,r0,c2,c0,0
mov r0,#0
mcr p15,0,r0,c2,c0,2
mov r0,#3
mcr p15,0,r0,c3,c0,0
mrc p15,0,r0,c1,c0,0
bic r0,r0,#2
orr r0,r0,#1
mcr p15,0,r0,c1,c0,0
isb
ldr r4,=0x1ffd0000
1:
ldmia r4!,{r0,r1}
cmp r0,#0
beq 2f
str r1,[r0]
b 1b
2:
dsb sy
isb
ldr r0,=0x8ca5f4
bx r0
gui_dispatch:
ldr r1,[r0]
ldr r2,=0x2ee84d8
mov r3,#21
1:
ldr ip,[r2],#8
cmp r1,ip
ldreq pc,[r2,#-4]
subs r3,r3,#1
bne 1b
mov r0,#1
bx lr
platform_delay:
push {r4,lr}
ldr r4,=0xdfff0000
str r0,[r4]
ldr r4,=0x1ffe0000
ldr r1,[r4]
add r1,r1,r0
str r1,[r4]
cmp r1,#1000
blo 4f
ldr r1,[r4,#0x10]
cmp r1,#0
beq 4f
mov r2,#0
str r2,[r4,#0x10]
cmp r1,#1
beq 2f
cmp r1,#2
bne 4f
add r0,r4,#0x40
ldr r3,=0x8cc6c8
blx r3
b 4f
2:
add r0,r4,#0x20
ldr r3,=0x56c780
blx r3
4:
mov r0,#0
pop {r4,pc}
platform_lcd:
ldr r0,=0x83299c4
ldr r1,[r0,#8]
ldr r2,[r0,#12]
lsl r1,r1,#2
movw r3,#1408
mla r1,r2,r3,r1
add r0,r1,#0x10000000
bx lr
platform_present:
ldr r0,=0xdfff0004
str r0,[r0]
mov r0,#0
bx lr
.ltorg
