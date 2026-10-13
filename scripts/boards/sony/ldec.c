/* Independent reconstruction from actual native resource fixture structure.
 * Copyright (c) 2026 QEMU Everything contributors.
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * Evidence: image_02_43.uxc, 306 ordinary image records, every stream
 * consumed completely and every output matched align16(width)*height*2.
 * These bounds and validation checks are ours, not Sony firmware code.
 * This is a codec, not a claim of full original camera system boot.
 */
#include "ldec.h"
#ifdef QE_LDEC_FREESTANDING
/* Generic Linux errno ABI for the target aarch64 guest.  This branch avoids
 * target libc headers; it does not call libc or change the decoder format.
 */
typedef __UINTPTR_TYPE__ uintptr_t;
#define UINTPTR_MAX __UINTPTR_MAX__
#define INT_MAX __INT_MAX__
#define EINVAL 22
#define EFBIG 27
#define EBADMSG 74
#define ENOTSUP 95
#else
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#endif

#define QE_LDEC_LIMIT ((size_t)64 * 1024 * 1024)
#define QE_LDEC_BLOCK ((size_t)4096)

int qe_ldec_decode(const void *source, size_t source_length,
                   void *destination, size_t expected)
{
    const unsigned char *src = (const unsigned char *)source;
    unsigned char *dst = (unsigned char *)destination;
    size_t input = 0, output = 0;
    uintptr_t sp = (uintptr_t)source, dp = (uintptr_t)destination;

    if (!src || !dst || !source_length || !expected)
        return -EINVAL;
    if (source_length > QE_LDEC_LIMIT || expected > QE_LDEC_LIMIT ||
        expected > INT_MAX)
        return -EFBIG;
    if (sp > UINTPTR_MAX - source_length || dp > UINTPTR_MAX - expected)
        return -EINVAL;
    if (sp < dp + expected && dp < sp + source_length)
        return -EINVAL;

    while (output < expected) {
        size_t block_start = output;
        size_t block_size = expected - output;
        int ended = 0;
        if (block_size > QE_LDEC_BLOCK)
            block_size = QE_LDEC_BLOCK;
        if (input == source_length)
            return -EBADMSG;
        if (src[input++] != 0xf0)
            return -ENOTSUP;

        while (!ended) {
            unsigned flags, bit;
            if (input == source_length)
                return -EBADMSG;
            flags = src[input++];
            for (bit = 0; bit < 8; ++bit) {
                if (!(flags & (1u << bit))) {
                    if (input == source_length || output - block_start >= block_size)
                        return -EBADMSG;
                    dst[output++] = src[input++];
                } else {
                    unsigned token, code;
                    size_t distance, length, i;
                    if (source_length - input < 2)
                        return -EBADMSG;
                    token = ((unsigned)src[input] << 8) | src[input + 1];
                    input += 2;
                    if (!token) {
                        if (output - block_start != block_size)
                            return -EBADMSG;
                        ended = 1;
                        break;
                    }
                    code = token >> 12;
                    distance = token & 0xfffu;
                    length = code == 15 ? 64 : code == 14 ? 32 : code + 3;
                    if (!distance || distance > output - block_start ||
                        length > block_size - (output - block_start))
                        return -EBADMSG;
                    /* Bytewise copy is intentional: matches may overlap. */
                    for (i = 0; i < length; ++i) {
                        dst[output] = dst[output - distance];
                        ++output;
                    }
                }
            }
        }
    }
    if (input != source_length)
        return -EBADMSG;
    return (int)output;
}
