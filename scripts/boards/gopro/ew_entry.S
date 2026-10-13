/* SPDX-License-Identifier: GPL-2.0-or-later */
.syntax unified
.cpu cortex-a7
.fpu neon
.arm
.section .entry,"ax"
.global _start
_start:
 cpsid if
 cps #0x17
 ldr sp,=0xa91ee000
 cps #0x1b
 ldr sp,=0xa91ed000
 cps #0x13
 ldr sp,=0xa91ff000
 mrc p15,0,r0,c1,c0,2
 orr r0,r0,#0x00f00000
 mcr p15,0,r0,c1,c0,2
 isb
 mov r0,#0x40000000
 vmsr fpexc,r0
 /* Original reset A0000334..0368 uses these original page tables and
  * explicitly clears SCTLR.A. Table8000 matches this emulated RAM extent.
  * With MMU off the architecture treats RAM as device memory, forbidding
  * the compiler's packed-object unaligned stores even with A cleared. */
 ldr r0,=0xa0008000
 mcr p15,0,r0,c2,c0,0
 mov r0,#0
 mcr p15,0,r0,c2,c0,2
 ldr r0,=0x55555555
 mcr p15,0,r0,c3,c0,0
 mrc p15,0,r0,c1,c0,0
 orr r0,r0,#0x1000
 orr r0,r0,#0x400
 orr r0,r0,#4
 bic r0,r0,#2
 orr r0,r0,#1
 mcr p15,0,r0,c1,c0,0
 dsb sy
 isb
 ldr r0,=vectors
 mcr p15,0,r0,c12,c0,0
 isb
 bl component_main
park:
 yield
 b park
.balign 32
vectors:
 b park
 b undefined
 b supervisor
 b prefetch
 b dataabort
 b park
 b irq
 b fiq
undefined: mov r0,#1; b fault
supervisor:
 push {r0-r3,r12}
 ldr r0,=0xa9200800
 stm r0,{r1-r3,r12}
 ldr r1,[sp]
 str r1,[r0,#16]
 add r1,sp,#20
 str r1,[r0,#20]
 pop {r0-r3,r12}
 mov r0,#2
 b fault
prefetch: mov r0,#3; b fault
dataabort: mov r0,#4; b fault
irq: mov r0,#6; b fault
fiq: mov r0,#7
fault:
 ldr r1,=0xa9200000
 str r0,[r1,#0x20]
 str lr,[r1,#0x24]
 mrc p15,0,r2,c6,c0,0
 str r2,[r1,#0x28]
 mrc p15,0,r2,c5,c0,0
 str r2,[r1,#0x2c]
 mov r0,#0xe000
 str r0,[r1,#4]
 b park
