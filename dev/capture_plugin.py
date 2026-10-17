"""Editor half of the screenshot harness: sets up each shot on request.

Sublime Text cannot be driven from outside the way an Electron editor can, and it
has no API for screen coordinates, so capturing is split in two. This plugin owns
everything inside the editor; dev/capture_screenshots.py owns the screen grab and
the verification. They talk through files in a handshake directory.

  capture_screenshots.py  writes request.json  {scheme, path}
  this plugin             opens and styles the view, writes ready.json
  capture_screenshots.py  grabs, verifies, writes done.json
  this plugin             waits for done.json, then takes the next request

HOW TO RUN

  Copy this file into Packages/User/ (Preferences > Browse Packages...), then run
  dev/capture_screenshots.py. It starts the handshake and tells you what to do.
  The plugin restores your sidebar, tabs, minimap and status bar when the run
  finishes, and deletes nothing of yours.

Colors are set per view, not in your settings, so your own color scheme is never
touched.

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.
"""
import json
import os
import tempfile

import sublime
import sublime_plugin

HANDSHAKE = os.path.join(tempfile.gettempdir(), "deep-json-shots")
REQUEST = os.path.join(HANDSHAKE, "request.json")
READY = os.path.join(HANDSHAKE, "ready.json")
DONE = os.path.join(HANDSHAKE, "done.json")
STOP = os.path.join(HANDSHAKE, "stop")
ALIVE = os.path.join(HANDSHAKE, "alive")
# What the editor looked like before the run. On disk rather than in memory:
# Sublime reloads a plugin whenever its file changes, and a reload gives the
# module a fresh namespace, so a saved-in-memory copy is gone and the window
# keeps the harness's bare chrome with nothing left that knows what it was.
# Written once per run and deleted only after the chrome is actually back, so
# it also survives restarting the editor.
SAVED = os.path.join(HANDSHAKE, "saved.json")

POLL_MS = 150

_running = False


def _beat():
    """Refresh the liveness file.

    It is a heartbeat rather than a flag because a leftover flag from a previous
    run looks exactly like a running plugin, and the capturer then fires a request
    at nobody. Staleness is the thing worth detecting.
    """
    try:
        with open(ALIVE, "w", encoding="utf-8") as fh:
            fh.write("1")
    except (IOError, OSError):
        pass


def _chrome(window, visible):
    window.set_minimap_visible(visible)
    window.set_sidebar_visible(visible)
    window.set_tabs_visible(visible)
    window.set_status_bar_visible(visible)
    window.set_menu_visible(visible)


def _save(window):
    return {
        "minimap": window.is_minimap_visible(),
        "sidebar": window.is_sidebar_visible(),
        "tabs": window.get_tabs_visible(),
        "status": window.is_status_bar_visible(),
        "menu": window.is_menu_visible(),
    }


def _remember(window):
    """Record the chrome, unless an earlier run already did.

    Not overwritten, because what is already there is the real pre-harness
    state: if a run ended without restoring, the chrome is bare now, and
    saving that would make the bare state the thing to return to.
    """
    if os.path.exists(SAVED):
        return
    with open(SAVED, "w", encoding="utf-8") as fh:
        json.dump(_save(window), fh)


def _forget():
    try:
        os.remove(SAVED)
    except OSError:
        pass


def _recall():
    try:
        with open(SAVED, encoding="utf-8") as fh:
            return json.load(fh)
    except (IOError, OSError, ValueError):
        return None


def _restore(window, saved):
    window.set_minimap_visible(saved["minimap"])
    window.set_sidebar_visible(saved["sidebar"])
    window.set_tabs_visible(saved["tabs"])
    window.set_status_bar_visible(saved["status"])
    window.set_menu_visible(saved["menu"])


def _style_view(view, scheme):
    s = view.settings()
    s.set("color_scheme", scheme)
    # everything here exists to make the pixels deterministic
    s.set("line_numbers", True)
    s.set("draw_indent_guides", False)
    s.set("draw_white_space", "none")
    # Spell check underlines "uuid", "cve", "tcp" and friends. It is on in many
    # people's settings and the squiggles are drawn in Sublime's own red, which
    # no scheme declares, so the capturer cannot detect them by color.
    s.set("spell_check", False)
    s.set("draw_unicode_white_space", "none")
    s.set("highlight_line", False)
    # The caret lives on the trailing blank line, outside the crop, but it is
    # drawn four pixels taller than its line at each end by default, so it
    # reached up into the last line of code. These three confine it.
    s.set("caret_extra_top", 0)
    s.set("caret_extra_bottom", 0)
    s.set("caret_extra_width", 0)
    # and these two drop the current-line markers in the gutter, which both
    # showed in the image and disturbed the capturer's line banding
    s.set("highlight_gutter", False)
    s.set("highlight_line_number", False)
    s.set("match_brackets", False)
    s.set("draw_centered", False)
    s.set("word_wrap", False)
    s.set("rulers", [])
    s.set("fold_buttons", False)
    # Sublime marks lines that differ from git in the gutter, and the samples do
    # differ, so those marks would land in every shot
    s.set("mini_diff", False)
    s.set("overlay_scroll_bars", "enabled")
    # Grayscale rather than subpixel antialiasing. ClearType gives each channel
    # its own coverage, so a thin glyph never reaches its palette color and the
    # pixel verifier reads values like #262692 off a green token. Grayscale also
    # loses the colored fringes, which is what the VS Code port does too.
    s.set("font_options", ["gray_antialias"])
    s.set("margin", 0)
    s.set("scroll_past_end", False)
    # No selection, so no caret is drawn anywhere. A caret cannot be kept out of
    # frame: it starts at the top of its row, so parking it on the trailing blank
    # line still left two pixels under the last brace and no room for clearance.
    #
    # The cost is that a package here whose selection listener does view.sel()[0]
    # raises IndexError, because Selection raises when empty, and an unhandled
    # plugin exception opens the console. Hiding the panel afterwards deals with
    # that, which is why hide_panel runs both now and again just before the
    # capturer is told the view is ready.
    view.sel().clear()
    view.set_viewport_position((0, 0), False)


class DeepJsonShotsCommand(sublime_plugin.ApplicationCommand):
    """Start serving capture requests."""

    def run(self):
        global _running
        if _running:
            print("shots: already running")
            return
        # start in either order: whichever side comes up first waits
        os.makedirs(HANDSHAKE, exist_ok=True)
        for leftover in (REQUEST, READY, DONE, STOP):
            try:
                os.remove(leftover)
            except OSError:
                pass
        window = sublime.active_window()
        _remember(window)
        _chrome(window, False)
        _running = True
        _beat()
        print("shots: serving requests from %s" % HANDSHAKE)
        self.serve()

    def serve(self):
        global _running
        if not _running:
            return
        if os.path.exists(STOP):
            self.finish()
            return
        _beat()
        if os.path.exists(REQUEST) and not os.path.exists(READY):
            try:
                with open(REQUEST, encoding="utf-8") as fh:
                    req = json.load(fh)
            except (IOError, OSError, ValueError):
                sublime.set_timeout(self.serve, POLL_MS)
                return
            self.prepare(req, 0)
            return
        sublime.set_timeout(self.serve, POLL_MS)

    def prepare(self, req, tries):
        window = sublime.active_window()
        view = window.open_file(req["path"])
        if view.is_loading():
            if tries > 60:
                self.announce({"error": "never loaded"})
                return
            sublime.set_timeout(lambda: self.prepare(req, tries + 1), 100)
            return
        _style_view(view, req["scheme"])
        window.focus_view(view)
        # close the console or find panel if either is open; both steal height
        window.run_command("hide_panel", {"cancel": True})
        # let the scheme and the relayout settle before saying ready
        def ready():
            # the selection listener in another package may have reopened it
            window.run_command("hide_panel", {"cancel": True})
            self.announce({
                "lines": view.rowcol(view.size())[0] + 1,
                "scheme": req["scheme"],
                # The capturer asserts both of these. A caret or a current-line
                # highlight in the image is the editor showing through, and
                # checking the cause here is better than checking the symptom
                # by color: a scheme whose caret is one of its own palette
                # colors cannot be checked for that color in the pixels.
                "selections": len(view.sel()),
                "highlight_line": bool(view.settings().get("highlight_line")),
            })

        sublime.set_timeout(ready, 450)

    def announce(self, payload):
        with open(READY, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        self.await_done(0)

    def await_done(self, tries):
        global _running
        if os.path.exists(STOP):
            self.finish()
            return
        if os.path.exists(DONE):
            for path in (REQUEST, READY, DONE):
                try:
                    os.remove(path)
                except OSError:
                    pass
            sublime.set_timeout(self.serve, POLL_MS)
            return
        _beat()
        if tries > 400:
            print("shots: capturer went quiet; stopping")
            self.finish()
            return
        sublime.set_timeout(lambda: self.await_done(tries + 1), POLL_MS)

    def finish(self):
        global _running
        _running = False
        try:
            os.remove(ALIVE)
        except OSError:
            pass
        saved = _recall()
        if saved:
            for window in sublime.windows():
                _restore(window, saved)
            _forget()
            print("shots: finished, window restored")
        else:
            print("shots: finished, but nothing recorded the chrome to restore")
