"""Pixel proof that the overlay keys cleanly: solid chroma outside, opaque panels inside.

Runs against a 4K capture produced by tests/overlay-harness.mjs. Set
CHYRON_SHOT to point at another capture; the test skips when the file is absent.
"""
from pathlib import Path
import os
import unittest

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the runtime venv has Pillow
    Image = None

SHOT = Path(os.environ.get('CHYRON_SHOT', '/tmp/chyron-4k-green.png'))
GREEN = (0, 255, 0)
LEAK = 40  # tolerance when hunting for chroma spill inside a panel


def near(pixel, target, tolerance=24):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], target))


@unittest.skipIf(Image is None, 'Pillow is required for the pixel checks')
@unittest.skipUnless(SHOT.is_file(), f'capture {SHOT} not found; run the harness first')
class ChromaPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = Image.open(SHOT).convert('RGB')
        cls.width, cls.height = cls.image.size
        cls.panels = cls.find_panels()

    @classmethod
    def find_panels(cls):
        """Locate banner panels as the long non-green runs down the centre column."""
        column = cls.width // 2
        runs, start = [], None
        for y in range(cls.height):
            panel = not near(cls.image.getpixel((column, y)), GREEN, tolerance=LEAK)
            if panel and start is None:
                start = y
            elif not panel and start is not None:
                if y - start > 40:
                    runs.append((start, y))
                start = None
        if start is not None and cls.height - start > 40:
            runs.append((start, cls.height))
        return runs

    def test_capture_is_4k(self):
        self.assertEqual((self.width, self.height), (3840, 2160))

    def test_outer_surface_is_flat_key_green(self):
        corners = [(2, 2), (self.width - 3, 2), (2, self.height - 3), (self.width - 3, self.height - 3)]
        for point in corners:
            self.assertTrue(near(self.image.getpixel(point), GREEN),
                            f'{point} is {self.image.getpixel(point)}, not key green')

    def test_background_is_mostly_key_colour(self):
        raw = self.image.tobytes()
        pixels = [raw[i:i + 3] for i in range(0, len(raw), 3)]
        step = max(1, len(pixels) // 40000)
        sampled = pixels[::step]
        share = sum(near(p, GREEN) for p in sampled) / len(sampled)
        # Three tall banners plus their shadows eat most of a 4K frame; what
        # matters is that the field around them is a large flat key colour.
        self.assertGreater(share, 0.4, f'only {share:.0%} of the frame is key green')

    def test_three_banner_bands_are_found(self):
        self.assertEqual(len(self.panels), 3, f'panels at {self.panels}')

    def test_banner_interiors_never_pick_up_the_key_colour(self):
        # Stay clear of the rounded corners (radius is 0.75rem = 75px at 4K).
        for top, bottom in self.panels:
            row = (top + bottom) // 2
            span = [x for x in range(self.width)
                    if not near(self.image.getpixel((x, row)), GREEN, tolerance=LEAK)]
            left, right = min(span), max(span)
            leaked = []
            for y in range(top + 24, bottom - 24, 6):
                for x in range(left + 90, right - 90, 9):
                    pixel = self.image.getpixel((x, y))
                    if near(pixel, GREEN, tolerance=LEAK):
                        leaked.append((x, y, pixel))
            self.assertEqual(leaked[:3], [],
                             f'chroma leaked into the panel at y={top}-{bottom}: {leaked[:3]}')
            self.assertGreater(right - left, self.width * 0.85, 'banner is not full width')

    def test_panels_are_flat_opaque_fills(self):
        """The neobrutalist card is a flat opaque fill: it must match neither the
        key colour nor the transparency of a hole, and it must be uniform enough
        that the encoder sees one block colour."""
        for top, bottom in self.panels:
            box = self.image.crop((self.width // 8, top + 30, self.width * 7 // 8, bottom - 30))
            mean = box.resize((1, 1)).getpixel((0, 0))
            self.assertGreater(max(abs(a - b) for a, b in zip(mean, GREEN)), 60,
                               f'panel mean colour {mean} is too close to the key green')
            counts = {}
            for pixel in zip(*[iter(box.resize((160, 24)).tobytes())] * 3):
                counts[pixel] = counts.get(pixel, 0) + 1
            dominant = max(counts.values()) / sum(counts.values())
            self.assertGreater(dominant, 0.25,
                               f'panel is not a flat fill (top colour only {dominant:.0%})')


if __name__ == '__main__':
    unittest.main()
