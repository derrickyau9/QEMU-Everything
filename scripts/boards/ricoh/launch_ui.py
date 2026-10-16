#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launch the assisted original GR IV 1.11 UI on a POSIX host."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True, help='New run directory')
    parser.add_argument('--probe', action='store_true', help='Verify controls and exit without a viewer')
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = ROOT/'run_ui.py'
    config = {
        'manifest': str(args.manifest.resolve()),
        'payload': str(args.payload.resolve()),
        'destination': str(destination),
        'mode': 'probe' if args.probe else 'interactive',
        'session_seconds': 3600,
        'seconds': 180,
        'port': 8792,
    }
    command = [sys.executable, str(source.resolve()), json.dumps(config)]
    process = subprocess.Popen(command, cwd=source.resolve().parents[3], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for line in process.stdout:
            print(line, end='', flush=True)
        return process.wait()
    except KeyboardInterrupt:
        print('\nStopping the owned menu session…', flush=True)
        if process.poll() is None:
            process.stdin.write('stop\n')
            process.stdin.flush()
        try:
            return process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            return process.wait(timeout=10)
    finally:
        process.stdin.close()
        process.stdout.close()


if __name__ == '__main__':
    raise SystemExit(main())
