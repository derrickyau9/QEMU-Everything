"""Generate stripped AArch64 menu variants. Truth is for the evaluator only."""
from pathlib import Path
import argparse
import hashlib
import json
import random
import subprocess

HERE = Path(__file__).resolve().parent


def build(output, seed, variant='menu'):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(seed)
    variants = {'menu': [], 'completion-only': ['-DMOLTEN_NO_PIXELS'],
                'static-logo': ['-DMOLTEN_STATIC_UI'], 'idle-loop': ['-DMOLTEN_IDLE_LOOP'],
                'unsupported-window': []}
    if variant not in variants:
        raise ValueError('Unknown fixture variant')
    base = 0x40000000 + rng.randrange(1, 64) * 0x10000
    cfg, status = rng.sample(range(0x0b000000, 0x0b100000, 4), 2)
    mask = rng.randrange(1, 1 << 16) | 0x3f
    key = rng.randrange(1, 1 << 16)
    nonce = rng.randrange(1, 1 << 16)
    if not ((nonce ^ key) & mask):
        nonce ^= 1
    response, mask_reg, expected, write_ptr, read_ptr, command, temp = rng.sample(range(9, 18), 7)
    w = lambda i: f'w{i}'
    x = lambda i: f'x{i}'
    assembly = f'''.section .text.start,"ax"
.global _start
_start:
    ldr x0, =0x47f00000
    mov sp, x0
    bl menu_main
1:  b 1b
.text
.global wait_ready
wait_ready:
    mov {w(temp)}, #{nonce}
    eor {w(command)}, w0, {w(temp)}
    ldr {x(write_ptr)}, ={cfg}
    str {w(command)}, [{x(write_ptr)}]
    mov {w(temp)}, #{key}
    eor {w(expected)}, {w(command)}, {w(temp)}
    ldr {w(mask_reg)}, ={mask}
    and {w(expected)}, {w(expected)}, {w(mask_reg)}
    ldr {x(read_ptr)}, ={status}
poll:
    ldr {w(response)}, [{x(read_ptr)}]
    {''.join('nop; ' for _ in range(rng.randrange(0, 5)))}
    {f'rev {w(response)}, {w(response)}; rev {w(response)}, {w(response)}' if variant == 'unsupported-window' else ''}
    and {w(response)}, {w(response)}, {w(mask_reg)}
    cmp {w(response)}, {w(expected)}
    b.ne poll
    ret
.ltorg
'''
    (output / 'startup.S').write_text(assembly)
    offset = rng.randrange(0, 32) * 4
    (output / 'link.ld').write_text(f'''ENTRY(_start)
PHDRS {{ code PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }}
SECTIONS {{
 . = {base + offset};
 .text : {{ *(.text.start) *(.text*) }} :code
 .rodata : {{ *(.rodata*) }} :code
 .data : {{ *(.data*) }} :data
 .bss : {{ *(.bss*) *(COMMON) }} :data
 /DISCARD/ : {{ *(.comment) *(.note*) *(.eh_frame*) }}
}}
''')
    command_line = ['aarch64-linux-gnu-gcc', '-O1', '-ffreestanding', '-fno-builtin',
                    '-fno-stack-protector', '-fno-pie', '-mgeneral-regs-only', '-nostdlib',
                    '-static', '-no-pie', '-Wl,--build-id=none', '-Wl,-T,' + str(output / 'link.ld'),
                    *variants[variant], str(output / 'startup.S'), str(HERE / 'menu.c'), '-o', str(output / 'firmware.debug.elf')]
    subprocess.run(command_line, check=True)
    subprocess.run(['aarch64-linux-gnu-strip', '--strip-all', '-o', str(output / 'firmware.elf'),
                    str(output / 'firmware.debug.elf')], check=True)
    truth = dict(seed=seed, variant=variant, entry_region=base, config_address=cfg, status_address=status,
                 mask=mask, xor_value=key, command_nonce=nonce,
                 scope='research_fixture_menu', inputs={'next': 1, 'enter': 2, 'back': 3},
                 source_sha256=hashlib.sha256((HERE / 'menu.c').read_bytes()).hexdigest(),
                 firmware_sha256=hashlib.sha256((output / 'firmware.elf').read_bytes()).hexdigest())
    (output / 'truth.json').write_text(json.dumps(truth, indent=2) + '\n')
    print(output / 'firmware.elf')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--seed', required=True, type=int)
    parser.add_argument('--variant', default='menu', choices=('menu', 'completion-only', 'static-logo', 'idle-loop', 'unsupported-window'))
    args = parser.parse_args()
    build(args.output, args.seed, args.variant)
