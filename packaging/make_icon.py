"""產生程式圖示 src/astro_light_selector/assets/app.ico：深藍夜空底 + 帶繞射芒的星點。

python packaging/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "src" / "astro_light_selector" / "assets" / "app.ico"
SIZE = 1024  # 先畫大張再縮小，邊緣才平滑


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = SIZE * 0.2
    d.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=r, fill=(16, 28, 58, 255))

    c = SIZE / 2
    # 光暈
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((c - 230, c - 230, c + 230, c + 230), fill=(120, 170, 255, 110))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))

    # 繞射芒：四條由粗到細的尖刺
    spike = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd = ImageDraw.Draw(spike)
    length, width = SIZE * 0.44, SIZE * 0.035
    sd.polygon([(c, c - length), (c + width, c), (c, c + length), (c - width, c)], fill=(235, 242, 255, 255))
    sd.polygon([(c - length, c), (c, c - width), (c + length, c), (c, c + width)], fill=(235, 242, 255, 255))
    img.alpha_composite(spike.filter(ImageFilter.GaussianBlur(3)))

    # 星點本體
    core = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(core).ellipse((c - 95, c - 95, c + 95, c + 95), fill=(255, 255, 255, 255))
    img.alpha_composite(core.filter(ImageFilter.GaussianBlur(12)))

    # 旁邊點綴幾顆小星星
    for x, y, s in ((0.78, 0.24, 18), (0.2, 0.74, 14), (0.26, 0.3, 9)):
        ImageDraw.Draw(img).ellipse((x * SIZE - s, y * SIZE - s, x * SIZE + s, y * SIZE + s),
                                    fill=(210, 225, 255, 230))
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().resize((256, 256), Image.LANCZOS).save(
        OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"已產生 {OUT}")


if __name__ == "__main__":
    main()
