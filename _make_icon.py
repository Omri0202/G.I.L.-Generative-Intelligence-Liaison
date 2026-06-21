"""
_make_icon.py — Run once to convert gil_logo.png -> gil.ico + gil_icon_hq.png
Usage:  python _make_icon.py
"""
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance

SRC = Path(__file__).parent / "data" / "gil_logo.png"
DST = Path(__file__).parent / "data" / "gil.ico"
HQ  = Path(__file__).parent / "data" / "gil_icon_hq.png"


def make() -> None:
    if not SRC.exists():
        print(f"[ERROR] Logo not found: {SRC}")
        return

    img = Image.open(SRC).convert("RGBA")

    # Upscale to 512 first — always gives crisper small-size downscales
    img = img.resize((512, 512), Image.LANCZOS)

    # Boost contrast and sharpness on the source before downscaling
    img = ImageEnhance.Contrast(img).enhance(1.20)
    img = ImageEnhance.Sharpness(img).enhance(1.40)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=140, threshold=3))

    # High-quality 128px PNG used by wm_iconphoto (crisp in title bar / taskbar)
    hq = img.resize((128, 128), Image.LANCZOS)
    hq = ImageEnhance.Sharpness(hq).enhance(1.2)
    hq.save(HQ)
    print(f"[OK] HQ icon -> {HQ}")

    # Multi-resolution ICO
    sizes  = [16, 24, 32, 48, 64, 128, 256]
    frames = []
    for s in sizes:
        frame = img.resize((s, s), Image.LANCZOS)
        if s <= 48:
            frame = ImageEnhance.Sharpness(frame).enhance(1.6)
            frame = ImageEnhance.Contrast(frame).enhance(1.15)
        frames.append(frame)

    frames[0].save(DST, format="ICO",
                   sizes=[(s, s) for s in sizes],
                   append_images=frames[1:])
    print(f"[OK] ICO ({len(frames)} sizes) -> {DST}")


if __name__ == "__main__":
    make()
