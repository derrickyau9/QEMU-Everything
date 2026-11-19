"""Cross-check the offline audit using synthetic chunks and independent AES."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from boot_audit import audit_image


def word_sum(data):
    padded = data + bytes(-len(data) % 4)
    return sum(x[0] for x in struct.iter_unpack("<I", padded)) & 0xffffffff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--crypto-tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Use a new evidence file")
    spec = importlib.util.spec_from_file_location("imah", args.crypto_tool)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    name = next(k for k in tool.keys if k.startswith("TBIE"))
    key = bytes(range(16))
    wrap = Cipher(algorithms.AES(tool.keys[name]), modes.ECB()).encryptor()
    wrapped = wrap.update(key) + wrap.finalize()
    # Deliberately unaligned chunk lengths test 32-byte padding and CTR continuity.
    kern = bytearray(67)
    kern[56:60] = b"ARM\x64"
    dtb = b"\xd0\x0d\xfe\xed" + bytes(41)
    chunks = [bytes(kern), dtb]
    plaintext = b"".join(x + bytes(-len(x) % 32) for x in chunks)
    encrypt = Cipher(algorithms.AES(key), modes.CTR(bytes(16))).encryptor()
    ciphertext = encrypt.update(plaintext) + encrypt.finalize()
    header = bytearray(256)
    header[:4] = b"IM*H"
    total = len(header) + 384 + len(ciphertext)
    struct.pack_into("<I", header, 4, 2)
    struct.pack_into("<I", header, 8, total)
    struct.pack_into("<4I", header, 16, len(header), 384, len(ciphertext), total)
    header[44:48] = b"TBIE"
    header[48:64] = wrapped
    struct.pack_into("<II", header, 152, word_sum(plaintext), 2)
    struct.pack_into("<4sIIIQ8s", header, 192, b"KERN", 0, len(kern), 0, 0x80000, bytes(8))
    struct.pack_into("<4sIIIQ8s", header, 224, b"LDTB", 96, len(dtb), 0, 0x100000, bytes(8))
    struct.pack_into("<I", header, 108, -(word_sum(header)+word_sum(ciphertext)) & 0xffffffff)
    fixture = bytes(header) + bytes(384) + ciphertext
    with tempfile.TemporaryDirectory(prefix="qe-hasselblad-audit-") as temporary:
        path = Path(temporary)/"synthetic.img"
        path.write_bytes(fixture)
        report = audit_image(path, tool)
        matches = [x["key_name"] for x in report["published_key_checks"] if x["plaintext_checksum_matches"]]
        assert matches == [name] and report["verified_main_kernel_candidate"]
        assert not report["signature_authenticated"]
        corrupt = bytearray(fixture)
        corrupt[-1] ^= 1
        path.write_bytes(corrupt)
        try:
            audit_image(path, tool)
        except ValueError as exc:
            assert "checksum failed" in str(exc)
        else:
            raise AssertionError("Corrupt ciphertext was accepted")
    args.output.write_text(json.dumps({
        "passed": True, "synthetic_fixture_sha256": hashlib.sha256(fixture).hexdigest(),
        "correct_key_identified": True, "other_published_keys_rejected": True,
        "padding_and_cross_chunk_ctr_checked": True, "corrupt_ciphertext_rejected": True,
        "signature_not_falsely_authenticated": True,
        "note": "Synthetic header/checksum validation only; this is not camera firmware boot evidence."
    }, indent=2), encoding="utf-8")
    print("PASS", args.output)


if __name__ == "__main__":
    main()
