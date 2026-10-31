"""Cover art for the hack entries in EmulationStation, in the site's style: the console's gradient, a pixel
grid, scanlines, the hack's initials and its title. Needs Pillow (only at build time, not on the device).

    python3 device/covers.py dist/device/index.json dist/device/media
"""
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

W, H = 600, 800
FONTS = Path(__file__).parent / "fonts"


def font(size, pixel=True):
    if pixel:
        return ImageFont.truetype(str(FONTS / "PressStart2P-Regular.ttf"), size)
    for f in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def initials(title):
    words = [w for w in re.sub(r"[^A-Za-z0-9 ]", "", title).split() if len(w) > 2 or re.search(r"\d", w)]
    return "".join(w[0] for w in words[:3]).upper()


def wrap(draw, text, fnt, width):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def cover(hack, console):
    a, b = (rgb(c) for c in (console.get("colors") or ["#444444", "#111111"]))
    # 135deg gradient: a diagonal ramp used as the mask between the two colours.
    down = Image.linear_gradient("L").resize((W, H))
    across = Image.linear_gradient("L").rotate(90).resize((W, H))
    ramp = ImageChops.add(down, across, scale=2.0)
    img = Image.composite(Image.new("RGB", (W, H), b), Image.new("RGB", (W, H), a), ramp)
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for x in range(0, W, 12):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 14))
    for y in range(0, H, 12):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 14))
    for y in range(0, H, 4):
        d.line([(0, y), (W, y)], fill=(0, 0, 0, 28))
    # Bottom shade so the title reads on light gradients.
    for y in range(H * 2 // 5, H):
        d.line([(0, y), (W, y)], fill=(0, 0, 0, int(170 * ((y - H * 0.4) / (H * 0.6)) ** 1.5)))
    img = Image.alpha_composite(img.convert("RGBA"), over)
    d = ImageDraw.Draw(img)

    short = console.get("short") or hack["console"].upper()
    f = font(18)
    tw = d.textlength(short, font=f)
    d.rounded_rectangle([24, 24, 24 + tw + 28, 24 + 44], 10, fill=(0, 0, 0, 90))
    d.text((38, 37), short, font=f, fill="white")

    ini = initials(hack["title"]) or hack["title"][:2].upper()
    f = font(110 if len(ini) < 3 else 92)
    tw = d.textlength(ini, font=f)
    d.text(((W - tw) / 2 + 6, 286), ini, font=f, fill=(0, 0, 0, 90))
    d.text(((W - tw) / 2, 280), ini, font=f, fill="white")

    f = font(26)
    lines = wrap(d, hack["title"], f, W - 64)[:4]
    y = H - 64 - 40 * len(lines)
    for line in lines:
        d.text((32 + 3, y + 3), line, font=f, fill=(0, 0, 0, 120))
        d.text((32, y), line, font=f, fill="white")
        y += 40
    by = ", ".join(hack.get("authors") or [])
    if by:
        f = font(22, pixel=False)
        by = wrap(d, "by " + by, f, W - 64)[0]
        d.text((32, H - 52), by, font=f, fill=(255, 255, 255, 210))
    return img.convert("RGB")


def _save(job):
    hack, console, out = job
    cover(hack, console).save(out / ("%s.png" % hack["id"]))


def main(index_path, out_dir, media_prefix="device/media"):
    """Writes <out_dir>/<hack id>.png and records each as "cover" (a path relative to the site root) in the index."""
    idx = json.loads(Path(index_path).read_text(encoding="utf-8"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # PNG compression is most of the time, so it is spread over the CPUs, at the default level: optimize=True
    # made files about 15% smaller but saving 8x slower, too slow for the site build (~4,500 covers).
    with ProcessPoolExecutor() as pool:
        list(pool.map(_save, [(h, idx["consoles"].get(h["console"], {}), out) for h in idx["hacks"]], chunksize=32))
    for h in idx["hacks"]:
        h["cover"] = "%s/%s.png" % (media_prefix, h["id"])
    Path(index_path).write_text(json.dumps(idx), encoding="utf-8")
    print("%d covers in %s" % (len(idx["hacks"]), out))


if __name__ == "__main__":
    main(*sys.argv[1:3])
