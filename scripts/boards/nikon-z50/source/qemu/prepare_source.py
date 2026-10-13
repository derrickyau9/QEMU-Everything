# SPDX-License-Identifier: GPL-2.0-or-later
"""Idempotent Z50 overlay for upstream QEMU 10.2.4; no Z7 CPU patches."""
from pathlib import Path
import shutil
import sys

def prepare(root):
    if (root / 'VERSION').read_text().strip() != '10.2.4':
        raise ValueError('Requires upstream QEMU 10.2.4')
    shutil.copyfile(Path(__file__).with_name('nikon-z50.c'), root / 'hw/arm/nikon-z50.c')
    for name in ['nikon-z50-display.c','nikon-z50-gpu.h','nikon-z50-isp.h','nikon-z50-isp-pixels.h','z50-display-map.h']:
        shutil.copyfile(Path(__file__).with_name(name), root / 'hw/arm' / name)
    kconfig=root/'hw/arm/Kconfig'
    old='config NIKON_Z50_DISPLAY\n    bool\n    depends on TCG && ARM\n    select NIKON_Z50\n'
    new=old+'    select SDHCI\n'
    current=kconfig.read_text()
    if old in current and new not in current:
        kconfig.write_text(current.replace(old,new,1))
    for name, addition in {
        'hw/arm/meson.build': ["arm_ss.add(when: 'CONFIG_NIKON_Z50', if_true: files('nikon-z50.c'))\n", "arm_ss.add(when: 'CONFIG_NIKON_Z50_DISPLAY', if_true: files('nikon-z50-display.c'))\n"],
        'hw/arm/Kconfig': ['\nconfig NIKON_Z50\n    bool\n    depends on TCG && ARM\n    select GENERIC_LOADER\n    select A15MPCORE\n', '\n'+new],
        'configs/devices/arm-softmmu/default.mak': ['\nCONFIG_NIKON_Z50=y\n','\nCONFIG_NIKON_Z50_DISPLAY=y\n'],
    }.items():
        path = root / name
        original = path.read_text()
        modified=original
        for item in addition:
            if item.strip() not in modified:
                modified += item
        if modified != original:
            path.write_text(modified)
    # Adapted from WonderLantern: diagnostic GDB/qtest reads have no CPU.
    path = root / 'hw/intc/arm_gic.c'
    original = path.read_text()
    old = 'if (!qtest_enabled() && s->num_cpu > 1) {'
    new = 'if (!qtest_enabled() && current_cpu && s->num_cpu > 1) {'
    if old not in original and new not in original:
        raise ValueError('Unexpected GIC source')
    if old in original:
        path.write_text(original.replace(old, new, 1))
    # Z50 SD diagnostic: the stock driver ends a finished open-ended read with
    # CMD12 while the card is already back in the transfer state (the command
    # carries SDHC_CMD_DATA_PRESENT and the read used multi-block mode with
    # block count disabled). Real cards answer that with R1; this model answers
    # nothing, so the controller reports a command timeout and the driver
    # re-initialises instead of mounting. The tolerance stays behind an
    # environment variable so runs with and without it can be compared; the
    # branch adds no data, success or file-system result of its own.
    path = root / 'hw/sd/sd.c'
    original = path.read_text()
    marker = 'Z50_SD_CMD12_TRAN_STATE_RESPONSE'
    old_block = '''/* CMD12 */
static sd_rsp_type_t sd_cmd_STOP_TRANSMISSION(SDState *sd, SDRequest req)
{
    switch (sd->state) {
    case sd_sendingdata_state:
        sd->state = sd_transfer_state;
        return sd_r1b;
    case sd_receivingdata_state:
        sd->state = sd_programming_state;
        /* Bzzzzzzztt .... Operation complete.  */
        sd->state = sd_transfer_state;
        return sd_r1;
    default:
        return sd_invalid_state_for_cmd(sd, req);
    }
}'''
    new_block = old_block.replace(
        '    default:\n        return sd_invalid_state_for_cmd(sd, req);',
        '    default:\n        if (getenv("Z50_SD_CMD12_TRAN_STATE_RESPONSE")) {\n            return sd_r1;\n        }\n        return sd_invalid_state_for_cmd(sd, req);')
    if marker not in original:
        if old_block not in original:
            raise ValueError('Unexpected SD CMD12 source')
        path.write_text(original.replace(old_block, new_block, 1))
    print('Prepared Z50 board:', root)

if __name__ == '__main__':
    prepare(Path(sys.argv[1]).resolve())
