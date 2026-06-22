"""
image_gen.py — G.I.L.
AI image generation using Pollinations.ai — completely free, no API key.
Uses the FLUX model (state-of-the-art, rivals Midjourney quality).

Supports photorealistic, artistic, and stylised prompts.
Images are saved to data/generated_images/ and opened in Windows Photo Viewer.
"""

import time
import random
import urllib.parse
import requests
from pathlib import Path
from logger import get as _get_log

log      = _get_log("image_gen")
OUT_DIR  = Path(__file__).parent / "data" / "generated_images"

# Default dimensions — matches 1024×1024 FLUX output quality sweet spot
DEFAULT_W = 1024
DEFAULT_H = 1024

# Aspect ratio presets that GIL can choose from based on the prompt
_RATIOS = {
    "portrait":   (768,  1024),
    "landscape":  (1366, 768),
    "square":     (1024, 1024),
    "wallpaper":  (1920, 1080),
    "banner":     (1200, 400),
    "instagram":  (1080, 1080),
    "story":      (1080, 1920),
}


def generate(
    prompt:   str,
    width:    int  = DEFAULT_W,
    height:   int  = DEFAULT_H,
    model:    str  = "flux",        # "flux" = best | "turbo" = fastest
    enhance:  bool = True,          # let Pollinations auto-enhance the prompt
    negative: str  = "",            # things to exclude from the image
) -> Path:
    """
    Generate an image from a text prompt.
    Returns the Path to the saved JPEG.
    Raises requests.HTTPError or IOError on failure.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    encoded  = urllib.parse.quote(prompt)
    seed     = random.randint(1, 99_999)
    params   = (
        f"?width={width}&height={height}"
        f"&seed={seed}&model={model}"
        f"&enhance={'true' if enhance else 'false'}"
        f"&nologo=true"
    )
    if negative:
        params += f"&negative={urllib.parse.quote(negative)}"

    url = f"https://image.pollinations.ai/prompt/{encoded}{params}"
    log.info("requesting image — model=%s  prompt=%s", model, prompt[:80])

    resp = requests.get(url, timeout=90, stream=True)
    resp.raise_for_status()

    # Build a safe filename
    ts        = time.strftime("%Y%m%d_%H%M%S")
    safe      = "".join(
        c if (c.isalnum() or c == " ") else "_"
        for c in prompt[:44]
    ).strip().replace(" ", "_")
    filename  = OUT_DIR / f"{ts}_{safe}.jpg"

    with open(filename, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8_192):
            if chunk:
                fh.write(chunk)

    size_kb = filename.stat().st_size // 1024
    log.info("image saved: %s  (%d KB)", filename.name, size_kb)

    if size_kb < 5:
        filename.unlink(missing_ok=True)
        raise IOError("Pollinations returned an empty/invalid image. Try again.")

    return filename


def infer_dimensions(prompt: str) -> tuple[int, int]:
    """
    Guess width × height from keywords in the prompt.
    Falls back to 1024×1024 square.
    """
    lower = prompt.lower()
    for key, dims in _RATIOS.items():
        if key in lower:
            return dims
    if any(w in lower for w in ("wallpaper", "desktop", "background", "widescreen")):
        return _RATIOS["wallpaper"]
    if any(w in lower for w in ("portrait", "person", "selfie", "vertical")):
        return _RATIOS["portrait"]
    return DEFAULT_W, DEFAULT_H


def open_image(path: Path) -> None:
    """Open the saved image in the default Windows viewer."""
    import os, ctypes
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), None, None, 1)
        if ret <= 32:
            os.startfile(str(path))
    except Exception as exc:
        log.error("could not open image: %s", exc)
