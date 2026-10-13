# Original Sony image-resource decoder

`ldec.c` is an independent software reconstruction of the **F0 LZ77 resource
variant** used by the original ILCE-7M4 2.00 UI assets. It allows a compatibility
backend to provide the original `/dev/ldec` decoder operation with actual
decompressed resource bytes. This component has passed its codec checks;
it does not establish that the complete Sony camera UI or camera system boots.

No firmware, compressed Sony asset, recovered image, encryption key, or
firmware disassembly is included here. The implementation and synthetic tests
use the project's AGPL-3.0-or-later license. The codec was inferred from locally
supplied original resources; no third-party decoder implementation was copied.

## API and build

```c
int qe_ldec_decode(const void *source, size_t source_length,
                   void *destination, size_t expected);
```

Success returns exactly `expected` bytes. An error returns a negative Linux
errno; discard all destination contents after an error. Both ranges are caller
owned, must have their declared capacities, and must not overlap. Input and
output are each limited to 64 MiB. The decoder allocates no memory and makes no
system calls. It does not pad a short decode or substitute missing pixels.

On an ordinary host:

```sh
cc -std=c11 -Wall -Wextra -Wconversion -Werror -fPIC -O2 -c ldec.c
```

A compatibility library can compile the codec without the target libc headers:

```sh
clang --target=aarch64-linux-gnu -DQE_LDEC_FREESTANDING -ffreestanding \
  -std=c11 -Wall -Wextra -Wconversion -Werror -fPIC -O2 -c ldec.c
```

For native LDEC ioctl 1, the original 32-byte parameter record contains the
source virtual address at +0, source length at +8, destination physical low
32 bits at +12, and expected output length at +16. The backend must resolve
the destination through its genuinely backed MA region. These addresses are
not interchangeable with host pointers. Remaining fields describe pages,
DMA channel, and cache flags; this codec only performs the byte decode.

## Verified stream format

Each block begins with byte `F0`. A flag byte follows. Its eight bits are read
from bit 0 to bit 7. A clear bit means a single literal byte; a set bit means a
two-byte big-endian reference token. A zero token terminates the block.

For a nonzero token:

| Field | Meaning |
| --- | --- |
| Low 12 bits | Backward distance in bytes, within the current block |
| High 4 bits, 0 through 13 | Length = code + 3 bytes |
| High 4 bits, 14 | Length = 32 bytes |
| High 4 bits, 15 | Length = 64 bytes |

Reference copies may overlap and are performed byte by byte. Every full block
produces exactly 4096 bytes. The last block produces exactly the remaining
expected bytes. History resets at each block. Each block requires its explicit
zero reference, including the last block, and the entire input must be consumed.
An unknown block header, bad distance, short input, output overflow, incomplete
block, or trailing bytes is rejected.

The verified image records have resource compression type 4 and stored pixel
format 0, which the original library resolves to GRM format 1 / GPE RGBA4444.
The native expected byte count is `align16(width) * height * 2`. The codec itself
does not interpret pixels. Compression type 6 and other stream-header variants
have not been independently verified. Image records with header bit 0x08000000
use another record layout and are outside this validation set.

## Evidence and limits

`ldec-validation-20261011.json` contains hashes and lengths for 306 original
resource streams. Native C decoded all 306 exactly, matching an independent
Python decoder byte for byte. The tests also rejected 8128 malformed cases,
checked 2000 reproducible mutations against the reference, and preserved
destination canaries. All 16 length codes occurred in this real validation set.
Original assets remain local and are not distributed in the evidence file.

The 600-by-2 resource produces 2432 bytes. The first three 70-by-60 resources
each produce 9600 bytes in blocks of 4096, 4096, and 1408 bytes. These sizes
also match the native alignment calculation. That is resource-decode evidence;
actual UI startup, graphics delivery, and original input still need their own
runtime verification.

Run the synthetic tests with `python -m pytest -q tests/test_sony_ldec.py`.
They compile and execute the C implementation on a native C toolchain, or in
the Debian WSL distribution on Windows. A host without either toolchain skips
the native-C execution test and still checks the packaged source and evidence.
