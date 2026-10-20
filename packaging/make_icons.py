"""Render the AI Switch icon (rounded gradient square with two arrows) to PNG/ICO/ICNS.

Run: python packaging/make_icons.py   (needs Pillow). Outputs are committed, so builds don't need it.
"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
SIZE = 1024


def render(size: int = SIZE) -> Image.Image:
    s = size / 64  # design grid matches ai_switch/web/app/icon.svg
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    a, b = (0x5B, 0x7C, 0xFA), (0x7A, 0x3F, 0xD1)
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(15 * s), fill=255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    w = int(5.5 * s)

    def line(points):
        pts = [(x * s, y * s) for x, y in points]
        d.line(pts, fill="white", width=w, joint="curve")
        for x, y in pts:
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill="white")

    line([(17, 24), (45, 24)])
    line([(45, 24), (38, 17)])
    line([(47, 40), (19, 40)])
    line([(19, 40), (26, 47)])
    return img


if __name__ == "__main__":
    big = render()
    big.save(HERE / "icon.png")
    big.resize((256, 256), Image.LANCZOS).save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                                                          (64, 64), (128, 128), (256, 256)])
    big.save(HERE / "icon.icns")
    print("wrote icon.png, icon.ico, icon.icns")
