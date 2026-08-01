"""Create the transparent animated GIF used for first-stage UI verification."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "web" / "assets" / "pet-placeholder.gif"
SIZE = (300, 360)


def star(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int, color: tuple[int, ...]) -> None:
    points = []
    for index in range(8):
        angle = math.pi / 4 * index - math.pi / 2
        length = radius if index % 2 == 0 else radius * 0.38
        points.append((center[0] + math.cos(angle) * length, center[1] + math.sin(angle) * length))
    draw.polygon(points, fill=color)


def draw_frame(index: int) -> Image.Image:
    image = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    bob = round(math.sin(index / 8 * math.tau) * 4)
    blink = index in (3, 4)

    draw.ellipse((67, 328, 233, 350), fill=(71, 57, 123, 38))
    # Tail behind the body.
    tail_points = [(213, 260 + bob), (260, 250 + bob), (270, 202 + bob), (248, 184 + bob)]
    draw.line(tail_points, fill=(106, 92, 205, 255), width=25, joint="curve")
    draw.line(tail_points, fill=(159, 144, 240, 255), width=15, joint="curve")

    # Body, paws and chest.
    draw.rounded_rectangle((83, 180 + bob, 220, 326 + bob), radius=54, fill=(130, 115, 224, 255))
    draw.ellipse((109, 217 + bob, 194, 319 + bob), fill=(235, 230, 255, 255))
    draw.ellipse((78, 300 + bob, 137, 334 + bob), fill=(107, 92, 204, 255))
    draw.ellipse((171, 300 + bob, 230, 334 + bob), fill=(107, 92, 204, 255))

    # Ears and head.
    draw.polygon([(78, 120 + bob), (94, 49 + bob), (137, 103 + bob)], fill=(105, 90, 202, 255))
    draw.polygon([(174, 100 + bob), (217, 48 + bob), (229, 124 + bob)], fill=(105, 90, 202, 255))
    draw.polygon([(94, 99 + bob), (101, 67 + bob), (123, 103 + bob)], fill=(244, 167, 190, 255))
    draw.polygon([(187, 101 + bob), (211, 67 + bob), (215, 108 + bob)], fill=(244, 167, 190, 255))
    draw.ellipse((72, 82 + bob, 232, 226 + bob), fill=(153, 138, 238, 255))
    draw.ellipse((92, 104 + bob, 212, 215 + bob), fill=(243, 240, 255, 255))

    # Eyes, nose and smile.
    if blink:
        draw.arc((111, 139 + bob, 139, 156 + bob), 12, 168, fill=(48, 48, 82, 255), width=4)
        draw.arc((166, 139 + bob, 194, 156 + bob), 12, 168, fill=(48, 48, 82, 255), width=4)
    else:
        draw.ellipse((116, 137 + bob, 135, 160 + bob), fill=(48, 48, 82, 255))
        draw.ellipse((171, 137 + bob, 190, 160 + bob), fill=(48, 48, 82, 255))
        draw.ellipse((121, 140 + bob, 127, 147 + bob), fill=(255, 255, 255, 255))
        draw.ellipse((176, 140 + bob, 182, 147 + bob), fill=(255, 255, 255, 255))
    draw.polygon([(153, 166 + bob), (145, 172 + bob), (161, 172 + bob)], fill=(226, 117, 151, 255))
    draw.arc((139, 166 + bob, 154, 188 + bob), 275, 90, fill=(61, 55, 87, 255), width=3)
    draw.arc((153, 166 + bob, 168, 188 + bob), 90, 265, fill=(61, 55, 87, 255), width=3)

    # Scarf and a moving sparkle.
    draw.rounded_rectangle((101, 203 + bob, 205, 230 + bob), radius=12, fill=(255, 186, 105, 255))
    draw.polygon([(176, 220 + bob), (212, 257 + bob), (177, 247 + bob)], fill=(242, 145, 70, 255))
    sparkle_x = 49 + (index % 4) * 5
    sparkle_y = 116 - (index % 3) * 7
    star(draw, (sparkle_x, sparkle_y + bob), 12, (255, 206, 102, 255))
    return image


def to_gif_palette(frame: Image.Image) -> Image.Image:
    alpha = frame.getchannel("A")
    paletted = frame.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    transparent = Image.new("L", frame.size, 255)
    transparent.paste(0, mask=alpha.point(lambda value: 255 if value <= 8 else 0))
    paletted.paste(255, mask=transparent.point(lambda value: 255 if value == 0 else 0))
    paletted.info["transparency"] = 255
    return paletted


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [to_gif_palette(draw_frame(index)) for index in range(8)]
    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=135,
        loop=0,
        disposal=2,
        transparency=255,
        optimize=False,
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
