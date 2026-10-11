"""No Canon firmware fixtures are distributed; cipher fixtures are authored."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from devkit import canon_lab as lab
from devkit.core import Cancelled, Task


def quiet():
    return Task(lambda _: None)


def payload_fixture():
    payload = bytearray(104)
    struct.pack_into("<7I", payload, 4, len(payload), 2, 0, 2, 32, 24, 24)
    struct.pack_into("<6I", payload, 32, 1, 80, 12, 0xFF010000, 0, 1)
    struct.pack_into("<6I", payload, 56, 2, 92, 12, 0xF0001000, 0, 1)
    payload[80:] = b"authored-patches-content!"
    struct.pack_into("<I", payload, 0, (~sum(payload[4:])) & 0xFFFFFFFF)
    return bytes(payload)


def encrypted_fixture():
    payload = payload_fixture()
    seed, mixed, key = 0x12345678, bytes(range(32)), bytes(range(32, 64))
    size = (len(payload) + 15) & ~15
    offset = 0x120
    fir = bytearray(offset + 0x7C + size)
    struct.pack_into("<I", fir, 0x30, offset)
    struct.pack_into("<III", fir, offset, 12, 0x7C, 0x7C + size)
    header = offset + 12
    struct.pack_into("<4I", fir, header, size, len(payload), 0, seed)
    iv_key, iv_payload = b"a" * 16, b"b" * 16
    fir[header + 16:header + 32] = iv_key
    derived = lab._digest(struct.pack("<I", seed) + mixed + struct.pack("<I", len(payload)))
    cipher = Cipher(algorithms.AES(derived[:16]), modes.CBC(iv_key)).encryptor()
    fir[header + 32:header + 64] = cipher.update(key) + cipher.finalize()
    fir[header + 64:header + 80] = iv_payload
    fir[header + 80:header + 112] = lab._digest(key)
    cipher = Cipher(algorithms.AES(key[:16]), modes.CBC(iv_payload)).encryptor()
    fir[header + 112:] = cipher.update(payload + bytes(size - len(payload))) + cipher.finalize()
    return bytes(fir), mixed, payload


@pytest.fixture
def small_roms(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "ROM0_BYTES", 8192)
    monkeypatch.setattr(lab, "ROM1_BYTES", 131072)
    monkeypatch.setattr(lab, "KEY_HEADER_OFFSET", 1024)
    first, second = bytearray(b"R" * 8192), bytearray(b"S" * 131072)
    first[0] = 0
    second[-1] = 0
    struct.pack_into("<I", second, 1028, 64)
    second[1088:1120] = bytes(range(32))
    paths = tmp_path / "ROM0.BIN", tmp_path / "ROM1.BIN"
    paths[0].write_bytes(first)
    paths[1].write_bytes(second)
    return paths


def test_rom_import_is_copy_and_never_boot_authentication(tmp_path, small_roms):
    output = tmp_path / "out"
    output.mkdir()
    original = tuple(path.read_bytes() for path in small_roms)
    result = lab.import_roms(*small_roms, output, quiet())
    assert not result["stock_boot_verified"] and not result["stock_ui_verified"]
    assert "user assertion" in result["model"]
    assert (output / "50D/ROM0.BIN").read_bytes() == original[0]
    assert (output / "50D/ROM1.BIN").read_bytes() == original[1]
    assert tuple(path.read_bytes() for path in small_roms) == original


@pytest.mark.parametrize("offset,value", [(1028, 0xFFFFFFFF), (1028, 0), (1028, 131060)])
def test_rom_seed_header_must_be_bounded(small_roms, offset, value):
    second = bytearray(small_roms[1].read_bytes())
    struct.pack_into("<I", second, offset, value)
    small_roms[1].write_bytes(second)
    with pytest.raises(ValueError, match="out of bounds"):
        lab.inspect_roms(*small_roms, quiet())


def test_erased_boot_vectors_and_seed_are_rejected(small_roms):
    second = bytearray(small_roms[1].read_bytes())
    second[1088:1120] = bytes(32)
    small_roms[1].write_bytes(second)
    with pytest.raises(ValueError, match="erased seed"):
        lab.inspect_roms(*small_roms, quiet())
    second[1088:1120] = bytes(range(32))
    second[-65536:] = b"\xff" * 65536
    small_roms[1].write_bytes(second)
    with pytest.raises(ValueError, match="boot/exception"):
        lab.inspect_roms(*small_roms, quiet())


def test_wrong_sizes_and_nonempty_output_are_rejected(tmp_path, small_roms):
    small_roms[0].write_bytes(b"too small")
    with pytest.raises(ValueError, match="size mismatch"):
        lab.inspect_roms(*small_roms, quiet())
    output = tmp_path / "out"
    output.mkdir()
    (output / "keep").write_text("user file")
    with pytest.raises(ValueError, match="empty"):
        lab.import_roms(*small_roms, output, quiet())
    assert (output / "keep").read_text() == "user file"


def test_hardlinked_input_is_rejected(tmp_path):
    original = tmp_path / "source"
    original.write_bytes(b"1234")
    duplicate = tmp_path / "duplicate"
    try:
        os.link(original, duplicate)
    except OSError:
        pytest.skip("Hard links unavailable")
    with pytest.raises(ValueError, match="one link"):
        lab._regular(original, quiet())


def test_symlink_output_or_input_is_rejected(tmp_path):
    original = tmp_path / "source"
    original.write_bytes(b"1234")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(original)
    except OSError:
        pytest.skip("Symbolic links unavailable")
    with pytest.raises(ValueError, match="symbolic link"):
        lab._regular(alias, quiet())
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        lab._destination(link)


def test_payload_decrypt_authenticates_key_and_corruption():
    fir, mixed, payload = encrypted_fixture()
    assert lab._decrypt_main(fir, mixed, quiet()) == payload
    assert len(lab._records(payload)) == 2
    with pytest.raises(ValueError, match="key digest"):
        lab._decrypt_main(fir, bytes(32), quiet())
    corrupted = bytearray(fir)
    corrupted[-17] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        lab._decrypt_main(bytes(corrupted), mixed, quiet())


@pytest.mark.parametrize("offset,value", [(0x12C, 0xFFFFFFF0), (0x130, 1), (0x130, 0)])
def test_crypto_lengths_are_checked_before_aes(offset, value):
    fir, mixed, _ = encrypted_fixture()
    fir = bytearray(fir)
    struct.pack_into("<I", fir, offset, value)
    with pytest.raises(ValueError, match="out of bounds"):
        lab._decrypt_main(bytes(fir), mixed, quiet())


@pytest.mark.parametrize("offset,value", [(0x10, 257), (0x14, 0xFFFFFFFF), (0x18, 20), (36, 1), (40, 0xFFFFFFFF), (60, 88), (56, 1)])
def test_record_bounds_overlaps_and_duplicate_tags(offset, value):
    payload = bytearray(payload_fixture())
    struct.pack_into("<I", payload, offset, value)
    with pytest.raises(ValueError):
        lab._records(bytes(payload))


def test_unknown_updater_is_never_executed(tmp_path, monkeypatch):
    updater = tmp_path / "unknown-updater"
    updater.write_bytes(bytes(1_695_520))
    output = tmp_path / "out"
    output.mkdir()
    called = []
    monkeypatch.setattr(lab.subprocess, "Popen", lambda *a, **k: called.append(a))
    with pytest.raises(ValueError, match="SHA-256"):
        lab.run_updater(updater, tmp_path / "qemu", output, quiet())
    assert not called and not any(output.iterdir())


def test_rom_cancel_cleans_partial_copy(tmp_path, small_roms):
    output = tmp_path / "out"
    output.mkdir()
    class CancelPartial(Task):
        def check(self):
            if (output / "50D/ROM0.BIN").exists():
                raise Cancelled("test cancellation")
    with pytest.raises(Cancelled):
        lab.import_roms(*small_roms, output, CancelPartial())
    assert not any(output.iterdir())
    assert all(path.exists() for path in small_roms)


def test_rom_changed_between_inspection_and_copy_is_rejected(tmp_path, small_roms, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    real = lab.inspect_roms
    def mutate(*args):
        result = real(*args)
        data = bytearray(small_roms[0].read_bytes())
        data[3] ^= 1
        small_roms[0].write_bytes(data)
        return result
    monkeypatch.setattr(lab, "inspect_roms", mutate)
    with pytest.raises(ValueError, match="changed"):
        lab.import_roms(*small_roms, output, quiet())
    assert not any(output.iterdir())


def test_missing_roms_and_qemu_plan_do_not_start_a_process(tmp_path, small_roms, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(FileNotFoundError):
        lab.qemu_command(tmp_path / "qemu", workspace, tmp_path / "sd", tmp_path / "cf", quiet())
    lab.import_roms(*small_roms, workspace, quiet())
    qemu = tmp_path / "qemu-system-arm"
    qemu.write_text("not executed")
    cards = tmp_path / "sd.img", tmp_path / "cf.img"
    for card in cards:
        with card.open("wb") as stream:
            stream.truncate(1024**2)
    called = []
    monkeypatch.setattr(lab.subprocess, "Popen", lambda *a, **k: called.append(a))
    command = lab.qemu_command(qemu, workspace, *cards, quiet())
    assert "50D,firmware=boot=0" in command and "none" in command
    assert all("snapshot=on" in item for item in command if item.startswith("if="))
    assert not called


def test_seed_oracle_hash_gate_is_before_optional_guest_engine():
    with pytest.raises(ValueError, match="verified"):
        lab._mix_seed(b"not an updater", bytes(32), quiet())


def test_official_updater_optional_guest_seed_vector():
    source = os.environ.get("CANON50D_TEST_UPDATER")
    if not source:
        pytest.skip("User-owned official updater fixture not supplied")
    # Unicorn catches a first-chance Windows address probe internally. pytest's
    # process-wide faulthandler prints this harmless native probe as a fatal
    # exception. Use an owned standalone interpreter for this optional vector.
    # The authored zero seed is NOT Canon's secret key.
    code = "from pathlib import Path; import sys; from devkit.canon_lab import _mix_seed; from devkit.core import Task; print(_mix_seed(Path(sys.argv[1]).read_bytes(), bytes(32), Task(lambda _: None)).hex())"
    result = subprocess.run([sys.executable, "-c", code, source], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fccd6fe0e0fb1f7574d7fc6046b9434eca1d721088ee9a22dfff78e08cbff914"


def test_updater_prelude_is_exact_and_does_not_embed_vendor_bytes():
    assert len(lab.PRELUDE) == 20
    assert lab.PRELUDE.hex() == "100f11ee020ac0e3100f01ee04f01fe520018000"


def mock_updater(tmp_path, monkeypatch):
    content = b"authored fake updater"
    updater = tmp_path / "updater.bin"
    updater.write_bytes(content)
    monkeypatch.setattr(lab, "UPDATER_SHA256", hashlib.sha256(content).hexdigest())
    real_regular = lab._regular
    monkeypatch.setattr(lab, "_regular", lambda path, task, **options: real_regular(path, task, expected=len(content)))
    qemu = tmp_path / "qemu-system-arm"
    qemu.write_text("never executed")
    output = tmp_path / "out"
    output.mkdir()
    return updater, qemu, output


def test_updater_exit_zero_is_not_a_boot_claim_and_workspace_cleans(tmp_path, monkeypatch):
    updater, qemu, output = mock_updater(tmp_path, monkeypatch)
    code = "import json,sys; print(json.dumps({'QMP': {}}),flush=True); item=json.loads(sys.stdin.readline()); print(json.dumps({'return':{},'id':item['id']}),flush=True)"
    real_popen = subprocess.Popen
    owned = []
    def launch(_command, **options):
        work = Path(options["cwd"])
        assert (work / "50D/ROM0.BIN").stat().st_size == lab.ROM0_BYTES
        assert (work / "50D/ROM1.BIN").stat().st_size == lab.ROM1_BYTES
        process = real_popen([sys.executable, "-c", code], **options)
        owned.append((process, work))
        return process
    monkeypatch.setattr(lab.subprocess, "Popen", launch)
    result = lab.run_updater(updater, qemu, output, quiet(), seconds=1)
    assert result["exit_code"] == 0
    assert not result["stock_boot_verified"] and not result["stock_ui_verified"]
    assert not result["updater_runtime_observed"]
    assert not owned[0][1].exists() and owned[0][0].poll() == 0
    assert (output / "canon-updater-probe.json").exists()


def test_updater_cancel_terminates_owned_child_before_work_cleanup(tmp_path, monkeypatch):
    updater, qemu, output = mock_updater(tmp_path, monkeypatch)
    real_popen = subprocess.Popen
    owned = []
    def launch(_command, **options):
        process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **options)
        owned.append((process, Path(options["cwd"])))
        return process
    monkeypatch.setattr(lab.subprocess, "Popen", launch)
    class CancelOwned(Task):
        def check(self):
            if owned:
                assert owned[0][1].exists()
                raise Cancelled("test owned child cancellation")
    with pytest.raises(Cancelled):
        lab.run_updater(updater, qemu, output, CancelOwned(), seconds=1)
    assert owned[0][0].poll() is not None
    assert not owned[0][1].exists()
    assert not any(output.iterdir())


def test_updater_reader_start_error_still_reaps_process(tmp_path, monkeypatch):
    updater, qemu, output = mock_updater(tmp_path, monkeypatch)
    real_popen = subprocess.Popen
    owned = []
    def launch(_command, **options):
        process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **options)
        owned.append(process)
        return process
    monkeypatch.setattr(lab.subprocess, "Popen", launch)
    monkeypatch.setattr(lab.threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("reader failed")))
    with pytest.raises(RuntimeError, match="reader failed"):
        lab.run_updater(updater, qemu, output, quiet(), seconds=1)
    assert owned[0].poll() is not None
    assert not any(output.iterdir())


def test_updater_qmp_line_limit_reaps_child_and_removes_artifacts(tmp_path, monkeypatch):
    updater, qemu, output = mock_updater(tmp_path, monkeypatch)
    real_popen = subprocess.Popen
    owned = []
    def launch(_command, **options):
        process = real_popen([sys.executable, "-c", "import sys,time; sys.stdout.write('x'*70000+'\\n');sys.stdout.flush();time.sleep(30)"], **options)
        owned.append(process)
        return process
    monkeypatch.setattr(lab.subprocess, "Popen", launch)
    with pytest.raises(ValueError, match="QMP output limit"):
        lab.run_updater(updater, qemu, output, quiet(), seconds=1)
    assert owned[0].poll() is not None
    assert not any(output.iterdir())
