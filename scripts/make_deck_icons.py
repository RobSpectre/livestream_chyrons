#!/usr/bin/env python3
"""Draw the OpenDeck key icons for the chyron toggles.

One 144x144 tile per agent per state, in the house style of the existing deck
icons: a rounded background tile, an inset frame, and a glyph, all in the state
colour - dim slate when the chyron is hidden, amber when it is up, red when OBS
cannot answer. Files are named after the sha256 of their contents, which is how
OpenDeck addresses bundled images.

    python3 scripts/make_deck_icons.py            # write into ~/.config/opendeck
    python3 scripts/make_deck_icons.py --check    # report only, for tests
"""
from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path


def feather(pivot, angle_deg, length, width):
    """One solid feather: a pointed wedge with a rounded base at the pivot.

    Solid, not stroked: a stroked fan merges into a blob (or a comma) once the
    key is scaled down to ~52px.
    """
    angle = math.radians(angle_deg)
    dx, dy = math.cos(angle), -math.sin(angle)   # screen y grows downward
    px, py = -dy, dx                             # perpendicular to the feather
    bx, by = pivot
    half = width / 2
    tip = (bx + dx * length, by + dy * length)
    left = (bx + px * half, by + py * half)
    right = (bx - px * half, by - py * half)
    base = (bx - dx * half, by - dy * half)      # control point for the rounded base
    return (f'M {left[0]:.1f},{left[1]:.1f} '
            f'L {tip[0]:.1f},{tip[1]:.1f} '
            f'L {right[0]:.1f},{right[1]:.1f} '
            f'Q {base[0]:.1f},{base[1]:.1f} {left[0]:.1f},{left[1]:.1f} Z')


# Hermes: a wing - three solid feathers fanning from one shoulder. Kept in the
# file as the alternative to the caduceus: swap CADUCEUS for WING below.
WING = ''.join(feather((42, 104), angle, length, width)
               for angle, length, width in [(68, 48, 24), (46, 64, 22), (22, 76, 20)])

# Hermes: the caduceus - the staff he carries, and the emblem that reads as
# "surgeon". Mixed weights on purpose: a stroked staff and snakes need the
# strokes visible while the wings stay solid, so the group carries both paint
# styles. Thin snakes (v2's tighter weave) collapse into a knot below ~96px.
CADUCEUS = (
    '<circle cx="72" cy="28" r="7"/>'
    '<path d="M72,36 L72,118" fill="none" stroke-width="9"/>'
    '<path d="M62,48 C50,40 38,42 26,52 C40,60 52,60 62,56 Z" stroke="none"/>'
    '<path d="M82,48 C94,40 106,42 118,52 C104,60 92,60 82,56 Z" stroke="none"/>'
    '<path d="M54,60 C42,70 42,80 54,88 C42,96 42,106 52,116" fill="none" stroke-width="8"/>'
    '<path d="M90,60 C102,70 102,80 90,88 C102,96 102,106 92,116" fill="none" stroke-width="8"/>')

# From the existing hack.party deck icons, so the new keys match their neighbours.
STATES = {
    'hidden': {'background': '#10141b', 'ink': '#44505e'},
    'shown': {'background': '#201604', 'ink': '#ffb72e'},
    'unknown': {'background': '#10141b', 'ink': '#df6666'},
}

# Glyphs are drawn inside a 144x144 box, roughly 26..118. Weight matters: these
# have to still read as pictograms on a Stream Deck key (96px on an XL, and they
# are checked at 64px). Entries are (paint, stroke width, glyph):
#   'stroke' - outline only;  'fill' - solid shapes;  'both' - a mix, so
#   individual elements carry their own overrides.
GLYPHS = {
    # Codex: angle brackets. The universal "code" mark.
    'codex': ('stroke', 10, '<polyline points="52,38 28,72 52,106"/>'
                           '<polyline points="92,38 116,72 92,106"/>'),
    # Claude: the sunburst from its mark. Thick rays: thin ones smear into a star.
    'claude': ('stroke', 12, ''.join(
        f'<line x1="{72 + 16 * dx:.1f}" y1="{72 + 16 * dy:.1f}" '
        f'x2="{72 + 44 * dx:.1f}" y2="{72 + 44 * dy:.1f}"/>'
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1),
                       (0.7071, 0.7071), (-0.7071, 0.7071),
                       (0.7071, -0.7071), (-0.7071, -0.7071)])),
    # Hermes: the caduceus (swap in WING here to go back to the wing).
    'hermes': ('both', 8, CADUCEUS),
}

PAINT = {'stroke': ('none', 'ink'), 'fill': ('ink', 'none'), 'both': ('ink', 'ink')}

TEMPLATE = ('<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" '
            'viewBox="0 0 144 144">'
            '<rect width="144" height="144" rx="16" fill="{background}"/>'
            '<rect x="5" y="5" width="134" height="134" rx="13" fill="none" '
            'stroke="{ink}" stroke-width="3"/>'
            '<g fill="{glyph_fill}" stroke="{glyph_stroke}" stroke-width="{stroke}" '
            'stroke-linecap="round" stroke-linejoin="round">{glyph}</g>'
            '</svg>')


def tiles(root: Path):
    """{agent: {state: (filename, bytes)}} - deterministic, so hashes are stable."""
    out = {}
    for agent, (paint, stroke, glyph) in GLYPHS.items():
        fill, stroke_key = PAINT[paint]
        out[agent] = {}
        for state, palette in STATES.items():
            svg = TEMPLATE.format(glyph=glyph, stroke=stroke,
                                  glyph_fill=palette['ink'] if fill == 'ink' else 'none',
                                  glyph_stroke=palette['ink'] if stroke_key == 'ink' else 'none',
                                  **palette)
            data = svg.encode()
            name = f'{hashlib.sha256(data).hexdigest()}.svg'
            out[agent][state] = (name, data)
    return out


def install(root: Path, dry_run=False):
    written = []
    for agent, states in tiles(root).items():
        for state, (name, data) in states.items():
            target = root / name
            if not target.exists():
                if not dry_run:
                    root.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                written.append(target)
    return written


# The deck repo names its control art by key and state rather than by hash.
CONTROL_NAMES = {'hidden': 'off', 'shown': 'on', 'unknown': 'unknown'}


def install_controls(root: Path, dry_run=False):
    """Write images/controls/chyron-<agent>-<state>.svg, the layout's own art."""
    written = []
    for agent, states in tiles(root).items():
        for state, (_, data) in states.items():
            target = root / f'chyron-{agent}-{CONTROL_NAMES[state]}.svg'
            if not dry_run:
                root.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            written.append(target)
    return written


def main():
    parser = argparse.ArgumentParser(description='Draw the chyron toggle key icons.')
    parser.add_argument('--root', default=str(Path.home() / '.config/opendeck/images/hackparty'))
    parser.add_argument('--controls', help='also write named art for the deck repo layout spec')
    parser.add_argument('--check', action='store_true', help='report instead of writing')
    args = parser.parse_args()
    root = Path(args.root)

    if args.controls:
        written = install_controls(Path(args.controls))
        print(f'{len(written)} control tiles written to {args.controls}')
        for target in sorted(written):
            print(f'  {target.name}')

    if args.check:
        missing = [name for states in tiles(root).values() for name, _ in states.values()
                   if not (root / name).is_file()]
        print(f'{len(GLYPHS) * len(STATES)} tiles, {len(missing)} missing')
        for name in missing:
            print('  missing', name)
        return 1 if missing else 0

    written = install(root, dry_run=False)
    print(f'{len(written)} new tiles written to {root}')
    for agent, states in sorted(tiles(root).items()):
        print(f'  {agent:<7} ' + ' '.join(f'{state}={name[:12]}' for state, (name, _) in states.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
