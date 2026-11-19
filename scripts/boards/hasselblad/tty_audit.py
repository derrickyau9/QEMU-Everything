"""Read embedded kernel build configuration; no module loading or camera access."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from elftools.elf.elffile import ELFFile


OPTIONS = (
    "TTY", "VT", "VT_CONSOLE", "VT_HW_CONSOLE_BINDING", "FRAMEBUFFER_CONSOLE",
    "DUMMY_CONSOLE", "VGA_CONSOLE", "FB", "DRM", "DRM_FBDEV_EMULATION",
    "DRM_DJI", "DRM_DJI_VOP_V3", "UNIX98_PTYS", "INPUT_EVDEV", "HID",
    "USB_HID", "HID_GENERIC", "USB_XHCI_HCD", "USB_DWC3_DUAL_ROLE",
    "MAGIC_SYSRQ", "MAGIC_SYSRQ_DEFAULT_ENABLE", "IKCONFIG", "IKCONFIG_PROC",
    "SECURITY_SELINUX", "SECURITY_SELINUX_DEVELOP", "SECURITY_SELINUX_BOOTPARAM",
    "SECURITY_SELINUX_DISABLE",
)


def inspect(module, output):
    output.mkdir(parents=True, exist_ok=False)
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        syms = {s.name: s for s in elf.get_section_by_name(".symtab").iter_symbols()}
        start, end = syms["kernel_headers_data"], syms["kernel_headers_data_end"]
        if start["st_shndx"] != end["st_shndx"]:
            raise ValueError("Header archive boundaries cross sections")
        section = elf.get_section(start["st_shndx"]).data()
        if not 0 <= start["st_value"] < end["st_value"] <= len(section):
            raise ValueError("Invalid archive boundaries")
        archive = section[start["st_value"]:end["st_value"]]
        modinfo = elf.get_section_by_name(".modinfo").data().decode().split("\0")
    selected = {}
    # Read only fixed archive members; never extract paths supplied by the archive.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        for name in ("autoconf.h", "utsrelease.h", "compile.h"):
            member = tar.getmember("./include/generated/" + name)
            if not member.isfile() or member.size > 1024**2:
                raise ValueError("Unexpected header member")
            data = tar.extractfile(member).read()
            (output/name).write_bytes(data)
            selected[name] = data.decode()
    config = dict(re.findall(r"^#define (CONFIG_\w+)\s+(.+)$", selected["autoconf.h"], re.M))
    states = {}
    for option in OPTIONS:
        key = "CONFIG_" + option
        states[key] = config.get(key, "module" if key+"_MODULE" in config else "not enabled")
    report = {
        "source": str(module.resolve()),
        "module_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "embedded_archive_sha256": hashlib.sha256(archive).hexdigest(),
        "autoconf_sha256": hashlib.sha256((output/"autoconf.h").read_bytes()).hexdigest(),
        "vermagic": next(x.partition("=")[2] for x in modinfo if x.startswith("vermagic=")),
        "utsrelease": selected["utsrelease.h"].strip(),
        "config": states,
        "physical_camera_access": False,
        "evidence_scope": "Embedded build configuration from the supplied firmware module; live kernel configuration and display ownership not inspected.",
        "interpretation": {
            "vt_compiled": config.get("CONFIG_VT") == "1",
            "vt_console_compiled": config.get("CONFIG_VT_CONSOLE") == "1",
            "framebuffer_console_compiled": any(k in config for k in (
                "CONFIG_FRAMEBUFFER_CONSOLE", "CONFIG_FRAMEBUFFER_CONSOLE_MODULE")),
            "usb_keyboard_support_compiled": config.get("CONFIG_USB_HID") == "1",
            "live_vt_switch_tested": False,
        },
    }
    (output/"result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.module, args.output)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
