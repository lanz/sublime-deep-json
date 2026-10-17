"""Verify every screenshot's per-level colors against its scheme's own palette.

Palettes are read out of the generated .sublime-color-scheme files, so this checks the
images against what actually ships, including Monokai Neue's six-level cycle.
Rows are banded on the gutter line numbers where present, otherwise on glyph pixels.

Needs Pillow (pip install pillow); nothing else in dev/ has a dependency.
Run:  python verify_screenshots.py

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.
"""
import collections
import glob
import os
import re

from PIL import Image

# derived, so renaming the checkout cannot break this
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_LEVEL = 20

# code line -> nesting level, for each sample file
EXPECT = {
    "object": ({r: r - 1 for r in range(2, 12)}, 21),
    "array": ({2: 1, 3: 2, 5: 3, 7: 4, 9: 5, 11: 6, 13: 7, 15: 8, 17: 9, 19: 10}, 29),
    "mixed": ({2: 1, 3: 2, 5: 3, 8: 3, 9: 4, 10: 4, 12: 5, 13: 6, 15: 7, 17: 8,
               18: 9, 20: 10}, 30),
}


def palette(scheme_path):
    raw = open(scheme_path, encoding="utf-8").read()
    out = []
    i = 1
    while True:
        m = re.search(r'"json_level%d":\s*"([^"]+)"' % i, raw)
        if not m:
            break
        out.append(m.group(1).lower())
        i += 1
    return out


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def bands_for(im, bg):
    w, h = im.size
    px = im.load()

    def dist(c):
        return sum(abs(a - b) for a, b in zip(c, bg))

    # prefer the gutter (line numbers appear on every line); fall back to glyphs
    for xmax, thresh in ((34, 100), (w, 140)):
        rows = [y for y in range(h)
                if sum(1 for x in range(xmax) if dist(px[x, y]) > thresh) > 0]
        bands = []
        for y in rows:
            if bands and y - bands[-1][-1] <= 3:
                bands[-1].append(y)
            else:
                bands.append([y])
        yield bands


def check(path, pal, which):
    exp_map, nlines = EXPECT[which]
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    counts = collections.Counter(px[x, y] for x in range(w) for y in range(h))
    bg = counts.most_common(1)[0][0]
    glyph = {c for c, n in counts.items() if n >= 40 and c != bg}

    bands = None
    for cand in bands_for(im, bg):
        if len(cand) == nlines:
            bands = cand
            break
    if bands is None:
        return None, "could not band %d lines" % nlines

    pal_rgb = [hex2rgb(c) for c in pal]
    ok = bad = 0
    detail = []
    for i, band in enumerate(bands, 1):
        exp = exp_map.get(i)
        if exp is None:
            continue
        c = collections.Counter()
        for y in band:
            for x in range(42, w):
                q = px[x, y]
                if q in glyph:
                    c[q] += 1
        if not c:
            continue
        dom = c.most_common(1)[0][0]
        d, best = min((sum(abs(a - b) for a, b in zip(dom, p)), j + 1)
                      for j, p in enumerate(pal_rgb))
        want_slot = (min(exp, MAX_LEVEL) - 1) % len(pal) + 1
        if best == want_slot and d <= 12:
            ok += 1
        else:
            bad += 1
            detail.append("line %d: level %d wants slot %d (%s), got #%02X%02X%02X"
                          % (i, exp, want_slot, pal[want_slot - 1], dom[0], dom[1], dom[2]))
    return (ok, bad, detail), None


def slug(name):
    """A scheme name as its screenshot folder.

    Folders are slugs rather than the scheme name itself, so the paths in the
    README need no percent-encoding and match the VS Code repository's layout.
    """
    out = "".join(ch if ch.isalnum() else "-" for ch in name.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


total_ok = total_bad = 0
for scheme in sorted(glob.glob(os.path.join(REPO, "*.sublime-color-scheme"))):
    name = os.path.basename(scheme).replace(".sublime-color-scheme", "")
    pal = palette(scheme)
    print("\n=== %s   (%d-color cycle) ===" % (name, len(pal)))
    print("    " + " ".join(pal))
    for which in ("object", "array", "mixed"):
        shot = os.path.join(REPO, "screenshots", slug(name), which + ".png")
        if not os.path.exists(shot):
            print("  %-11s MISSING" % which)
            continue
        res, err = check(shot, pal, which)
        if err:
            print("  %-11s %s" % (which, err))
            continue
        ok, bad, detail = res
        total_ok += ok
        total_bad += bad
        print("  %-11s %2d correct, %d wrong" % (which, ok, bad))
        for d in detail[:6]:
            print("        " + d)

print("\n" + "=" * 62)
print("TOTAL: %d levels correct, %d wrong" % (total_ok, total_bad))
