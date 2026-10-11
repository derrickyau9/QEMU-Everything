"""Extract fingerprinted Sony Linux inputs through a bounded FAT12 directory.

This parser does not search for kernel magic or run firmware executables.
Other Sony versions need their own verified partition/component fingerprints.
"""
from pathlib import Path
import hashlib
import os
import stat
import struct
import tempfile

from .core import Task, checked_child, read_json, write_json

SOURCE_SHA256 = '394ac0daaeb9ce3f6ca0273a18c6af26505770e3b470f70d1d9f062e257bbe88'
PARTITION = ('payloads/0700_part_image/dev/nflasha3', 31006720,
             '5d8852b8c9cdfd94162baa867bc5aec36aef6ef35483a153b447274a98e651df')
INPUTS = {
    'kernel': ('VMLINUX.BIN', 'kernel.bin', 6955016, '5aa8ccf608b7e697b7ddb3fc78b00ebcb666236596e390c7d4517bcea72f474e'),
    'rootfs': ('INITRD.IMG', 'initrd.img', 5029888, '5bd6f0313198b7ddc3001faf07111ddf380f8e6fcfa3e76a033368ad311ae171'),
}
PROFILES = {
    'a7iv-2.00': {'id': 'a7iv-2.00', 'model': 'ILCE-7M4', 'version': '2.00',
                  'source_sha256': SOURCE_SHA256, 'source_size': 896712088,
                  'partition': PARTITION, 'inputs': INPUTS, 'kernel_version': '4.9.186-scel+'},
    'a7rv-4.00': {'id': 'a7rv-4.00', 'model': 'ILCE-7RM5', 'version': '4.00',
                  'source_sha256': '51e1176a255a9a4b4782dfbcfa6c5f332b71173bdfe9ed2bcf4fca6bcbc6c2e2',
                  'source_size': 754525608,
                  'partition': ('payloads/0700_part_image/dev/nflasha3', 31803392,
                                '7f5a573ce2d491035238f3a676562b49ae51eb6f21e4f6e92bacf4793f7bdce4'),
                  'inputs': {
                      'kernel': ('VMLINUX.BIN', 'kernel.bin', 6610952,
                                 'bdb129ad6806e3e33f1a1b9c094c0c97c4940bdea7aa9c09cef18f02e4012e66'),
                      'rootfs': ('INITRD.IMG', 'initrd.img', 5218304,
                                 '8a1ac5c8494fe6b4b8b46d46f88d11272fe42091bc4ce28393dc9eae6a0e649d')},
                  'kernel_version': '4.9.337-scel'},
}

def _path(device, relative):
    device = Path(device).absolute()
    path = checked_child(device, relative)
    lexical = device / relative.replace('\\', '/')
    for parent in (lexical, *lexical.parents):
        if not parent.exists(): continue
        meta = parent.lstat()
        if stat.S_ISLNK(meta.st_mode) or getattr(meta, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Sony lab path contains a link or junction')
    return path

def profile_for_device(device):
    """Select only known model/version source hashes; this performs no hashing."""
    try:
        source = read_json(_path(device, 'device.json')).get('sha256')
        return next((profile for profile in PROFILES.values()
                     if profile['source_sha256'] == source), None)
    except (OSError, ValueError, TypeError):
        return None

def profile_available(device):
    """Quick metadata eligibility; actual bytes are checked before execution."""
    return profile_for_device(device) is not None

def _verify_source(device, profile, task):
    path = _path(device, 'source/firmware.bin')
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != profile['source_size']:
            raise ValueError('Sony source size/type mismatch')
        total = 0
        while block := stream.read(1024**2):
            task.check(); total += len(block); sha.update(block)
            if total > profile['source_size']: raise ValueError('Sony source grew while hashing')
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after) or identity(after) != identity(path.stat()):
        raise ValueError('Sony source changed while hashing')
    if total != profile['source_size'] or sha.hexdigest() != profile['source_sha256']:
        raise ValueError('Sony source SHA-256 mismatch')

def _read_input(path, size, expected, task):
    task.check()
    with path.open('rb') as f:
        before = os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise ValueError('Sony component size/type mismatch')
        blocks = []
        remaining = size
        while remaining:
            task.check()
            block = f.read(min(1024**2, remaining))
            if not block: raise ValueError('Truncated Sony component')
            blocks.append(block); remaining -= len(block)
        after = os.fstat(f.fileno())
    same = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if same(before) != same(after) or same(after) != same(path.stat()):
        raise ValueError('Sony component changed while reading')
    data = b''.join(blocks)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Sony component SHA-256 mismatch')
    return data

def fat12_inputs(data, task=None):
    """Return the two named root files; reject invalid bounds/cluster chains."""
    task = task or Task(lambda _: None)
    if len(data) < 512 or len(data) > 64*1024**2 or data[510:512] != b'\x55\xaa':
        raise ValueError('Invalid bounded FAT12 boot sector')
    bps, spc, reserved, count, entries = struct.unpack_from('<HBHBH', data, 11)
    sectors = struct.unpack_from('<H', data, 19)[0] or struct.unpack_from('<I', data, 32)[0]
    fat_sectors = struct.unpack_from('<H', data, 22)[0]
    if bps not in (512,1024,2048,4096) or spc not in (1,2,4,8,16,32,64,128) or not 1<=count<=2 or not reserved or not fat_sectors or not entries:
        raise ValueError('Invalid FAT12 geometry')
    root_start = (reserved + count*fat_sectors)*bps
    root_end = root_start + entries*32
    data_start = root_start + ((entries*32+bps-1)//bps)*bps
    total = sectors*bps
    cluster_size = bps*spc
    clusters = (total-data_start)//cluster_size
    if not data_start<=total<=len(data) or not 1<=clusters<4085 or root_end>data_start:
        raise ValueError('Invalid FAT12 extent or cluster count')
    fat = data[reserved*bps:(reserved+fat_sectors)*bps]
    if (clusters+2)*3//2+1 > len(fat): raise ValueError('Truncated FAT12 allocation table')
    result, allocated, names = {}, set(), set()
    wanted = {'VMLINUX.BIN', 'INITRD.IMG'}
    for offset in range(root_start, root_end, 32):
        task.check(); entry = data[offset:offset+32]
        if entry[0] == 0: break
        if entry[0] == 0xe5 or entry[11] == 0xf or entry[11] & 0x18: continue
        try: name = entry[:8].decode('ascii').strip() + '.' + entry[8:11].decode('ascii').strip()
        except UnicodeDecodeError: raise ValueError('Invalid FAT12 short filename') from None
        if name in names: raise ValueError('Duplicate FAT12 root filename')
        names.add(name)
        if name not in wanted: continue
        cluster, size = struct.unpack_from('<H',entry,26)[0], struct.unpack_from('<I',entry,28)[0]
        if not 0<size<=total: raise ValueError('Invalid FAT12 file size')
        chain, seen = [], set()
        while 2<=cluster<0xff8:
            task.check()
            if cluster>=clusters+2 or cluster in seen or cluster in allocated:
                raise ValueError('FAT12 cluster chain loops, overlaps or escapes')
            seen.add(cluster); allocated.add(cluster)
            start = data_start+(cluster-2)*cluster_size
            chain.append(data[start:start+cluster_size])
            raw = struct.unpack_from('<H',fat,cluster+cluster//2)[0]
            cluster = raw>>4 if cluster&1 else raw&0xfff
        if cluster<0xff8 or len(chain)!=(size+cluster_size-1)//cluster_size:
            raise ValueError('Invalid FAT12 terminal cluster or allocation size')
        result[name] = b''.join(chain)[:size]
    if result.keys()!=wanted: raise ValueError('Sony FAT12 kernel/rootfs root entries are missing')
    return result

def prepare_components(device, task=None):
    """Return kernel/rootfs Path values after exact component validation."""
    task = task or Task()
    profile = profile_for_device(device)
    if profile is None: raise ValueError('Sony lab requires a verified model/version firmware fingerprint')
    _verify_source(device, profile, task)
    partition_info, inputs = profile['partition'], profile['inputs']
    output = _path(device, 'sony-lab-inputs')
    from .camera_research import unpack_device
    if not _path(device, 'extracted/extraction.json').is_file(): unpack_device(device, task)
    manifest = read_json(_path(device, 'extracted/extraction.json'))
    if manifest.get('source_sha256') != profile['source_sha256']: raise ValueError('Sony extraction source mismatch')
    artifacts = [a for a in manifest.get('artifacts',[]) if a.get('path') == partition_info[0]]
    if len(artifacts) != 1 or (artifacts[0].get('size'), artifacts[0].get('sha256')) != partition_info[1:]:
        raise ValueError('Sony main FAT partition is not the verified input')
    partition = _read_input(_path(device, 'extracted/'+partition_info[0]), *partition_info[1:], task)
    if not output.exists():
        files = fat12_inputs(partition, task)
        with tempfile.TemporaryDirectory(prefix='sony-components-', dir=Path(device)) as temporary:
            stage=Path(temporary)/'inputs';stage.mkdir()
            for name,(_,filename,size,digest) in inputs.items():
                content=files[inputs[name][0]]
                if len(content)!=size or hashlib.sha256(content).hexdigest()!=digest:
                    raise ValueError('Sony FAT12 component fingerprint mismatch')
                (stage/filename).write_bytes(content)
            write_json(stage/'components.json',{'profile':profile['id'], 'model':profile['model'],
                       'version':profile['version'], 'source_sha256':profile['source_sha256'],
                       'partition_sha256':partition_info[2],
                       'method':'FAT12 root directory and allocation chains','inputs':inputs})
            task.check();stage.rename(output)
    paths={}
    for name,(_,filename,size,digest) in inputs.items():
        path=_path(device,'sony-lab-inputs/'+filename)
        _read_input(path,size,digest,task);paths[name]=path
    return paths
