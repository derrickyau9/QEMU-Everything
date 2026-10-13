/* Independently adapted from WonderLantern/z30-qemu, commit
 * 0ba0c3547ccbec09e7c861eaca35db8e82795f15 (itself ported from Z7).
 * Historical PCs/transfer comments below describe upstream observations,
 * not Z50 evidence unless explicitly marked Z50 C2.60. See ../../README.md. */
/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Inherited Z30/Z7 RAM workspace contract; not physical Z50 RAM capacity. */
static const struct { uint32_t base; uint64_t size; } z50_display_regions[] = {
    { 0x04008000, UINT64_C(0x1000) },
    { 0x04294000, UINT64_C(0x4000) },
    { 0x24010000, UINT64_C(0x1000) },
    { 0x40000000, UINT64_C(0x400000) },
    { 0x80000000, UINT64_C(0x10000) },
    /* Stock DMA ch2: 11ece380 -> 80100000, observed length 57e30. */
    { 0x80100000, UINT64_C(0x57e30) },
    /* Next stock resource transfer: 11f261b0 -> 80180000, length 5b990. */
    { 0x80180000, UINT64_C(0x5b990) },
    /* Stock DMA ch2: embedded firmware resource 11ea30a0 -> 80300000. */
    { 0x80300000, UINT64_C(0x27800) },
    { 0x80400000, UINT64_C(0x340000) },
    { 0x80800000, UINT64_C(0x1ddd500) },
    { 0x82600000, UINT64_C(0x61f7c00) },
    { 0x88800000, UINT64_C(0x1df5c00) },
    { 0x8a600000, UINT64_C(0x33462200) },
    { 0xbfb00000, UINT64_C(0x4ca400) },
    { 0xc0000000, UINT64_C(0xe9e0000) },
    { 0xcea00000, UINT64_C(0x63cb400) },
    { 0xd4e00000, UINT64_C(0x107e8000) },
    { 0xe5600000, UINT64_C(0x1a2eb400) },
};
static bool z50_display_ram_range(uint32_t address, uint64_t size)
{
    uint64_t end=(uint64_t)address+size;
    if (!size || end>UINT64_C(0x100000000)) { return false; }
    for (unsigned i=0;i<ARRAY_SIZE(z50_display_regions);i++) {
        if (address>=z50_display_regions[i].base &&
            end<=(uint64_t)z50_display_regions[i].base+z50_display_regions[i].size) { return true; }
    }
    return false;
}
