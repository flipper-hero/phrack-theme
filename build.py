#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render Phrack's editable artwork into reproducible Flipper animation packs."""

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import textwrap
import zipfile

import heatshrink2
from PIL import Image


ROOT = Path(__file__).resolve().parent


@dataclass
class Screen:
    name: str
    frames: list[Image.Image]
    order: list[int]
    frame_rate: int


def bitmap(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as image:
        if image.mode != "1" or image.size != size:
            raise ValueError(f"{path}: expected a {size[0]}x{size[1]} one-bit PNG")
        return image.copy()


def text(frame: Image.Image, value: str, font: dict, x: int, y: int, spacing: int = 1) -> None:
    width = len(value) * (5 + spacing) - spacing
    if not value or x < 0 or x + width > frame.width or y < 0 or y + 7 > frame.height:
        raise ValueError(f"Text does not fit: {value!r}")
    for index, letter in enumerate(value):
        if letter not in font:
            raise ValueError(f"Unsupported glyph {letter!r} in {value!r}")
        rows = font[letter].split("/")
        if len(rows) != 7 or any(len(row) != 5 or set(row) - {"0", "1"} for row in rows):
            raise ValueError(f"Invalid 5x7 glyph: {letter!r}")
        for row, bits in enumerate(rows):
            for column, bit in enumerate(bits):
                if bit == "1":
                    frame.putpixel((x + index * (5 + spacing) + column, y + row), 0)


def render(assets: Path = ROOT / "assets") -> tuple[dict, list[Screen]]:
    config = json.loads((assets / "theme.json").read_text())
    font = json.loads((assets / "font.json").read_text())
    size = (config["width"], config["height"])
    if size != (128, 64) or config["frame_rate"] != 10:
        raise ValueError("This artwork is authored for a 128x64 display at 10 fps")
    templates = [bitmap(assets / f"magazine/frame_{n}.png", size) for n in range(5)]
    screens = []
    names = set()
    for spec in config["screens"]:
        name = spec["name"]
        if not re.fullmatch(r"Phrack_[A-Za-z0-9_]+", name) or name in names:
            raise ValueError(f"Invalid or duplicate animation name: {name!r}")
        names.add(name)
        entries = spec["entries"]
        cycles = spec["cycles_per_entry"]
        if not entries or not isinstance(cycles, int) or cycles < 1 or len(entries) * cycles * 5 > 255:
            raise ValueError(f"{name}: timeline must contain 1 to 255 frames")
        frames, order = [], []
        for index, entry in enumerate(entries):
            if spec.get("source") != "magazine":
                lines = textwrap.wrap("# " + entry, width=20, break_long_words=False, break_on_hyphens=False)
                if len(lines) > 2:
                    raise ValueError(f"{name}: caption needs more than two lines: {entry!r}")
            start = len(frames)
            for phase, template in enumerate(templates):
                if spec.get("source") == "magazine":
                    # Keep the authored per-frame glitches exactly as supplied.
                    frame = bitmap(assets / f"magazine/frame_{index * 5 + phase}.png", size)
                else:
                    frame = template.copy()
                    # Preserve the exact magazine backdrop, including scanlines.
                    # Its right margin holds a complete 16-pixel background period.
                    for y in (*range(29, 37), *range(44, 63)):
                        for x in range(128):
                            frame.putpixel((x, y), template.getpixel((112 + x % 16, y)))
                    heading = spec["heading"]
                    text(frame, heading, font, (128 - (len(heading) * 11 - 6)) // 2, 30, spacing=6)
                    for row, line in enumerate(lines):
                        text(frame, line, font, 4, 50 if len(lines) == 1 else 44 + row * 10)
                frames.append(frame)
            order.extend(list(range(start, start + 5)) * cycles)
        screens.append(Screen(name, frames, order, config["frame_rate"]))
    return config, screens


def encode_bm(image: Image.Image) -> bytes:
    """Encode the public Flipper .bm format: row-major, black pixels, LSB first."""
    if image.mode != "1":
        raise ValueError("Bitmap encoding requires native one-bit artwork")
    stride = (image.width + 7) // 8
    raw = bytearray(stride * image.height)
    for y in range(image.height):
        for x in range(image.width):
            if image.getpixel((x, y)) == 0:
                raw[y * stride + x // 8] |= 1 << (x % 8)
    compressed = heatshrink2.compress(bytes(raw), window_sz2=8, lookahead_sz2=4)
    if len(compressed) + 4 < len(raw) + 1:
        return b"\x01\x00" + struct.pack("<H", len(compressed)) + compressed
    return b"\x00" + raw


def metadata(screen: Screen) -> bytes:
    return (
        "Filetype: Flipper Animation\nVersion: 1\n\n"
        "Width: 128\nHeight: 64\n"
        f"Passive frames: {len(screen.order)}\nActive frames: 0\n"
        f"Frames order: {' '.join(map(str, screen.order))}\n"
        f"Active cycles: 0\nFrame rate: {screen.frame_rate}\n"
        "Duration: 0\nActive cooldown: 0\n\nBubble slots: 0\n"
    ).encode()


def animation_files(config: dict, screens: list[Screen], png: bool = False) -> dict[str, bytes]:
    files = {}
    manifest = "Filetype: Flipper Animation Manifest\nVersion: 1\n\n"
    values = config["manifest"]
    for screen in screens:
        manifest += (
            f"Name: {screen.name}\n"
            f"Min butthurt: {values['min_butthurt']}\nMax butthurt: {values['max_butthurt']}\n"
            f"Min level: {values['min_level']}\nMax level: {values['max_level']}\n"
            f"Weight: {values['weight']}\n\n"
        )
        files[f"{screen.name}/meta.txt"] = metadata(screen)
        for index, image in enumerate(screen.frames):
            if png:
                stream = io.BytesIO()
                image.save(stream, format="PNG")
                files[f"{screen.name}/frame_{index}.png"] = stream.getvalue()
            else:
                files[f"{screen.name}/frame_{index}.bm"] = encode_bm(image)
    files["manifest.txt"] = manifest.rstrip().encode() + b"\n"
    return files


def archive(path: Path, prefix: str, files: dict[str, bytes], license_text: bytes) -> None:
    entries = {f"{prefix}/{name}": data for name, data in files.items()}
    entries[f"{prefix}/LICENSE.txt"] = license_text
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, data, compresslevel=9)


def previews(screens: list[Screen], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for screen in screens:
        # Two-second excerpts, with the display's actual frame rate and sharp pixels.
        frames = [screen.frames[index].resize((256, 128), Image.Resampling.NEAREST) for index in screen.order[:20]]
        frames[0].save(
            output / f"{screen.name}.gif", save_all=True, append_images=frames[1:],
            duration=1000 // screen.frame_rate, loop=0, optimize=False,
        )


def build(output: Path, preview_output: Path | None = None) -> list[Path]:
    config, screens = render()
    packed = animation_files(config, screens)
    source = animation_files(config, screens, png=True)
    output.mkdir(parents=True, exist_ok=True)
    license_text = (ROOT / "LICENSE-ART").read_bytes()
    paths = []
    for name, prefix, files in (
        ("Phrack.zip", "Phrack/Anims", packed),
        ("Phrack-dolphin.zip", "dolphin", packed),
        ("Phrack-source.zip", "Phrack/Anims", source),
    ):
        path = output / name
        archive(path, prefix, files, license_text)
        paths.append(path)
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in paths
    ))
    if preview_output is not None:
        previews(screens, preview_output)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="Archive directory (default: dist/)")
    parser.add_argument("--previews", type=Path, help="Also regenerate the animated README previews")
    args = parser.parse_args()
    try:
        paths = build(args.output, args.previews)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Build failed: {error}\n")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
