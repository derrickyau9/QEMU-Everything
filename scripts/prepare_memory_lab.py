"""Create an isolated, evidence-checked memory lab; never access the camera."""
import argparse
from pathlib import Path
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from devkit.core import data_home, write_json
from devkit.memory_lab import import_evidence


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--evidence', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--source-device', type=Path)
    args = p.parse_args()
    profile = import_evidence(args.evidence)
    source = args.source_device or data_home()/'library/9789d6a9843d3880'
    args.output.mkdir(parents=True, exist_ok=False)
    # Only immutable payload/source metadata are copied. Writable disks and
    # logs belong to the new instance, never the currently running GUI.
    shutil.copy2(source/'device.json', args.output/'device.json')
    shutil.copytree(source/'payload-stage', args.output/'payload-stage')
    write_json(args.output/'memory-profile.json', profile)
    print(args.output/'memory-profile.json')


if __name__ == '__main__': main()
