/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Real freestanding firmware: every pixel is written by AArch64 CPU stores.
 * Surface/input are the generic research platform transport. The handshake
 * ports, registers, constants, entry and layout are generated independently.
 */
typedef unsigned int u32;
extern void wait_ready(u32 command);
static volatile u32 *const input = (volatile u32 *)0x0b100000;
static volatile u32 *const present = (volatile u32 *)0x0b100008;
static volatile u32 *const pixels = (volatile u32 *)0x0b110000;

static void rect(int x, int y, int w, int h, u32 color)
{
#ifdef MOLTEN_NO_PIXELS
    return;
#endif
    for (int j = y; j < y + h; j++)
        for (int i = x; i < x + w; i++)
            pixels[j * 80 + i] = color;
}

/* Three by five glyphs, original resources of this microfirmware. */
static const char letters[] = "MENUISOWBAF01234";
static const unsigned short glyphs[] = {
    0x7b6d, 0x79cf, 0x5b6d, 0x5b6f, 0x7497, 0x71c7, 0x7b6f,
    0x5b7d, 0x7aed, 0x2bed, 0x79e4, 0x7b6f, 0x2492, 0x73e7, 0x73cf, 0x5bc9
};
static void text(int x, int y, const char *str, u32 color)
{
    while (*str) {
        unsigned short bitmap = 0;
        for (unsigned int n = 0; n < sizeof(letters) - 1; n++)
            if (letters[n] == *str) { bitmap = glyphs[n]; break; }
        for (int j = 0; j < 5; j++)
            for (int i = 0; i < 3; i++)
                if (bitmap & (1u << (14 - j * 3 - i))) rect(x + i, y + j, 1, 1, color);
        x += 4;
        str++;
    }
}

static void draw(unsigned int page, unsigned int selection)
{
#ifdef MOLTEN_STATIC_UI
    page = selection = 0;
#endif
    rect(0, 0, 80, 48, 0xff14202c);
    rect(0, 0, 80, 10, page ? 0xff305c78 : 0xff30404c);
    text(4, 2, page ? "ISO" : "MENU", 0xfff4f4ec);
    const char *rows[] = {"ISO", "WB", "AF"};
    const char *values[] = {"100", "200", "400"};
    for (unsigned int i = 0; i < 3; i++) {
        rect(3, 12 + i * 11, 74, 9, selection == i ? 0xffb88438 : 0xff263744);
        text(8, 14 + i * 11, page ? values[i] : rows[i], 0xfff4f4ec);
    }
    *present = 1;
}

void menu_main(void)
{
#ifdef MOLTEN_IDLE_LOOP
    for (;;) { __asm__ volatile("nop"); }
#endif
    unsigned int page = 0, selection = 0;
    wait_ready(0);
    draw(page, selection);
    for (;;) {
        unsigned int key = *input;
        if (!key) { continue; }
        if (key == 1) { selection = (selection + 1) % 3; }
        if (key == 2) { page = 1; }
        if (key == 3) { page = 0; }
        wait_ready((page << 4) | selection);
        draw(page, selection);
    }
}
