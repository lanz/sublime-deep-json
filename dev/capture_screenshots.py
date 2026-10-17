"""Capture a screenshot of every scheme and sample, out of a running Sublime Text.

Sublime has no remote-control protocol and no API for screen coordinates, so this
is a screen grab rather than a renderer capture, and it comes with the obvious
caveat: the window has to be visible and the machine left alone while it runs.

dev/capture_plugin.py is the other half. It owns the editor, styling each view per
shot so your own color scheme is never touched. This half finds the window, grabs
it, crops to the text, and checks the result before keeping it.

Nothing is trusted. Every grab is verified against the scheme's own palette before
being written, the same idea the VS Code harness uses: a screenshot that looks
plausible but was taken mid-render is the failure that matters, and it is invisible
unless you check. A grab that does not verify is retried.

Needs Pillow. Windows only, for the window lookup.

Run:  python dev/capture_screenshots.py
      python dev/capture_screenshots.py --scheme "Deep JSON - Monokai Chalk"
      python dev/capture_screenshots.py --sample mixed

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.
"""
import argparse
import ctypes
import ctypes.wintypes
import glob
import io
import json
import os
import re
import sys
import tempfile
import time

from PIL import Image, ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PACKAGE = "Deep JSON Color Schemes"

HANDSHAKE = os.path.join(tempfile.gettempdir(), "deep-json-shots")
REQUEST = os.path.join(HANDSHAKE, "request.json")
READY = os.path.join(HANDSHAKE, "ready.json")
DONE = os.path.join(HANDSHAKE, "done.json")
STOP = os.path.join(HANDSHAKE, "stop")
ALIVE = os.path.join(HANDSHAKE, "alive")

SAMPLES = ("object", "array", "mixed", "openapi")
PAD = 8
# a pixel this far from the background in summed channel difference is ink
INK = 40


# ------------------------------------------------------------- the window ----

SW_MAXIMIZE = 3
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002


def set_topmost(hwnd, on):
    """Force the window above everything, or let it back down.

    SetForegroundWindow is not enough: Windows refuses to let a process raise
    another process's window over the one that spawned it, so the terminal running
    this script stayed on top and its text ended up in the grab. Topmost is not
    subject to that restriction.
    """
    ctypes.windll.user32.SetWindowPos(
        hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST,
        0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE,
    )


def find_window():
    """The handle of the Sublime Text window, or None."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                        ctypes.wintypes.LPARAM)
    def each(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if "Sublime Text" in buf.value:
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


class _Placement(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint),
                ("showCmd", ctypes.c_uint),
                ("ptMinPosition", ctypes.wintypes.POINT),
                ("ptMaxPosition", ctypes.wintypes.POINT),
                ("rcNormalPosition", ctypes.wintypes.RECT)]


def maximize(hwnd):
    """Maximize for the run, returning the previous placement to restore.

    openapi.json is 57 lines, which does not fit a windowed editor, and Sublime
    only renders what is on screen.
    """
    saved = _Placement()
    saved.length = ctypes.sizeof(_Placement)
    ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(saved))
    ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE)
    set_topmost(hwnd, True)
    # Hand focus back to this terminal. Sublime draws a caret only in the focused
    # window, so an unfocused but topmost window is visible without one, which is
    # what lets the crop leave clearance under the last line's brace. Maximizing
    # takes focus, so this has to come afterwards.
    console = ctypes.windll.kernel32.GetConsoleWindow()
    if console:
        ctypes.windll.user32.SetForegroundWindow(console)
    time.sleep(0.8)
    return saved


def restore(hwnd, saved):
    if not hwnd:
        return
    set_topmost(hwnd, False)
    if saved is not None:
        ctypes.windll.user32.SetWindowPlacement(hwnd, ctypes.byref(saved))

def sublime_window():
    """(left, top, right, bottom) of the Sublime Text window, in screen pixels."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                        ctypes.wintypes.LPARAM)
    def each(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if "Sublime Text" in buf.value:
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(each, 0)
    if not found:
        return None, None
    hwnd, title = found[0]
    # deliberately not SetForegroundWindow: Sublime draws the caret only in the
    # focused window, and leaving it unfocused removes the caret from the image
    # entirely, which is what lets the crop extend far enough to clear the
    # descender on a closing brace
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    # Grab the client area, not the window rect. A maximized window's rect extends
    # about eight pixels past the visible edges, for the invisible resize border, so
    # grabbing it captures a sliver of whatever sits behind. Stray glyphs from the
    # window behind this one were stretching the crop to the full width.
    client = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    origin = ctypes.wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    bbox = (origin.x, origin.y,
            origin.x + client.right, origin.y + client.bottom)
    return bbox, title


# -------------------------------------------------------------- the scheme ---

def palette_of(path):
    """(background, [level colors]) straight out of a .sublime-color-scheme."""
    raw = io.open(path, encoding="utf-8").read()
    background = re.search(r'"background":\s*"([^"]+)"', raw).group(1)
    if background.startswith("var("):
        name = background[4:-1]
        background = re.search(r'"%s":\s*"([^"]+)"' % re.escape(name), raw).group(1)
    def ramp(prefix):
        out = []
        n = 1
        while True:
            m = re.search(r'"%s%d":\s*"([^"]+)"' % (prefix, n), raw)
            if not m:
                return out
            out.append(m.group(1).upper())
            n += 1

    def one(key):
        m = re.search(r'"%s":\s*"([^"]+)"' % key, raw.split('"rules"')[0])
        return m.group(1).upper() if m else None

    levels, brackets = ramp("json_level"), ramp("json_bracket")
    chrome = [c for c in (one("caret"), one("line_highlight")) if c]
    # A scheme may paint its caret or current line in one of its own depth
    # colors, and Deep JSON - VS Code Dark does: its caret is the editor
    # foreground, which is also level 1. Checking the pixels for that color
    # would flag every level 1 glyph as a stray caret. Those colors are dropped
    # here and returned so the run can say which check it gave up; the plugin
    # reports whether a caret or a highlight could be drawn at all, which is the
    # cause rather than the symptom and does not depend on the palette.
    palette = set(levels) | set(brackets)
    dropped = [c for c in chrome if c in palette]
    chrome = [c for c in chrome if c not in palette]
    return background.upper(), levels, brackets, chrome, dropped


def hex2rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def l1(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


# ---------------------------------------------------------------- cropping ---

def crop_to_text(image, background, levels, expect_lines=0):
    """Trim a client-area grab down to the gutter and the code.

    Rows come from the line-number gutter, not from the code. The gutter carries a
    dense number on every line, including the trailing blank one, so banding it
    gives one band per line and the first and last are unambiguous. Measuring rows
    from the code does not work: the first and last line of every sample hold a
    single thin glyph, and antialiasing can leave it with no pixel within tolerance
    of its palette color, which silently cropped those lines away.

    The right edge does come from the code, with the scrollbar excluded by shape:
    it carries ink down most of the height, whereas a column of text is sparse.
    """
    bg = hex2rgb(background)
    px = image.load()
    w, h = image.size

    def ink(x, y):
        return l1(px[x, y], bg) > INK

    # where the gutter ends: the first run of empty columns after the numbers
    gap = 0
    content = 0
    seen = False
    for x in range(w):
        if any(ink(x, y) for y in range(0, h, 3)):
            seen = True
            gap = 0
        elif seen:
            gap += 1
            if gap >= 4:
                content = x
                break
    if not content:
        return None, "could not separate the gutter from the code"

    # The gutter holds more than line numbers: the caret can sit in it, and there
    # is a marker block in the far-left margin. Both have ink on a single line,
    # while a digit column has ink on nearly every line, so the digits are the
    # columns whose row count is a large fraction of the busiest column's.
    counts = {}
    for x in range(content):
        counts[x] = sum(1 for y in range(h) if ink(x, y))
    peak = max(counts.values()) if counts else 0
    if not peak:
        return None, "no line numbers found"
    digits = [x for x, count in counts.items() if count > peak * 0.3]

    # one band per rendered line
    rows = [y for y in range(h) if any(ink(x, y) for x in digits)]
    if not rows:
        return None, "no line numbers found"
    bands = []
    for y in rows:
        if bands and y - bands[-1][-1] <= 3:
            bands[-1].append(y)
        else:
            bands.append([y])
    if len(bands) < 2:
        return None, "only %d line band(s) found" % len(bands)
    if expect_lines and len(bands) != expect_lines:
        # The gutter numbers every line, so the band count is knowable up front.
        # A mismatch means something merged or split bands, which is exactly how
        # an earlier run cropped a line short without anything looking wrong.
        return None, "banded %d lines, expected %d" % (len(bands), expect_lines)

    # drop the trailing blank line, which is numbered but holds no code
    top = max(0, bands[0][0] - PAD)
    # Room for the descender on a closing brace, which reaches lower than the
    # digits the bands are measured from, while stopping short of the next line's
    # number. With the window unfocused there is no caret in the way.
    # Clearance under the last line's brace, which descends below the digits the
    # bands are measured from. Bounded by the next line's number so that number
    # never appears.
    bottom = min(h, bands[-2][-1] + PAD, bands[-1][0] - 3)

    tall = set()
    for x in range(content, w):
        if sum(1 for y in range(h) if ink(x, y)) > h * 0.6:
            tall.add(x)
    xs = [x for y in range(top, bottom) for x in range(content, w)
          if x not in tall and ink(x, y)]
    if not xs:
        return None, "no code found past the gutter"
    # round up to a multiple of four: every scheme renders identical text, so any
    # difference in the measured edge is antialiasing, and quantizing keeps each
    # sample one consistent size across all ten
    right = min(w, ((max(xs) + PAD * 3) // 4 + 1) * 4)

    # the grab is already the client area, so its own left edge is the gutter
    left = 0
    if right <= left or bottom <= top:
        return None, "degenerate crop box"
    return image.crop((left, top, right, bottom)), None


# how close a pixel must be to a palette color to count as that color. Sublime
# antialiases text, so a short token's glyphs may never reach the pure value; this
# check only has to prove the right scheme is on screen and fully rendered, and
# verify_screenshots.py is the precise arbiter afterwards.
NEAR = 36


def verify(image, background, levels, chrome=()):
    """Is the right scheme fully rendered, with no editor chrome? (ok, detail, missing)"""
    px = image.load()
    w, h = image.size

    # The caret and the current-line highlight are editor furniture, not theme. A
    # two-pixel caret slipped into every image of an earlier run unnoticed, so it
    # is checked for rather than looked for.
    unwanted = {hex2rgb(c): c for c in chrome}
    if unwanted:
        seen = {}
        for y in range(h):
            for x in range(w):
                pixel = px[x, y]
                if pixel in unwanted:
                    seen[pixel] = seen.get(pixel, 0) + 1
        for rgb, count in seen.items():
            if count:
                return False, "%d px of editor chrome (%s)" % (
                    count, unwanted[rgb]), []
    bg = hex2rgb(background)
    # The dominant color has to be this scheme's background. Sampling one corner
    # was the earlier check, and it broke once the crop tightened onto the code:
    # the bottom-left corner lands on a marker block in the left margin.
    tally = {}
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            pixel = px[x, y]
            tally[pixel] = tally.get(pixel, 0) + 1
    modal = max(tally, key=lambda k: tally[k])
    if l1(modal, bg) > 20:
        return False, "dominant color is not the scheme background", []

    wanted = {c: hex2rgb(c) for c in levels}
    found = set()
    for y in range(h):
        for x in range(w):
            pixel = px[x, y]
            if l1(pixel, bg) <= INK:
                continue
            for name, rgb in wanted.items():
                if name not in found and l1(pixel, rgb) <= NEAR:
                    found.add(name)
            if len(found) == len(wanted):
                return True, "", []
    missing = [c for c in levels if c not in found]
    return False, "%d palette color(s) absent" % len(missing), missing


# ------------------------------------------------------------- handshaking ---

def ask(scheme_resource, path, timeout=30.0):
    for leftover in (READY, DONE):
        if os.path.exists(leftover):
            os.remove(leftover)
    with io.open(REQUEST, "w", encoding="utf-8") as fh:
        json.dump({"scheme": scheme_resource, "path": path}, fh)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(READY):
            with io.open(READY, encoding="utf-8") as fh:
                return json.load(fh)
        time.sleep(0.1)
    return None


def release():
    with io.open(DONE, "w", encoding="utf-8") as fh:
        fh.write("1")
    for _ in range(100):
        if not os.path.exists(READY):
            return
        time.sleep(0.05)


def slug(name):
    out = "".join(ch if ch.isalnum() else "-" for ch in name.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


# ------------------------------------------------------------------- main ----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scheme", help="only this scheme")
    ap.add_argument("--sample", help="only this sample")
    ap.add_argument("--keep-alive", action="store_true",
                    help="leave the plugin serving, for repeated runs")
    ap.add_argument("--debug", action="store_true",
                    help="save the raw grab and the crop, and report colors")
    args = ap.parse_args()

    schemes = sorted(glob.glob(os.path.join(REPO, "*.sublime-color-scheme")))
    if args.scheme:
        schemes = [p for p in schemes
                   if os.path.basename(p)[:-21] == args.scheme]
    samples = [s for s in SAMPLES if not args.sample or s == args.sample]
    if not schemes or not samples:
        print("nothing to do; check --scheme / --sample")
        return 1

    os.makedirs(HANDSHAKE, exist_ok=True)
    # ALIVE is deliberately not cleared: the plugin may already be waiting
    for leftover in (REQUEST, READY, DONE, STOP):
        if os.path.exists(leftover):
            os.remove(leftover)

    print("  handshake: %s" % HANDSHAKE)
    print()
    print("  In Sublime Text, open the console with Ctrl+` and run:")
    print()
    print('      sublime.run_command("deep_json_shots")')
    print()
    print("  Then leave the machine alone. The window has to stay visible.")
    print("  Waiting for the plugin (up to 15 minutes)...")
    def plugin_is_live():
        # a heartbeat older than this is a leftover, not a running plugin
        try:
            return time.time() - os.path.getmtime(ALIVE) < 3.0
        except OSError:
            return False

    deadline = time.time() + 900
    while time.time() < deadline and not plugin_is_live():
        time.sleep(0.25)
    if not plugin_is_live():
        print("\n  The plugin never started. Is capture_plugin.py in Packages/User?")
        return 1
    print("  Plugin is up. Capturing.")

    hwnd = find_window()
    placement = maximize(hwnd) if hwnd else None

    made = 0
    failures = []
    try:
        for scheme_path in schemes:
            name = os.path.basename(scheme_path)[:-21]
            background, levels, brackets, chrome, dropped = palette_of(
                scheme_path
            )
            resource = "Packages/%s/%s.sublime-color-scheme" % (PACKAGE, name)
            print("\n  %s" % name)
            if dropped:
                print("    (%s is also a depth color here, so the pixel check"
                      % ", ".join(dropped))
                print("     for it is dropped; the editor still has to report")
                print("     that no caret or highlight can be drawn)")
            for sample in samples:
                sample_path = os.path.join(REPO, "samples", sample + ".json")
                ok = False
                detail = "never attempted"
                for attempt in range(1, 4):
                    state = ask(resource, sample_path)
                    if state is None:
                        detail = "plugin did not respond"
                        break
                    if "error" in state:
                        detail = state["error"]
                        release()
                        continue
                    if state.get("selections"):
                        detail = ("%d selection(s) in the view, so a caret is "
                                  "drawn" % state["selections"])
                        release()
                        continue
                    if state.get("highlight_line"):
                        detail = "the current line is highlighted"
                        release()
                        continue
                    time.sleep(0.35)
                    rect, _title = sublime_window()
                    if rect is None:
                        detail = "no Sublime window found"
                        release()
                        break
                    grab = ImageGrab.grab(bbox=rect).convert("RGB")
                    if args.debug:
                        grab.save(os.path.join(HANDSHAKE, "raw.png"))
                    shot, why = crop_to_text(
                        grab, background, levels + brackets,
                        state.get("lines", 0)
                    )
                    if args.debug and shot is not None:
                        shot.save(os.path.join(HANDSHAKE, "crop.png"))
                        print("      debug: grab %s crop %s -> %s"
                              % (grab.size, shot.size, HANDSHAKE))
                    if shot is None:
                        detail = why
                        release()
                        continue
                    good, why, missing = verify(shot, background, levels, chrome)
                    release()
                    if not good:
                        detail = why
                        if args.debug and missing:
                            print("      debug: missing %s" % " ".join(missing))
                        time.sleep(0.6)
                        continue
                    folder = os.path.join(REPO, "screenshots", slug(name))
                    os.makedirs(folder, exist_ok=True)
                    out = os.path.join(folder, sample + ".png")
                    shot.save(out)
                    made += 1
                    ok = True
                    print("    %-9s %dx%d  %d KB  (attempt %d)"
                          % (sample, shot.size[0], shot.size[1],
                             os.path.getsize(out) // 1024, attempt))
                    break
                if not ok:
                    failures.append("%s/%s: %s" % (name, sample, detail))
                    print("    %-9s FAILED  %s" % (sample, detail))
    finally:
        if not args.keep_alive:
            with io.open(STOP, "w", encoding="utf-8") as fh:
                fh.write("1")
            time.sleep(0.5)
        else:
            print("\n  plugin left running (--keep-alive)")
        restore(hwnd, placement)

    print("\n  %d screenshots written, %d failed" % (made, len(failures)))
    for f in failures:
        print("    " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
