/* SPDX-License-Identifier: GPL-2.0-or-later */
ENTRY(_start)
PHDRS {
 get_time PT_LOAD FLAGS(5); delay PT_LOAD FLAGS(5); task_id PT_LOAD FLAGS(5);
 lcd_buffer PT_LOAD FLAGS(5); send_wait PT_LOAD FLAGS(5); send PT_LOAD FLAGS(5);
 app_send PT_LOAD FLAGS(5); lcd_present PT_LOAD FLAGS(5); bootstrap PT_LOAD FLAGS(5);
}
SECTIONS {
 .get_time 0x5975bc : { *(.get_time) } :get_time
 .delay 0x5975d0 : { *(.delay) } :delay
 .task_id 0x597604 : { *(.task_id) } :task_id
 .lcd_buffer 0x597728 : { *(.lcd_buffer) } :lcd_buffer
 .lcd_present 0x597744 : { *(.lcd_present) } :lcd_present
 .gui_send_wait 0x8cec64 : { *(.gui_send_wait) } :send_wait
 .gui_send 0x8cecd8 : { *(.gui_send) } :send
 .app_send 0x8ced44 : { *(.app_send) } :app_send
 .bootstrap 0x1ffc0000 : { *(.bootstrap) } :bootstrap
 /DISCARD/ : { *(.ARM.attributes) }
}
