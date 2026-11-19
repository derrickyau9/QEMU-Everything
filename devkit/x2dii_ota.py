"""Read the final OTA on the host using decryption material supplied for this import."""
import hashlib
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


VERSION = '1.4.15.7'
CIM_SHA256 = '22e5211d63f20bb132e1b36cfb9f098e653bd9d3286bdf9f6316e4c9be8b103b'
ZIP_SHA256 = '2924f9da36a343003bc39c5e366b847ad45c324b1caf0f3697f68dee0d1a6463'
FINAL_ENTRY_NAME = f'{CIM_SHA256[:16]}-final'
HEADER_SIZE = 192
CHUNK = struct.Struct('<4sIIIQ8x')


class FinalOtaKeyRequired(ValueError):
    def __init__(self):
        super().__init__(f'X2D II {VERSION} requires your own ota.sig decryption key.')


def parse_key(text):
    """Accept exactly one AES-128 key without echoing invalid input."""
    try:
        key = bytes.fromhex(text.strip())
    except ValueError:
        key = b''
    if len(key) != 16:
        raise ValueError('Enter a 16-byte ota.sig decryption key in hexadecimal.')
    return key


def decrypt_zip(container, key, destination, task):
    """Read the single encrypted ZIP chunk of the fingerprint-pinned final release."""
    if len(key) != 16:
        raise ValueError('Expected a 16-byte ota.sig decryption key.')
    if len(container) < HEADER_SIZE + CHUNK.size or not container.startswith(b'IM*H'):
        raise ValueError('Invalid final ota.sig header.')
    version, size, _, header_size, signature_size, payload_size = struct.unpack_from('<6I', container, 4)
    chunk_count, = struct.unpack_from('<I', container, 156)
    chunk_id, offset, length, attributes, _ = CHUNK.unpack_from(container, HEADER_SIZE)
    start = header_size + signature_size
    if (version != 2 or size != len(container) or header_size != HEADER_SIZE + CHUNK.size
            or signature_size != 384 or start + payload_size != size or container[44:48] != b'TBIE'
            or chunk_count != 1 or chunk_id != b'0802' or offset != 0 or attributes != 0
            or not 4 <= length <= payload_size or (length + 31) // 32 * 32 != payload_size):
        raise ValueError('Unexpected layout in the final ota.sig.')

    unwrap = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    content_key = unwrap.update(container[48:64]) + unwrap.finalize()
    decryptor = Cipher(algorithms.AES(content_key), modes.CTR(bytes(16))).decryptor()
    fingerprint = hashlib.sha256()
    view = memoryview(container)
    with destination.open('xb') as output:
        for position in range(0, length, 1024**2):
            task.check()
            block = decryptor.update(view[start + position:start + min(length, position + 1024**2)])
            if position == 0 and not block.startswith(b'PK\x03\x04'):
                raise ValueError('The ota.sig key did not decrypt the expected final OTA.')
            fingerprint.update(block)
            output.write(block)
        decryptor.finalize()
    if fingerprint.hexdigest() != ZIP_SHA256:
        raise ValueError('The ota.sig key did not decrypt the expected final OTA.')
