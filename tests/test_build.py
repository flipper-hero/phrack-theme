# SPDX-License-Identifier: MIT
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

import heatshrink2
from PIL import Image

import build


def decode(data, size):
    if data[0] == 0:
        raw = data[1:]
    elif data[:2] == b"\x01\x00":
        if struct.unpack("<H", data[2:4])[0] != len(data) - 4:
            raise ValueError("Compressed length mismatch")
        raw = heatshrink2.decompress(data[4:], window_sz2=8, lookahead_sz2=4)
    else:
        raise ValueError("Unknown bitmap header")
    width, height = size
    stride = (width + 7) // 8
    if len(raw) != stride * height:
        raise ValueError("Bitmap length mismatch")
    image = Image.new("1", size, 255)
    for y in range(height):
        for x in range(width):
            if raw[y * stride + x // 8] & (1 << (x % 8)):
                image.putpixel((x, y), 0)
    return image


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, cls.screens = build.render()
        cls.files = build.animation_files(cls.config, cls.screens)

    def test_known_good_hardware_verified_pack(self):
        # Covers artwork, content, compression, and metadata of the reviewed pack.
        digest = hashlib.sha256()
        for name, data in sorted(self.files.items()):
            digest.update(name.encode() + b"\0" + data)
        self.assertEqual(len(self.files), 124)
        self.assertEqual(digest.hexdigest(), "426e01b2e42b3391ff5002b585aee496d2ed2d95fb662133e861b941218809d9")

    def test_native_dimensions_and_firmware_limits(self):
        self.assertEqual([len(screen.frames) for screen in self.screens], [20, 50, 50])
        self.assertEqual([len(screen.order) for screen in self.screens], [40, 200, 200])
        for screen in self.screens:
            self.assertLessEqual(len(screen.order), 255)
            self.assertEqual(screen.frame_rate, 10)
            self.assertEqual([n % 5 for n in screen.order], list(range(5)) * (len(screen.order) // 5))
            self.assertTrue(all(0 <= n < len(screen.frames) for n in screen.order))
            for image in screen.frames:
                self.assertEqual((image.mode, image.size), ("1", (128, 64)))

    def test_shared_magazine_motion_and_header(self):
        for screen in self.screens[1:]:
            for index, image in enumerate(screen.frames):
                magazine = self.screens[0].frames[index % 5]
                for top, bottom in ((0, 29), (37, 44), (63, 64)):
                    self.assertEqual(
                        image.crop((0, top, 128, bottom)).tobytes(),
                        magazine.crop((0, top, 128, bottom)).tobytes(),
                    )

    def test_all_frames_survive_bitmap_encoding(self):
        for screen in self.screens:
            for index, image in enumerate(screen.frames):
                data = self.files[f"{screen.name}/frame_{index}.bm"]
                self.assertEqual(decode(data, image.size).tobytes(), image.tobytes())

    def test_lsb_first_uncompressed_rows_and_padding(self):
        image = Image.new("1", (9, 2), 255)
        for pixel in ((0, 0), (8, 0), (7, 1)):
            image.putpixel(pixel, 0)
        self.assertEqual(build.encode_bm(image), b"\x00\x01\x01\x80\x00")

    def test_compressed_header(self):
        image = Image.new("1", (128, 64), 255)
        data = build.encode_bm(image)
        self.assertEqual(data[:2], b"\x01\x00")
        self.assertLess(len(data), 1025)
        self.assertEqual(decode(data, image.size).tobytes(), image.tobytes())

    def test_rejects_clipped_text_and_unknown_glyphs(self):
        font = json.loads((build.ROOT / "assets/font.json").read_text())
        image = Image.new("1", (128, 64), 255)
        for value, x, y in (("A" * 22, 0, 0), ("HELLO", 4, 60), ("Ω", 4, 4)):
            with self.assertRaises(ValueError):
                build.text(image, value, font, x, y)

    def test_rejects_antialiased_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gray.png"
            Image.new("L", (128, 64), 128).save(path)
            with self.assertRaises(ValueError):
                build.bitmap(path, (128, 64))

    def test_release_layouts_licenses_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as directory:
            left = Path(directory) / "left"
            right = Path(directory) / "right"
            build.build(left)
            build.build(right)
            for name, prefix, extension in (
                ("Phrack.zip", "Phrack/Anims/", ".bm"),
                ("Phrack-dolphin.zip", "dolphin/", ".bm"),
                ("Phrack-source.zip", "Phrack/Anims/", ".png"),
            ):
                self.assertEqual((left / name).read_bytes(), (right / name).read_bytes())
                with zipfile.ZipFile(left / name) as archive:
                    self.assertIsNone(archive.testzip())
                    self.assertEqual(len(archive.namelist()), 125)
                    self.assertTrue(all(path.startswith(prefix) for path in archive.namelist()))
                    self.assertFalse(any("/Icons/" in path or "/Fonts/" in path for path in archive.namelist()))
                    self.assertEqual(archive.read(prefix + "LICENSE.txt"), (build.ROOT / "LICENSE-ART").read_bytes())
                    self.assertEqual(sum(path.endswith(extension) for path in archive.namelist()), 120)
                    self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
                    if extension == ".png":
                        with Image.open(io.BytesIO(archive.read(prefix + "Phrack_Articles/frame_0.png"))) as image:
                            self.assertEqual((image.mode, image.size), ("1", (128, 64)))
            self.assertEqual((left / "SHA256SUMS").read_bytes(), (right / "SHA256SUMS").read_bytes())
            for line in (left / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ")
                self.assertEqual(digest, hashlib.sha256((left / name).read_bytes()).hexdigest())

    def test_preview_dimensions_playback_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build.previews(self.screens, output)
            for screen in self.screens:
                path = output / f"{screen.name}.gif"
                self.assertEqual(path.read_bytes(), (build.ROOT / "previews" / path.name).read_bytes())
                with Image.open(path) as image:
                    self.assertEqual(image.size, (256, 128))
                    self.assertEqual(image.n_frames, 20)
                    for index in range(image.n_frames):
                        image.seek(index)
                        self.assertEqual(image.info["duration"], 100)


if __name__ == "__main__":
    unittest.main()
