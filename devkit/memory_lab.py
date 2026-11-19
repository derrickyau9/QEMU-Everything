"""Evidence-derived RAM carveouts on QEMU virt, not an Eagle2 board model.

The camera address space is translated by 1 GiB. The 64 MiB RAM hole is
backed by QEMU but excluded with no-map; devices cannot be validated here.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import time

from pyfdt.pyfdt import FdtBlobParse, FdtNode, FdtProperty, FdtPropertyWords
from .core import ASSETS, read_json, write_json
from .desktop_session import DesktopSession
from .guest.prepare import _qemu

OFFSET = 0x40000000


def tree_from(data):
    return FdtBlobParse(io.BytesIO(data)).to_fdt()


def reg_pairs(prop):
    words = prop.words
    if len(words) % 4:
        raise ValueError('Expected 64-bit address and size cells')
    return [(words[i] << 32 | words[i+1], words[i+2] << 32 | words[i+3])
            for i in range(0, len(words), 4)]


def words64(*values):
    return [word for value in values for word in (value >> 32, value & 0xffffffff)]


def intervals(regions):
    return sorted((int(r['address'], 0), int(r['address'], 0)+r['size_bytes']) for r in regions)


def validate_profile(profile):
    ram = intervals(profile['dt_ram_regions'])
    reserved = intervals(profile['reserved_regions'])
    for name, entries in [('RAM', ram), ('reserved', reserved)]:
        if not entries or any(a >= b or a % 4096 or b % 4096 for a, b in entries):
            raise ValueError(f'Invalid {name} alignment or size')
        if any(b > c for (_, b), (c, _) in zip(entries, entries[1:])):
            raise ValueError(f'Overlapping {name} intervals')
    if ram[0][0] != 0:
        raise ValueError('This translation requires camera RAM starting at zero')
    if any(not any(a <= c and d <= b for a, b in ram) for c, d in reserved):
        raise ValueError('Reserved memory is outside described RAM')
    if sum(b-a for a, b in ram) != profile['dt_described_ram_bytes']:
        raise ValueError('RAM total disagrees with evidence')
    ion = [r for r in profile['reserved_regions'] if r['node'].split('/')[-1].startswith('ion')]
    if sum(r['size_bytes'] for r in ion) != profile['ion_reserved_bytes']:
        raise ValueError('ION reserved regions disagree with heap total')
    heaps = profile['ion_heaps']
    for h in heaps:
        if min(h['allocated_bytes'], h['free_bytes']) < 0 or h['allocated_bytes']+h['free_bytes'] != h['size_bytes']:
            raise ValueError('Invalid ION heap accounting')
    for key, field in [('size_bytes', 'ion_reserved_bytes'), ('allocated_bytes', 'ion_allocated_bytes_at_capture'),
                       ('free_bytes', 'ion_free_bytes_at_capture')]:
        if sum(h[key] for h in heaps) != profile[field]:
            raise ValueError(f'Heap sum disagrees with {field}')
    holes = [(b, c) for (_, b), (c, _) in zip(ram, ram[1:]) if b < c]
    return {'backing_bytes': ram[-1][1], 'ram_holes': holes,
            'reserved_bytes': sum(b-a for a, b in reserved),
            'unreserved_bytes_before_kernel': sum(b-a for a, b in ram)-sum(b-a for a, b in reserved)}


def import_evidence(directory):
    """Cross-check the summary against the captured binary DT, not just totals."""
    summary = (directory/'summary.json').read_bytes()
    binary = (directory/'running.dtb').read_bytes()
    profile = json.loads(summary)
    validate_profile(profile)
    tree = tree_from(binary)
    memory = []
    for path, obj in tree.get_rootnode().walk():
        if path.count('/') == 2 and path.startswith('/memory') and path.endswith('/reg'):
            memory += reg_pairs(obj)
    expected = [(a, b-a) for a, b in intervals(profile['dt_ram_regions'])]
    if sorted(memory) != expected:
        raise ValueError('Captured DT RAM ranges disagree with summary')
    real_reserved = tree.resolve_path('/reserved-memory')
    real_nodes = [x for x in real_reserved if isinstance(x, FdtNode)]
    if len(real_nodes) != len(profile['reserved_regions']):
        raise ValueError('Captured DT reserved node count disagrees')
    for region in profile['reserved_regions']:
        node = tree.resolve_path(region['node'])
        props = {x.name: x for x in node}
        if reg_pairs(props['reg']) != [(int(region['address'], 0), region['size_bytes'])]:
            raise ValueError('Captured DT reserved region disagrees')
        for name, field in [('no-map', 'no_map'), ('reusable', 'reusable')]:
            if (name in props) != region[field]:
                raise ValueError('Captured DT reservation flags disagree')
    cpus = [n for n in tree.resolve_path('/cpus') if isinstance(n, FdtNode) and n.name.startswith('cpu@')]
    if len(cpus) != 4 or any('arm,cortex-a55' not in next(p for p in n if p.name == 'compatible').strings for n in cpus):
        raise ValueError('Expected four captured Cortex-A55 CPU nodes')
    profile['lab'] = {'translation_offset': hex(OFFSET), 'cpu': 'cortex-a55', 'cpu_count': 4,
                      'source_summary_sha256': hashlib.sha256(summary).hexdigest(),
                      'source_dtb_sha256': hashlib.sha256(binary).hexdigest(),
                      'source_directory': str(directory.resolve()),
                      'physical_camera_access': False,
                      'limitations': ['QEMU virt board, not Eagle2 MMIO/interrupt routing',
                                     'RAM addresses translated by +0x40000000; RAM hole has virtual backing',
                                     'SDK kernel, not camera main kernel',
                                     'ION heap accounting is captured evidence, not an implemented ION driver',
                                     'No ISP/DSP/GPU/DMA/coherency/DDR timing emulation',
                                     'Original GUI uses mocked camera services; memory load differs from camera']}
    return profile


def add_reservations(tree, profile):
    info = validate_profile(profile)
    if tree.resolve_path('/reserved-memory') is not None:
        raise ValueError('Base DT already has reservations')
    root = FdtNode('reserved-memory')
    root.append(FdtPropertyWords('#address-cells', [2]))
    root.append(FdtPropertyWords('#size-cells', [2]))
    root.append(FdtProperty('ranges'))
    mapping = []
    entries = list(profile['reserved_regions'])
    for a, b in info['ram_holes']:
        entries.append({'node': f'/reserved-memory/ram-hole@{a:x}', 'address': hex(a),
                        'size_bytes': b-a, 'no_map': True, 'reusable': False})
    for region in entries:
        original = int(region['address'], 0)
        address = original+OFFSET
        name = region['node'].split('/')[-1].split('@')[0]+f'@{address:x}'
        node = FdtNode(name)
        node.append(FdtPropertyWords('reg', words64(address, region['size_bytes'])))
        if region['no_map']: node.append(FdtProperty('no-map'))
        if region['reusable']: node.append(FdtProperty('reusable'))
        root.append(node)
        mapping.append({**region, 'guest_node': '/reserved-memory/'+name, 'guest_address': hex(address)})
    tree.get_rootnode().append(root)
    return mapping


class MemoryDesktopSession(DesktopSession):
    def __init__(self, device, runtime, desktop, profile):
        self.memory_profile = profile
        self.memory_info = validate_profile(profile)
        self.pending_diagnostic_error = None
        self.diagnostic_deadline = 0
        self.diagnostic_complete = False
        self.probe_in_flight = False
        self.probe_deadline = 0
        super().__init__(device, runtime, desktop)

    def prepare_extra_payload(self):
        super().prepare_extra_payload()
        target = self.device/'payload-stage/camera/memory_probe.sh'
        target.write_bytes((ASSETS/'guest/memory_probe.sh').read_bytes().replace(b'\r\n', b'\n'))
        (self.device/'payload-stage/camera/memory_failure.sh').write_bytes(
            (ASSETS/'guest/memory_failure.sh').read_bytes().replace(b'\r\n', b'\n'))
        if self.desktop.get('headroom_helper'):
            import shutil
            shutil.copy2(self.desktop['headroom_helper'], self.device/'payload-stage/qe-memory-headroom')

    def build_command(self, *ports):
        args = super().build_command(*ports)
        backing = self.memory_info['backing_bytes']
        if backing % (1024**2): raise ValueError('Backing size is not in whole MiB')
        mib = str(backing//1024**2)
        base = self.device/'memory-base.dtb'
        self.task.run([_qemu(Path(self.runtime['qemu']), 'qemu-system-aarch64'),
                       '-machine', f'virt,dumpdtb={base}', '-cpu', 'cortex-a55', '-smp', '4',
                       '-m', mib, '-display', 'none'])
        tree = tree_from(base.read_bytes())
        android = tree_from((self.device/'android-virt.dtb').read_bytes())
        tree.get_rootnode().append(copy.deepcopy(android.resolve_path('/firmware')))
        mapping = add_reservations(tree, self.memory_profile)
        dtb = self.device/'memory-layout.dtb'
        dtb.write_bytes(tree.to_dtb())
        write_json(self.device/'memory-layout.json', {'profile': self.memory_profile, 'derived': self.memory_info,
                    'mapping': mapping, 'dtb_sha256': hashlib.sha256(dtb.read_bytes()).hexdigest(),
                    'gui_firmware': read_json(self.device/'device.json').get('version')})
        for flag, value in [('-cpu', 'cortex-a55'), ('-smp', '4'), ('-m', mib), ('-dtb', str(dtb))]:
            args[args.index(flag)+1] = value
        index = args.index('-append')+1
        args[index] = args[index].replace('loglevel=1 quiet', 'loglevel=4')
        write_json(self.device/'memory-command.json', args)
        return args

    def probe(self, stage):
        if stage not in ('camera', 'desktop', 'returned'):
            raise ValueError('Unknown memory probe stage')
        self.commands.put(f'sh /mnt/x2dii/camera/memory_probe.sh {stage}')

    def match_headroom(self):
        target = self.memory_profile['linux_memavailable_bytes_at_capture']//1024
        self.commands.put(f'/mnt/x2dii/qe-memory-headroom {target} &')

    def on_guest_line(self, line):
        if line.rstrip().endswith('QE_PROBE_END'):
            self.probe_in_flight = False
        if line.rstrip().endswith('QE_MEMORY_FAILURE_CAPTURED'):
            self.diagnostic_complete = True
        super().on_guest_line(line)

    def on_command_sent(self, command):
        if command.startswith('sh /mnt/x2dii/camera/memory_probe.sh '):
            self.probe_in_flight = True
            self.probe_deadline = time.monotonic()+20

    def can_send_command(self):
        # Changing the guest terminal echo mode can discard buffered input.
        # Wait for the checksummed snapshot to finish before the next command.
        return not self.probe_in_flight

    def session_step(self):
        if self.pending_diagnostic_error:
            if self.diagnostic_complete or time.monotonic() > self.diagnostic_deadline:
                raise self.pending_diagnostic_error
            return
        try:
            if self.probe_in_flight and time.monotonic() > self.probe_deadline:
                self.probe_in_flight = False
                raise TimeoutError('Guest memory snapshot did not complete')
            super().session_step()
        except (RuntimeError, TimeoutError) as exc:
            self.pending_diagnostic_error = exc
            self.diagnostic_deadline = time.monotonic()+15
            self.commands.put('sh /mnt/x2dii/camera/memory_failure.sh')


def parse_probes(console):
    import base64
    import re
    probes = {}
    transported = []
    for stage, body in re.findall(r'^QE_PROBE_BEGIN (\w+)\s*\n(.*?)^QE_PROBE_END\s*$', console, re.M | re.S):
        encoded = ''.join(re.findall(r'^QE_PROBE_DATA:([A-Za-z0-9+/=]+)\s*$', body, re.M))
        raw = base64.b64decode(encoded, validate=True)
        digest = re.search(r'^QE_PROBE_SHA ([0-9a-f]{64})\s*$', body, re.M)
        if not digest or hashlib.sha256(raw).hexdigest() != digest[1]:
            raise ValueError(f'{stage}: memory snapshot transport checksum mismatch')
        transported.append(raw.decode('utf-8'))
    if transported: console = '\n'.join(transported)
    for stage, body in re.findall(r'^QE_MEMORY_BEGIN (\w+)\s*\n(.*?)^QE_MEMORY_END\s*$', console, re.M | re.S):
        sections = re.split(r'^QE_MEMORY_SECTION (\w+)\s*$', body, flags=re.M)
        fields = {sections[i]: sections[i+1].strip() for i in range(1, len(sections), 2)}
        meminfo = {k: int(v) for k, v in re.findall(r'^(\w+):\s+(\d+) kB', fields.get('meminfo', ''), re.M)}
        probes[stage] = {'fields': fields, 'meminfo_kib': meminfo, 'transport_checksum_verified':bool(transported)}
    return probes


def verify_probes(console, layout):
    """Check the live kernel DT, boot identity, CPU count and reserved PFNs."""
    import base64
    import re
    try:
        probes = parse_probes(console)
    except ValueError as exc:
        return {'passed':False, 'errors':[str(exc)], 'snapshots':{}}
    errors = []
    if set(probes) != {'camera', 'desktop', 'returned'}:
        errors.append('Missing complete memory snapshots')
    totals, boots = set(), set()
    for stage, probe in probes.items():
        if not probe['transport_checksum_verified']:
            errors.append(f'{stage}: missing snapshot checksum')
        fields = probe['fields']
        totals.add(probe['meminfo_kib'].get('MemTotal'))
        boots.add(fields.get('boot_id'))
        try:
            encoded = ''.join(line[7:] for line in fields['dtb'].splitlines() if line.startswith('QE_DTB:'))
            tree = tree_from(base64.b64decode(encoded or fields['dtb'], validate=bool(encoded)))
            memory = []
            for path,obj in tree.get_rootnode().walk():
                if path.count('/') == 2 and path.startswith('/memory') and path.endswith('/reg'):
                    memory += reg_pairs(obj)
            if memory != [(OFFSET,layout['derived']['backing_bytes'])]:
                raise ValueError('Live RAM backing range differs from generated model')
            nodes = [n for n in tree.resolve_path('/reserved-memory') if isinstance(n,FdtNode)]
            if len(nodes) != len(layout['mapping']): raise ValueError('Live reservation count differs')
            cpus = [n for n in tree.resolve_path('/cpus') if isinstance(n,FdtNode) and n.name.startswith('cpu@')]
            if len(cpus) != 4 or any('arm,cortex-a55' not in next(p for p in n if p.name=='compatible').strings for n in cpus):
                raise ValueError('Live CPU model differs')
            for region in layout['mapping']:
                node = tree.resolve_path(region['guest_node'])
                props = {p.name: p for p in node}
                expected = (int(region['guest_address'], 0), region['size_bytes'])
                if reg_pairs(props['reg']) != [expected]: raise ValueError('reservation address/size differs')
                if ('no-map' in props) != region['no_map']: raise ValueError('no-map differs')
                if ('reusable' in props) != region['reusable']: raise ValueError('reusable differs')
            online = fields.get('online', '')
            if online != '0-3': raise ValueError(f'CPU online list: {online}')
            if probe['meminfo_kib']['MemTotal']*1024 >= layout['derived']['unreserved_bytes_before_kernel']:
                raise ValueError('Guest MemTotal exceeds unreserved RAM')
            # Older SDK kernels do not publish mapped carveouts as children of
            # System RAM in iomem. Use the kernel's memblock reserved list for
            # those; no-map intervals must not intersect System RAM at all.
            resources = []
            for line in fields['iomem'].splitlines():
                match = re.fullmatch(r'(\s*)([0-9a-f]+)-([0-9a-f]+) : (.*)', line)
                if match: resources.append((int(match[2],16), int(match[3],16)+1, match[4]))
            system = [(a,b) for a,b,n in resources if n == 'System RAM']
            explicit = [(int(a,16),int(b,16)+1) for a,b in re.findall(
                r'0x([0-9a-fA-F]+)\.\.0x([0-9a-fA-F]+)', fields.get('memblock_reserved',''))]
            if not system or not any(a for a,_,_ in resources): raise ValueError('No usable iomem addresses')
            for region in layout['mapping']:
                a = int(region['guest_address'], 0); b = a+region['size_bytes']
                if region['no_map'] and any(a<y and b>x for x,y in system):
                    raise ValueError(f'no-map overlaps System RAM: {region["guest_node"]}')
                for x,y in system:
                    lo, hi = max(a,x), min(b,y)
                    if lo >= hi: continue
                    chunks = sorted((max(lo,c),min(hi,d)) for c,d in explicit if c<hi and d>lo)
                    cursor = lo
                    for c,d in chunks:
                        if c>cursor: break
                        cursor=max(cursor,d)
                    if cursor<hi: raise ValueError(f'Reservation lacks memblock coverage: {region["guest_node"]}')
            probe['live_layout_verified'] = True
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            errors.append(f'{stage}: {exc}')
    if len(totals) != 1: errors.append('MemTotal changed during userspace switch')
    if len(boots) != 1 or None in boots: errors.append('Kernel boot ID changed or is absent')
    for stage, expected in [('desktop', 'T'), ('returned', 'S')]:
        fields = probes.get(stage, {}).get('fields', {})
        state = re.search(r'^State:\s+(\w)', fields.get('gui', ''), re.M)
        # Running (R) is also valid after resumption; SIGSTOP must be visible.
        if not state or (state[1] != 'T' if expected == 'T' else state[1] not in ('S','R')):
            errors.append(f'{stage}: original GUI state is not {expected}')
    return {'passed': not errors, 'errors': errors, 'snapshots': probes,
            'real_camera_memtotal_kib': layout['profile']['linux_memtotal_bytes']//1024,
            'ion_allocations_emulated': False}
