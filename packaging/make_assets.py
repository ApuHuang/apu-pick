"""產生程式圖示與 Logo：

- src/astro_light_selector/assets/app.ico      視窗與 exe 圖示（深藍夜空底 + 帶繞射芒的星點）
- src/astro_light_selector/assets/icon_128.png 視窗上方 Logo 用
- docs/logo.png                                README 最上方的 Logo 橫幅（APU Pick + 副標）

python packaging/make_assets.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "astro_light_selector" / "assets"
DOCS = ROOT / "docs"
SIZE = 1024  # 先畫大張再縮小，邊緣才平滑
NAVY = (16, 28, 58, 255)
NAME = "APU Pick"
SUBTITLE = "Astrophotography Pick Utility"


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE * 0.2, fill=NAVY)

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


def _font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    """Windows 用 Segoe UI；其他平台找不到就用 matplotlib 內附的 DejaVu。"""
    import matplotlib
    candidates = [Path("C:/Windows/Fonts") / n for n in names]
    candidates.append(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans-Bold.ttf")
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def draw_logo(icon: Image.Image) -> Image.Image:
    """深藍底橫幅：圖示 + APU Pick + 副標。自帶底色，GitHub 亮色、暗色主題都看得清楚。"""
    w, h = 1320, 400
    scale = 2  # 畫兩倍大再縮小
    img = Image.new("RGBA", (w * scale, h * scale), NAVY)
    icon_size = 280 * scale
    img.alpha_composite(icon.resize((icon_size, icon_size), Image.LANCZOS), (60 * scale, 60 * scale))
    d = ImageDraw.Draw(img)
    title = _font(["segoeuib.ttf"], 150 * scale)
    sub = _font(["seguisb.ttf", "segoeui.ttf"], 58 * scale)
    x = (60 + 280 + 60) * scale
    d.text((x, 70 * scale), NAME, font=title, fill=(255, 255, 255, 255))
    d.text((x + 6 * scale, 262 * scale), SUBTITLE, font=sub, fill=(174, 187, 214, 255))
    return img.resize((w, h), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    icon256 = icon.resize((256, 256), Image.LANCZOS)
    icon256.save(ASSETS / "app.ico",
                 sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.resize((128, 128), Image.LANCZOS).save(ASSETS / "icon_128.png", optimize=True)
    draw_logo(icon).convert("RGB").save(DOCS / "logo.png", optimize=True)
    print(f"已產生 {ASSETS / 'app.ico'}、{ASSETS / 'icon_128.png'}、{DOCS / 'logo.png'}")


if __name__ == "__main__":
    main()
