"""Open the X2D II 1.4.15.7 camera UI from a local ext4 system partition image."""
import argparse
import hashlib
from pathlib import Path
import sys
import tempfile

from dissect.extfs import ExtFS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devkit.core import Task, data_home, read_json, write_json
from devkit.firmware import MAX_IMAGE, extract_system, profile_metadata, stage


VERSION = "1.4.15.7"


def check_image(stream):
    fs = ExtFS(stream)
    version = fs.get("/etc/VERSION").open().read(128).decode("ascii").strip()
    if version != VERSION:
        raise ValueError(f"Expected {VERSION}, found {version} in /etc/VERSION")
    props = fs.get("/build.prop").open().read(1024 * 1024).splitlines()
    if b"ro.product.device=eagle2_hb722" not in props:
        raise ValueError("Expected an X2D II system image (eagle2_hb722)")


def prepare(image, home):
    if not 0 < image.stat().st_size <= MAX_IMAGE:
        raise ValueError("Unsupported system image size")
    with image.open("rb") as stream:
        check_image(stream)
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    library = home / "library"
    library.mkdir(exist_ok=True, parents=True)
    device = library / ("x2dii-final-" + digest)
    if device.exists():
        profile = read_json(device / "device.json")
        if profile.get("sha256") == digest and profile.get("version") == VERSION:
            return device
        raise ValueError("Existing library entry does not match this image")
    task = Task()
    with tempfile.TemporaryDirectory(dir=library, prefix="final-import-") as tmp:
        work = Path(tmp)
        root = work / "system"
        extract_system(image, root, task)
        with image.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError("The source image changed during import")
        entry = work / "entry"
        abi = stage(root, entry / "payload-stage", task)
        if abi != "arm64":
            raise ValueError("The final camera UI must be AArch64")
        profile = profile_metadata("Hasselblad X2D II 100C", VERSION, digest, image.name, abi)
        profile.update(ota_stage="final", validated=False, ui_status="experimental")
        write_json(entry / "device.json", profile)
        entry.rename(device)
    return device


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("system_image", type=Path)
    args = parser.parse_args()
    home = data_home()
    device = prepare(args.system_image, home)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from devkit.application import Window

    app = QApplication(sys.argv[:1])
    window = Window(home)
    window.refresh_library(select=device)
    if window.device != device:
        raise RuntimeError("Workbench did not select the imported firmware")
    window.show()
    QTimer.singleShot(0, window.launch)
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
