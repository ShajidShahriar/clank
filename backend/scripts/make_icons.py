"""Makes the app icon files from ONE source picture (a PNG with a transparent background):

    uvx --with pillow --with numpy python backend/scripts/make_icons.py <source.png>

Writes `icons/icon.png` (1024 px master, for Windows and Linux), `icons/icon.icns` (macOS, every size) and `public/icon.png` (512 px, for the window and the Dock in
development). The picture is cleaned first (stray faint pixels around the shape are removed), cropped to the shape and centred in the square with the margin macOS
icons have (the shape is 80% of the square). Needs Pillow and numpy and macOS's `iconutil`; it is a manual tool, like the eval scripts, and not part of the app.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CANVAS = 1024
SHAPE_FRACTION = 0.80                 # macOS icons leave room around the shape
FAINT = 10                            # an alpha below this is noise, not part of the picture


def cleaned(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    pixels = np.array(image)
    pixels[pixels[..., 3] < FAINT] = 0                       # stray, almost invisible pixels around the shape
    ys, xs = np.where(pixels[..., 3] > 0)
    shape = Image.fromarray(pixels).crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    return shape


def square_icon(shape: Image.Image) -> Image.Image:
    target = round(CANVAS * SHAPE_FRACTION)
    scale = target / max(shape.size)
    resized = shape.resize((round(shape.width * scale), round(shape.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.paste(resized, ((CANVAS - resized.width) // 2, (CANVAS - resized.height) // 2), resized)
    return canvas


def write_icns(master: Image.Image, out: Path) -> None:
    sizes = {"icon_16x16.png": 16, "icon_16x16@2x.png": 32, "icon_32x32.png": 32, "icon_32x32@2x.png": 64, "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
             "icon_256x256.png": 256, "icon_256x256@2x.png": 512, "icon_512x512.png": 512, "icon_512x512@2x.png": 1024}
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for name, size in sizes.items():
            master.resize((size, size), Image.LANCZOS).save(iconset / name)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    master = square_icon(cleaned(Path(sys.argv[1])))
    (ROOT / "icons").mkdir(exist_ok=True)
    master.save(ROOT / "icons" / "icon.png")
    write_icns(master, ROOT / "icons" / "icon.icns")
    master.resize((512, 512), Image.LANCZOS).save(ROOT / "public" / "icon.png")
    print("wrote icons/icon.png, icons/icon.icns, public/icon.png")


if __name__ == "__main__":
    main()
