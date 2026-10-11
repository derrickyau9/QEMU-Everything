"""Offline EOS 50D 1.0.9 lab: user-owned ROMs and verified FIR payloads.

No Canon ROM, private key, updater code or fabricated boot result is bundled.
The bounded ARM calculation executes the user's checksum-verified updater's
seed-mixing routine in Unicorn, not a native executable. This is deliberately
separate from automatic import: one exact tested updater is supported.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import queue
import stat
import struct
import subprocess
import tempfile
import threading
import time

from .camera_formats import canon
from .core import Task

EOS_PIN = "4b667a1d3c08ab7a55835d15ddbd884fa754946d"
FIR_SHA256 = "68a223845bd23a4aa35509ca3454f68a6128ef68c0f7d3dcc31ffa470690d298"
UPDATER_SHA256 = "fa7640db657dee18959259c4a285fefc9050918332cb844c67bb266cf4bab711"
FIR_BYTES = 9_520_908
ROM0_BYTES, ROM1_BYTES = 0x400000, 0x800000
KEY_HEADER_OFFSET = 0x7C0000  # physical ROM address 0xF87C0000
MAX_RECORDS = 256
PRELUDE = struct.pack("<5I", 0xEE110F10, 0xE3C00A02, 0xEE010F10, 0xE51FF004, 0x00800120)


def _stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _linked(path: Path) -> bool:
    return any(parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
               for parent in (path, *path.parents))


def _regular(path: Path, task: Task, *, expected: int | None = None, limit: int = 16 * 1024**2) -> bytes:
    path = Path(path).absolute()
    task.check()
    if _linked(path):
        raise ValueError("Canon lab input must not traverse a symbolic link")
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise ValueError("Canon lab input must be a regular file with one link")
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("Canon lab input must be a regular file with one link")
        if identity(initial) != identity(before):
            raise ValueError("Canon lab input changed before it was opened")
        if (expected is not None and before.st_size != expected) or not 0 < before.st_size <= limit:
            raise ValueError(f"Canon lab input size mismatch (expected {expected or f'1..{limit}'} bytes)")
        chunks = []
        remaining = before.st_size
        while remaining:
            task.check()
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                raise ValueError("Canon lab input was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(stream.fileno())
        current = path.lstat()
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or after.st_nlink != 1
                or identity(before) != identity(after) or identity(after) != identity(current) or _linked(path)):
            raise ValueError("Canon lab input changed while being read")
    return b"".join(chunks)


def _destination(path: Path) -> Path:
    path = Path(path).absolute()
    if _linked(path):
        raise ValueError("Canon lab destination must not traverse a symbolic link")
    if not path.is_dir() or any(path.iterdir()):
        raise ValueError("Canon lab destination must be an empty existing directory")
    return path


def _seed_from_rom(rom1: bytes) -> tuple[bytes, int]:
    if len(rom1) != ROM1_BYTES:
        raise ValueError("EOS 50D ROM1 must be exactly 8 MiB")
    offset = struct.unpack_from("<I", rom1, KEY_HEADER_OFFSET + 4)[0]
    # The updater copies 32 bytes relative to this fixed key-header base.
    if offset < 12 or KEY_HEADER_OFFSET + offset + 32 > len(rom1):
        raise ValueError("EOS 50D ROM1 key-header offset is out of bounds; 0xF87C0000 region is required")
    seed = rom1[KEY_HEADER_OFFSET + offset:KEY_HEADER_OFFSET + offset + 32]
    if seed in (bytes(32), b"\xff" * 32):
        raise ValueError("EOS 50D ROM1 has an erased seed region")
    return seed, KEY_HEADER_OFFSET + offset


def inspect_roms(rom0: Path, rom1: Path, task: Task) -> dict:
    """Exact-size checks; model identity still needs the FIR key-digest check."""
    first = _regular(rom0, task, expected=ROM0_BYTES)
    second = _regular(rom1, task, expected=ROM1_BYTES)
    _, seed_offset = _seed_from_rom(second)
    if len(set(first)) < 2 or len(set(second[-65536:])) < 2:
        raise ValueError("Canon ROMs are erased/incomplete; ROM1's final 64 KiB boot/exception-vector region is required")
    return {"model": "EOS 50D (user assertion; cryptographic pairing is not yet verified)",
            "rom0_bytes": len(first), "rom1_bytes": len(second),
            "rom0_sha256": hashlib.sha256(first).hexdigest(),
            "rom1_sha256": hashlib.sha256(second).hexdigest(),
            "seed_rom1_offset": seed_offset, "seed_physical_address": hex(0xF8000000 + seed_offset),
            "qemu_machine": "50D", "qemu_source_commit": EOS_PIN,
            "stock_boot_verified": False, "stock_ui_verified": False,
            "notes": ["Sizes and key-header bounds do not authenticate a Canon ROM or prove matching model/firmware",
                      "FIR payload key-digest verification is required to pair this seed with EOS 50D 1.0.9"]}


def import_roms(rom0: Path, rom1: Path, destination: Path, task: Task) -> dict:
    """Copy only user-owned dumps; refuse wrong sizes, links and overwrites."""
    destination = _destination(destination)
    info = inspect_roms(rom0, rom1, task)
    first = _regular(rom0, task, expected=ROM0_BYTES)
    second = _regular(rom1, task, expected=ROM1_BYTES)
    if hashlib.sha256(first).hexdigest() != info["rom0_sha256"] or hashlib.sha256(second).hexdigest() != info["rom1_sha256"]:
        raise ValueError("Canon ROM source changed after inspection")
    produced = []
    directory = destination / "50D"
    made_directory = False
    try:
        task.check()
        directory.mkdir()
        made_directory = True
        for name, content in (("ROM0.BIN", first), ("ROM1.BIN", second)):
            task.check()
            target = directory / name
            with target.open("xb") as stream:
                produced.append(target)
                for offset in range(0, len(content), 65536):
                    task.check()
                    stream.write(content[offset:offset + 65536])
        target = destination / "canon-rom-import.json"
        with target.open("x", encoding="utf-8") as stream:
            produced.append(target)
            json.dump(info, stream, indent=2)
            stream.write("\n")
    except BaseException:
        for target in produced:
            target.unlink(missing_ok=True)
        if made_directory:
            directory.rmdir()
        raise
    return info


def _mix_seed(updater: bytes, seed: bytes, task: Task) -> bytes:
    """Run only this pinned updater's pure 32-byte seed calculation."""
    if hashlib.sha256(updater).hexdigest() != UPDATER_SHA256 or len(seed) != 32:
        raise ValueError("Seed oracle supports only the verified EOS 50D 1.0.9 updater and a 32-byte seed")
    try:
        from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP
    except ImportError:
        raise ValueError("Canon seed calculation needs optional dependency unicorn==2.1.4") from None
    emulator = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    for address, size in ((0, 0x200000), (0x800000, 0x200000), (0x1000000, 0x100000),
                          (0x2000000, 0x100000), (0x3000000, 0x100000), (0x7000000, 0x1000)):
        emulator.mem_map(address, size)
    emulator.mem_write(0x800120, updater)
    data_offset = 0x9945A8 - 0x800120
    emulator.mem_write(0x1900, updater[data_offset:data_offset + 0xB394 - 0x1900])
    emulator.mem_write(0xB760, seed)
    heap, instructions = 0x3000000, 0

    def hook(machine, address, _size, _context):
        nonlocal heap, instructions
        instructions += 1
        if instructions % 256 == 0:
            task.check()
        if address == 0x89FF28:  # calloc
            size = machine.reg_read(UC_ARM_REG_R0) * machine.reg_read(UC_ARM_REG_R1)
            if not 0 < size <= 4096 or heap + size > 0x3100000:
                raise ValueError("Canon seed oracle exceeded its bounded heap")
            pointer = heap
            heap += (size + 15) & ~15
            machine.mem_write(pointer, bytes(size))
            machine.reg_write(UC_ARM_REG_R0, pointer)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
        elif address == 0x843884:  # free; this fixed calculation does not reuse allocations
            machine.reg_write(UC_ARM_REG_R0, 0)
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

    emulator.hook_add(UC_HOOK_CODE, hook)
    for register, value in ((UC_ARM_REG_R0, 0xB760), (UC_ARM_REG_R1, 0x1000000),
                            (UC_ARM_REG_R2, 32), (UC_ARM_REG_SP, 0x20FF000), (UC_ARM_REG_LR, 0x7000000)):
        emulator.reg_write(register, value)
    task.check()
    try:
        emulator.emu_start(0x8737A4, 0x7000000, timeout=2_000_000, count=100_000)
    except UcError as error:
        raise ValueError(f"Canon seed oracle stopped on an invalid guest operation: {error}") from error
    task.check()
    if emulator.reg_read(UC_ARM_REG_PC) != 0x7000000 or emulator.reg_read(UC_ARM_REG_R0) != 0:
        raise ValueError("Canon seed oracle did not return successfully within its limits")
    return bytes(emulator.mem_read(0x1000000, 32))


def _digest(data: bytes) -> bytes:
    return hashlib.sha1(data + b"\x01").digest() + hashlib.sha1(data + b"\x02").digest()[:12]


def _decrypt_main(fir: bytes, mixed_seed: bytes, task: Task) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    offset = struct.unpack_from("<I", fir, 0x30)[0]
    header = offset + 12
    encrypted_length, plaintext_length, _, seed = struct.unpack_from("<4I", fir, header)
    if (offset < 0x120 or header + 0x70 + encrypted_length != len(fir)
            or encrypted_length % 16 or not 0 < plaintext_length <= encrypted_length
            or encrypted_length - plaintext_length >= 16 or len(mixed_seed) != 32):
        raise ValueError("Canon payload cryptographic header is out of bounds")
    derived = _digest(struct.pack("<I", seed) + mixed_seed + struct.pack("<I", plaintext_length))
    cipher = Cipher(algorithms.AES(derived[:16]), modes.CBC(fir[header + 16:header + 32])).decryptor()
    key = cipher.update(fir[header + 32:header + 64]) + cipher.finalize()
    if _digest(key) != fir[header + 80:header + 112]:
        raise ValueError("Canon payload key digest mismatch; a matching user-owned ROM1/32-byte seed is required")
    cipher = Cipher(algorithms.AES(key[:16]), modes.CBC(fir[header + 64:header + 80])).decryptor()
    chunks = []
    for position in range(header + 112, len(fir), 65536):
        task.check()
        chunks.append(cipher.update(fir[position:position + 65536]))
    chunks.append(cipher.finalize())
    payload = b"".join(chunks)[:plaintext_length]
    if len(payload) < 32 or struct.unpack_from("<I", payload)[0] != (~sum(payload[4:]) & 0xFFFFFFFF):
        raise ValueError("Canon plaintext payload checksum mismatch")
    return payload


def _records(payload: bytes) -> list[dict]:
    if len(payload) < 32:
        raise ValueError("Canon plaintext record header is truncated")
    count, table, stride, _size_after = struct.unpack_from("<4I", payload, 0x10)
    if not 0 < count <= MAX_RECORDS or stride != 24 or table < 32 or table + count * stride > len(payload):
        raise ValueError("Canon plaintext record table is out of bounds")
    records, ranges, tags = [], [], set()
    for index in range(count):
        tag, offset, size, address, unknown, count_hint = struct.unpack_from("<6I", payload, table + index * stride)
        if tag in tags or not size or offset < table + count * stride or offset + size > len(payload):
            raise ValueError("Canon plaintext record entry is invalid")
        if any(offset < end and offset + size > begin for begin, end in ranges):
            raise ValueError("Canon plaintext records overlap")
        tags.add(tag)
        ranges.append((offset, offset + size))
        records.append({"index": index, "tag": tag, "offset": offset, "size": size,
                        "memory_address": hex(address), "unknown": unknown, "count_hint": count_hint,
                        "path": f"patch-{index:03d}.bin"})
    return records


def decrypt_payload(source: Path, destination: Path, task: Task, *, seed_file: Path | None = None, rom1: Path | None = None) -> dict:
    """Require an explicitly supplied seed/dump and verify before writing."""
    destination = _destination(destination)
    if (seed_file is None) == (rom1 is None):
        raise ValueError("Provide exactly one user-owned ROM1 dump or 32-byte seed file")
    fir = _regular(source, task, expected=FIR_BYTES)
    if hashlib.sha256(fir).hexdigest() != FIR_SHA256:
        raise ValueError("Full payload decryption currently supports only the verified official EOS 50D 1.0.9 FIR")
    if seed_file is not None:
        seed = _regular(seed_file, task, expected=32)
        seed_source = "user-supplied 32-byte file; secret bytes are not included in the report"
    else:
        seed, _ = _seed_from_rom(_regular(rom1, task, expected=ROM1_BYTES))
        seed_source = "user-supplied ROM1 key-header region at 0xF87C0000"
    updater_start = struct.unpack_from("<I", fir, 0x28)[0]
    updater_size = struct.unpack_from("<I", fir, 0xB0)[0]
    updater_seed = struct.unpack_from("<I", fir, 0xBC)[0]
    updater = canon._xor(fir[updater_start:updater_start + updater_size], updater_seed)
    payload = _decrypt_main(fir, _mix_seed(updater, seed, task), task)
    records = _records(payload)
    report = {"method": "EOS 50D 1.0.9 ROM-seeded AES-128-CBC payload decryption", "model": "EOS 50D", "version": "1.0.9",
              "input_sha256": FIR_SHA256, "updater_sha256": UPDATER_SHA256,
              "payload_sha256": hashlib.sha256(payload).hexdigest(), "payload_bytes": len(payload),
              "seed_source": seed_source, "checks": ["Exact official FIR and updater SHA-256", "Decrypted key 32-byte digest verified",
                                                        "Decrypted payload additive checksum verified", "Record table and all output bounds verified"],
              "records": records, "stock_boot_verified": False, "stock_ui_verified": False,
              "blockers": ["Patch records do not contain complete ROM0/ROM1; retain a user-owned baseline ROM pair",
                           "Record memory fields are reported as encoded; applying flash patches is not implemented"],
              "notes": ["No cryptographic firmware signature verification or physical flashing performed",
                        "Seed calculation executes only a bounded guest ARM function; no native vendor executable is run"]}
    produced = []
    try:
        for name, content in [("main-payload.decrypted.bin", payload)] + [(record["path"], payload[record["offset"]:record["offset"] + record["size"]]) for record in records]:
            task.check()
            path = destination / name
            with path.open("xb") as stream:
                produced.append(path)
                for offset in range(0, len(content), 65536):
                    task.check()
                    stream.write(content[offset:offset + 65536])
        path = destination / "canon-payload-report.json"
        with path.open("x", encoding="utf-8") as stream:
            produced.append(path)
            json.dump(report, stream, indent=2)
            stream.write("\n")
    except BaseException:
        for path in produced:
            path.unlink(missing_ok=True)
        raise
    return report


def qemu_command(binary: Path, workspace: Path, sd_image: Path, cf_image: Path, task: Task) -> list[str]:
    """Build an explicit custom-machine command using virtual card snapshots."""
    workspace = Path(workspace).absolute()
    inspect_roms(workspace / "50D/ROM0.BIN", workspace / "50D/ROM1.BIN", task)
    for image in (sd_image, cf_image):
        image = Path(image)
        if _linked(image.absolute()) or not image.is_file():
            raise ValueError("Canon virtual cards must be regular local image files")
        if image.stat().st_nlink != 1 or not 1024**2 <= image.stat().st_size <= 8 * 1024**3:
            raise ValueError("Canon virtual card size/link count is invalid")
        if "," in str(image.resolve()):
            raise ValueError("Canon virtual card path cannot contain a comma")
    if not Path(binary).is_file():
        raise ValueError("Custom qemu-eos executable is missing; stock QEMU does not implement -M 50D")
    return [str(Path(binary).resolve()), "-M", "50D,firmware=boot=0", "-drive", f"if=sd,file={Path(sd_image).resolve()},format=raw,snapshot=on",
            "-drive", f"if=ide,file={Path(cf_image).resolve()},format=raw,snapshot=on", "-nic", "none", "-monitor", "none"]


def run_updater(updater: Path, binary: Path, destination: Path, task: Task, *, seconds: float = 10) -> dict:
    """Bounded assisted updater execution; never reported as stock UI boot.

    Only the exact previously extracted updater is accepted. Synthetic ROMs,
    virtual cards and the guest prelude live in a temporary owned workspace.
    No user dump is replaced and no physical disk is exposed to QEMU.
    """
    destination = _destination(destination)
    content = _regular(updater, task, expected=1_695_520)
    if hashlib.sha256(content).hexdigest() != UPDATER_SHA256:
        raise ValueError("Updater execution accepts only the verified EOS 50D 1.0.9 updater SHA-256")
    if not 1 <= seconds <= 30:
        raise ValueError("Canon updater runtime limit must be 1..30 seconds")
    binary = Path(binary).absolute()
    if _linked(binary) or not binary.is_file():
        raise ValueError("A regular local custom qemu-eos executable is required")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    limit = 2 * 1024**2
    logs = {name: destination / name for name in ("serial.log", "qemu.log", "guest-assembly.log")}
    report = {"model": "EOS 50D", "version": "1.0.9", "qemu_source_commit": EOS_PIN,
              "updater_sha256": UPDATER_SHA256, "seconds_limit": seconds, "log_limit_per_file": limit,
              "guest_intervention": {"prelude_hex": PRELUDE.hex(), "load_address": "0x00800000",
                                     "branch_address": "0x00800120", "effect": "Clear SCTLR.V high-vector bit before original updater entry"},
              "roms": "Synthetic FF placeholders with explicit SYNTHETIC-LAB marker; not Canon ROM dumps",
              "rom0_bytes": ROM0_BYTES, "rom1_bytes": ROM1_BYTES, "card_mode": "32 MiB sparse virtual SD/CF, snapshot=on",
              "stock_boot_verified": False, "stock_ui_verified": False, "updater_runtime_observed": False,
              "notes": ["QEMU process exit zero is not kernel, ROM or camera UI boot verification",
                        "Updater display depends on original ROM properties/model/fonts; placeholders cannot supply them",
                        "Temporary updater/prelude/ROM/card files are removed after the owned process exits"]}
    messages = queue.Queue(maxsize=64)
    process = None
    reader = None
    reader_started = False
    produced = []
    try:
        with tempfile.TemporaryDirectory(prefix=".canon-updater-", dir=destination) as temporary, ExitStack() as process_owner:
            work = Path(temporary)
            rom_directory = work / "50D"
            rom_directory.mkdir()
            for name, size in (("ROM0.BIN", ROM0_BYTES), ("ROM1.BIN", ROM1_BYTES)):
                (rom_directory / name).write_bytes(b"SYNTHETIC-LAB!!!" + b"\xff" * (size - 16))
            for name in ("sd.img", "cf.img"):
                with (work / name).open("xb") as stream:
                    stream.truncate(32 * 1024**2)
            loader = work / "updater-with-prelude.bin"
            loader.write_bytes(PRELUDE + bytes(0x120 - len(PRELUDE)) + content)
            # Relative filenames avoid QEMU's comma-delimited device syntax.
            command = [str(binary), "-M", "50D", "-device", "loader,file=updater-with-prelude.bin,addr=0x800000,cpu-num=0",
                       "-drive", "if=sd,file=sd.img,format=raw,snapshot=on", "-drive", "if=ide,file=cf.img,format=raw,snapshot=on",
                       "-nic", "none", "-display", "none", "-monitor", "none", "-qmp", "stdio", "-serial", f"file:{logs['serial.log']}",
                       "-d", "in_asm", "-D", str(logs["guest-assembly.log"])]
            report["command"] = command
            report["environment"] = {"QEMU_EOS_WORKDIR": "temporary owned synthetic workspace (removed after run)"}
            start = time.monotonic()
            with logs["qemu.log"].open("xb") as error_log:
                produced.append(logs["qemu.log"])
                environment = dict(os.environ, QEMU_EOS_WORKDIR=str(work))
                task.check()
                process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=error_log, creationflags=flags)
                process_owner.callback(_stop_process, process)

                def receive():
                    total = 0
                    try:
                        while True:
                            line = process.stdout.readline(65537)
                            if not line:
                                break
                            total += len(line)
                            if len(line) > 65536 or total > 65536:
                                messages.put_nowait({"reader_error": "QMP output limit exceeded"})
                                break
                            messages.put_nowait(json.loads(line))
                    except (ValueError, queue.Full, OSError):
                        try:
                            messages.put_nowait({"reader_error": "Invalid or excessive QMP output"})
                        except queue.Full:
                            pass

                reader = threading.Thread(target=receive, daemon=True)
                reader.start()
                reader_started = True
                identifier = 0

                def query(name, arguments=None):
                    nonlocal identifier
                    identifier += 1
                    process.stdin.write(json.dumps({"execute": name, "arguments": arguments or {}, "id": identifier}).encode() + b"\n")
                    process.stdin.flush()
                    deadline = min(start + seconds + 2, time.monotonic() + 1)
                    while time.monotonic() < deadline:
                        task.check()
                        try:
                            response = messages.get(timeout=.05)
                        except queue.Empty:
                            continue
                        if "reader_error" in response:
                            raise ValueError(response["reader_error"])
                        if response.get("id") == identifier:
                            return response
                    return {"error": "QMP response timeout"}

                report["qmp_capabilities"] = query("qmp_capabilities")
                while process.poll() is None and time.monotonic() - start < seconds:
                    task.check()
                    if any(path.exists() and path.stat().st_size >= limit for path in logs.values()):
                        report["stop_reason"] = "log output limit"
                        break
                    time.sleep(.05)
                if process.poll() is None:
                    report["registers"] = query("human-monitor-command", {"command-line": "info registers"})
                    screen = destination / "updater-screen.ppm"
                    report["screen_capture"] = query("screendump", {"filename": str(screen)})
                    if screen.exists():
                        produced.append(screen)
                report.setdefault("stop_reason", "duration limit" if process.poll() is None else "process exited")
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                report["exit_code"] = process.returncode
                report["elapsed_seconds"] = round(time.monotonic() - start, 3)
            for path in logs.values():
                if path.exists():
                    if path not in produced:
                        produced.append(path)
                    if path.stat().st_size > limit:
                        with path.open("r+b") as stream:
                            stream.truncate(limit)
                        report.setdefault("truncated_logs", []).append(path.name)
            serial = logs["serial.log"].read_bytes() if logs["serial.log"].exists() else b""
            report["updater_runtime_observed"] = all(marker in serial for marker in (b"Welcome to Update program", b"Initialized", b"BackLightOn"))
            report["observed_missing_rom"] = any(marker in serial for marker in (b"NOT FOUND PROPERTY", b"NOT_FOUND", b"DS_MODELID =0x20202020"))
        task.check()
        target = destination / "canon-updater-probe.json"
        with target.open("x", encoding="utf-8") as stream:
            produced.append(target)
            json.dump(report, stream, indent=2)
            stream.write("\n")
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        # Cancel/error may arrive before QEMU-created paths were registered.
        for path in [*produced, *logs.values(), destination / "updater-screen.ppm"]:
            path.unlink(missing_ok=True)
        raise
    finally:
        if process is not None:
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()
        if reader is not None and reader_started:
            reader.join(timeout=1)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline Canon EOS 50D lab; user-owned ROMs only")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect-roms")
    copy = commands.add_parser("import-roms")
    for subparser in (inspect, copy):
        subparser.add_argument("--rom0", type=Path, required=True)
        subparser.add_argument("--rom1", type=Path, required=True)
    copy.add_argument("--output", type=Path, required=True)
    decrypt = commands.add_parser("decrypt")
    decrypt.add_argument("fir", type=Path)
    choice = decrypt.add_mutually_exclusive_group(required=True)
    choice.add_argument("--rom1", type=Path)
    choice.add_argument("--seed-file", type=Path)
    decrypt.add_argument("--output", type=Path, required=True)
    boot = commands.add_parser("plan-boot")
    boot.add_argument("--workspace", type=Path, required=True)
    boot.add_argument("--qemu", type=Path, required=True)
    boot.add_argument("--sd", type=Path, required=True)
    boot.add_argument("--cf", type=Path, required=True)
    run = commands.add_parser("run-updater")
    run.add_argument("updater", type=Path)
    run.add_argument("--qemu", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args(argv)
    task = Task(lambda _: None)
    try:
        if args.command == "inspect-roms":
            result = inspect_roms(args.rom0, args.rom1, task)
        elif args.command == "import-roms":
            result = import_roms(args.rom0, args.rom1, args.output, task)
        elif args.command == "decrypt":
            result = decrypt_payload(args.fir, args.output, task, seed_file=args.seed_file, rom1=args.rom1)
        elif args.command == "plan-boot":
            result = {"command": qemu_command(args.qemu, args.workspace, args.sd, args.cf, task),
                      "environment": {"QEMU_EOS_WORKDIR": str(args.workspace.absolute())},
                      "stock_boot_verified": False, "stock_ui_verified": False,
                      "notes": ["Verify that this custom QEMU advertises machine 50D before launch",
                                "Virtual SD/CF cards use snapshots; no physical device is accessed"]}
        else:
            result = run_updater(args.updater, args.qemu, args.output, task, seconds=args.seconds)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Canon lab: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
