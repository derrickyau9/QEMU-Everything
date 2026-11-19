"""Owned native QEMU process and synchronous, bounded, restartable execution."""
from pathlib import Path
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import zlib
from .contracts import SemanticGap, digest, identity, save_json
from .model import validate_model


class BackendFailure(RuntimeError):
    """A runner/process failure, distinct from exhausting a search budget."""


def png_from_bgra(path, raw, width, height):
    if len(raw) != width * height * 4:
        raise ValueError('Truncated native surface')
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            offset = (y * width + x) * 4
            rows.extend((raw[offset + 2], raw[offset + 1], raw[offset]))
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                          + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class Backend:
    def __init__(self, manifest):
        if os.name != 'posix':
            raise SemanticGap('Run this first research backend in Linux/WSL')
        self.manifest_path = Path(manifest).resolve()
        self.manifest = json.loads(self.manifest_path.read_text())
        if self.manifest.get('qemu_version') != '10.2.4' or self.manifest.get('machine') != 'molten-aarch64':
            raise ValueError('Need the pinned MOLTEN 10.2.4 build manifest')
        self.binary = Path(self.manifest['binary'])
        if digest(self.binary) != self.manifest['binary_sha256']:
            raise ValueError('QEMU binary changed since build')
        version = subprocess.check_output([str(self.binary), '--version'], text=True)
        if not version.startswith('QEMU emulator version 10.2.4'):
            raise ValueError('QEMU runtime version differs from manifest')

    def capabilities(self):
        return dict(isa='aarch64', mode='system', vcpus=1, accelerator='tcg,thread=single',
                    mmio='synchronous-before-load-commit', state_capture='local-registers+restart-prefix',
                    checkpoint='unsupported', replay='clean-restart', memory_model='baremetal-identity',
                    native_model_grammar=['constant', 'xor_latch'], pressure_max=2,
                    reserved_regions=self.manifest.get('reserved_regions'),
                    faults='timeout/log only; pre-fault bridge not implemented',
                    direct_pc_api=False, qemu_version='10.2.4')

    def run(self, firmware, directory, inputs=(), *, model=None, responder=None,
            max_events=256, wall_seconds=20, probe=False):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        if model is not None:
            validate_model(model)
            save_json(directory / 'model.json', model)
        if any(type(key) is not int or not 0 <= key <= 3 for key in inputs):
            raise ValueError('Research input events are integers 0..3')
        if not 1 <= max_events <= 1000000 or not 0 < wall_seconds <= 3600:
            raise ValueError('Invalid execution budget')
        result = dict(schema_version=1, directory=str(directory), reason='budget', frames=[],
                      input_deliveries=[], blockers=[], events=0, requested_inputs=list(inputs),
                      model_hash=identity(model) if model else None, checkpoint='unsupported')
        records, pending = [], list(inputs)
        repeated, last_key, idle_polls = 0, None, 0
        last_write = None
        last_cause = None
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='qe-molten-') as temporary:
            socket_path = str(Path(temporary) / 'bridge.sock')
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(socket_path)
            server.listen(1)
            server.settimeout(0.2)
            command = [str(self.binary), '-machine', 'molten-aarch64,secure=off,virtualization=off',
                       '-cpu', 'cortex-a57', '-accel', 'tcg,thread=single', '-smp', '1', '-m', '128M',
                       '-nodefaults', '-display', 'none', '-serial', 'none', '-monitor', 'none',
                       '-nic', 'none', '-icount', 'shift=0,sleep=off',
                       '-device', f'loader,file={firmware.path},cpu-num=0']
            if probe:
                plugin = Path(self.manifest['probe'])
                if digest(plugin) != self.manifest['probe_sha256']:
                    raise ValueError('Capability probe binary changed')
                command += ['-plugin', str(plugin)]
            environment = os.environ.copy()
            environment.pop('MOLTEN_MODEL', None)
            environment['MOLTEN_SOCKET'] = socket_path
            if model is not None:
                environment['MOLTEN_MODEL'] = str((directory / 'model.json').resolve())
            save_json(directory / 'command.json', command)
            connection = None
            with (directory / 'qemu.log').open('w') as log, (directory / 'events.jsonl').open('w') as events:
                process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
                try:
                    while connection is None:
                        if time.monotonic() - start >= wall_seconds:
                            raise TimeoutError('Native backend did not connect within wall budget')
                        if process.poll() is not None:
                            raise BackendFailure(f'Native backend exited: {(directory / "qemu.log").read_text()[-2000:]}')
                        try:
                            connection, _ = server.accept()
                        except socket.timeout:
                            continue
                    connection.settimeout(wall_seconds)
                    stream = connection.makefile('rb')
                    for index in range(max_events):
                        remaining = wall_seconds - (time.monotonic() - start)
                        if remaining <= 0:
                            result['reason'] = 'wall_budget'
                            break
                        connection.settimeout(remaining)
                        raw_message = stream.readline(1024 * 1024)
                        if not raw_message:
                            log.flush()
                            details = (directory / 'qemu.log').read_text()[-2000:]
                            raise BackendFailure('QEMU bridge ended before a bounded stop: ' + details)
                        event = json.loads(raw_message)
                        pc = int(event['pc'], 0)
                        if not firmware.is_code(pc):
                            raise SemanticGap(f'Event PC {pc:#x} is outside the input executable')
                        event.update(schema_version=1, campaign_id=directory.parent.name,
                                     world_id=directory.name, vcpu=0, address_space_id='baremetal:identity',
                                     component_sha256=firmware.sha256, pc_offset=pc - firmware.base,
                                     model_version=result['model_hash'],
                                     intervention_prefix='P1' if responder else ('P2' if model is not None else 'P0'),
                                     checkpoint='restart-prefix', causal_parent=last_cause)
                        response = 0
                        if event['kind'] == 'mmio_write':
                            last_write = dict(address=event['address'], value=event['value'], seq=event['seq'])
                            last_cause = event['seq']
                        elif event['kind'] in ('mmio_read_unresolved', 'mmio_read'):
                            event['preceding_write'] = last_write
                            if responder is not None:
                                response = responder(event)
                            key = (event['pc'], event['address'], tuple(event['registers']),
                                   last_write['value'] if last_write else None)
                            repeated = repeated + 1 if key == last_key else 1
                            last_key = key
                            if repeated >= 4:
                                result['reason'] = 'unresolved_poll'
                                result['blockers'].append(event)
                                response = 'stop'
                        elif event['kind'] == 'frame':
                            pixels = base64.b64decode(event.pop('bgra'), validate=True)
                            if not event['pixel_writes'] or not event['writers']:
                                raise ValueError('Present without CPU pixel writes is not graphical progress')
                            if not all(firmware.is_code(int(writer, 0)) for writer in event['writers']):
                                raise ValueError('Pixel writer outside original executable')
                            number = len(result['frames'])
                            frame_path = directory / f'frame-{number:03d}.png'
                            png_from_bgra(frame_path, pixels, event['width'], event['height'])
                            raw_path = frame_path.with_suffix('.bgra')
                            raw_path.write_bytes(pixels)
                            event.update(pixels_sha256=hashlib.sha256(pixels).hexdigest(),
                                         image=str(frame_path), raw_surface=str(raw_path))
                            result['frames'].append({k: event[k] for k in ('pixels_sha256', 'image', 'raw_surface', 'writers', 'pixel_writes')})
                            last_cause = event['seq']
                        elif event['kind'] == 'input_poll':
                            if pending:
                                response = pending.pop(0)
                                if response:
                                    result['input_deliveries'].append(response)
                                    last_cause = event['seq']
                            else:
                                idle_polls += 1
                                if idle_polls >= 3:
                                    response = 'stop'
                                    result['reason'] = 'inputs_complete'
                        else:
                            raise SemanticGap('Unknown backend event: ' + event['kind'])
                        if index == max_events - 1 and response != 'stop':
                            response = 'stop'
                            result['reason'] = 'event_budget'
                        event['response'] = response
                        records.append(event)
                        events.write(json.dumps(event, separators=(',', ':')) + '\n')
                        events.flush()
                        result['events'] += 1
                        if response != 'stop' and (type(response) is not int or not 0 <= response <= 0xffffffff):
                            raise ValueError('Response must be a uint32 or typed stop')
                        connection.sendall((str(response) + '\n').encode())
                        if response == 'stop':
                            break
                    stream.close()
                except TimeoutError:
                    result['reason'] = 'wall_budget'
                except BaseException as error:
                    result['reason'] = 'semantic_gap' if isinstance(error, SemanticGap) else 'runner_error'
                    result['error'] = str(error)
                    raise
                finally:
                    if connection is not None:
                        connection.close()
                    server.close()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    result['exit_code'] = process.returncode
                    result['wall_seconds'] = time.monotonic() - start
                    result['pending_inputs'] = pending
                    result['event_digest'] = identity(records)
                    stable_fields = ('kind', 'seq', 'virtual_time', 'pc', 'address', 'width_bits',
                                     'registers', 'value', 'response', 'pixels_sha256', 'writers', 'pixel_writes')
                    result['replay_signature'] = identity([{k: event[k] for k in stable_fields if k in event}
                                                          for event in records])
                    save_json(directory / 'result.json', result)
        return result
