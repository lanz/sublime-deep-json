"""Muted bracket colors for the "(Muted Brackets)" schemes, plus indent guides.

Sublime Text has no bracket colorizer of its own, so depth-colored delimiters are
purely additive here: the rules in depth_selectors.py target
punctuation.section.mapping and punctuation.section.sequence, and the color comes
from a json_bracket<N> variable.

Two styles ship, matching the Visual Studio Code port:

  vivid  json_bracket<N> is json_level<N>, so a delimiter is exactly the color of
         the content it wraps
  muted  the same hue desaturated and then lightness-adjusted to a target contrast
         against the scheme background, so delimiters read as structure rather than
         competing with the content

The math below is copied verbatim from the VS Code repository's dev/brackets.py so
the two editors produce identical colors. Every scheme background is identical
across the two, so identical inputs give identical output. check_palettes.py in the
VS Code repository compares the results.

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.
"""

import colorsys

SLOTS = 6

# how much of the original saturation a muted bracket keeps
MUTED_SATURATION = 0.42
# contrast ratio a muted bracket should hit against the editor background:
# clearly visible, but below the content it wraps
MUTED_CONTRAST = 2.9


def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(
        max(0, min(255, int(round(c * 255)))) for c in rgb
    )


def _relative_luminance(rgb):
    def channel(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(rgb_a, rgb_b):
    a, b = _relative_luminance(rgb_a), _relative_luminance(rgb_b)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def mute(color, background, target=MUTED_CONTRAST):
    """Desaturate `color`, then move its lightness until it hits `target` contrast."""
    r, g, b = _hex_to_rgb(color)
    bg = _hex_to_rgb(background)
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    saturation *= MUTED_SATURATION

    # bisect on lightness rather than guess: monotonic against a dark background
    bg_is_dark = _relative_luminance(bg) < 0.18
    low, high = (0.0, lightness) if not bg_is_dark else (0.0, 1.0)
    best = None
    for _ in range(40):
        mid = (low + high) / 2.0
        candidate = colorsys.hls_to_rgb(hue, mid, saturation)
        ratio = contrast(candidate, bg)
        if best is None or abs(ratio - target) < abs(best[1] - target):
            best = (candidate, ratio)
        if ratio < target:
            low = mid
        else:
            high = mid
    return _rgb_to_hex(best[0]), best[1]


# Indent guides are chrome, not tokens: a color scheme sets them as globals and
# there is no way to vary them by depth. So they get one color each, derived from
# the palette so they belong to the theme, and muted so they do not compete with
# the content. The active guide takes the palette's second color rather than its
# first, which is usually near-white: a hue shift is what makes the active guide
# distinguishable from the stack behind it, and alpha alone is not enough.
GUIDE_ALPHA = 0x30
STACK_GUIDE_ALPHA = 0x80
ACTIVE_GUIDE_ALPHA = 0xF0


def guide_colors(levels, background):
    """(guide, stack_guide, active_guide) for a palette, as 8-digit hex."""
    base = mute(levels[0], background)[0]
    accent = mute(levels[1 % len(levels)], background)[0]
    return (
        "%s%02X" % (base, GUIDE_ALPHA),
        "%s%02X" % (base, STACK_GUIDE_ALPHA),
        "%s%02X" % (accent, ACTIVE_GUIDE_ALPHA),
    )


def resolve_guides(declared, levels, background):
    """(guide, stack_guide, active_guide), declared slots winning over derived.

    A palette may declare all three, or leave any of them None to derive that one.
    Monokai Classic declares only its active guide, Hazenberg's orange, and lets
    the quieter two derive: the orange at half alpha made a loud stack.
    """
    derived = guide_colors(levels, background)
    if not declared:
        return derived
    return tuple(
        value if value else derived[index]
        for index, value in enumerate(declared)
    )


def bracket_levels(levels, background, style):
    """json_bracket1..N for a palette, in level order."""
    if style == "vivid":
        return [c.upper() for c in levels]
    return [mute(c, background)[0] for c in levels]


STYLES = [
    {"id": "vivid", "suffix": ""},
    {"id": "muted", "suffix": " (Muted Brackets)"},
]
