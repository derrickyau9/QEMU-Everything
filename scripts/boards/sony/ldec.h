/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Independent, bounded decoder inferred from user-provided ILCE-7M4 resources.
 * No firmware code or compressed resource bytes are embedded in this file.
 * Validated format: F0, 4 KiB LZ77 blocks, explicit zero reference terminator.
 */
#ifndef QE_LDEC_BACKEND_H
#define QE_LDEC_BACKEND_H
#ifdef QE_LDEC_FREESTANDING
typedef __SIZE_TYPE__ size_t;
#else
#include <stddef.h>
#endif

/* Returns exactly expected bytes on success, or a negative errno on failure.
 * The caller must discard destination contents following any error.
 * Source and destination must not overlap. Both ranges are caller owned.
 * This decoder supports the verified F0 resource variant only.
 */
int qe_ldec_decode(const void *source, size_t source_length,
                   void *destination, size_t expected);
#endif
