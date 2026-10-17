"""Extract Monokai Neue's globals and base rules into palettes/monokai-neue-base.json.

Monokai Neue's own JSON depth rules are dropped, since build.py generates a fuller set
covering arrays, string values, numbers and booleans.

Usage:
    python extract_neue_base.py [path to Monokai Neue.sublime-package or .tmTheme]

With no argument it looks for Monokai Neue in Sublime Text's Installed Packages.

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.

The data this produces is from Monokai Neue, Copyright (c) 2015 Josh Kaplan, MIT licensed.
"""
import io
import json
import os
import plistlib
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "palettes", "monokai-neue-base.json")

PACKAGE = "Monokai Neue.sublime-package"
TMTHEME = "Monokai-Neue.tmTheme"

# tmTheme global key -> .sublime-color-scheme global key
GLOBALS = {
    "background": "background",
    "foreground": "foreground",
    "caret": "caret",
    "invisibles": "invisibles",
    "lineHighlight": "line_highlight",
    "selection": "selection",
    "selectionBorder": "selection_border",
    "findHighlight": "find_highlight",
    "findHighlightForeground": "find_highlight_foreground",
    "activeGuide": "active_guide",
    "stackGuide": "stack_guide",
    "guide": "guide",
    "bracketsForeground": "brackets_foreground",
    "bracketsOptions": "brackets_options",
    "bracketContentsForeground": "bracket_contents_foreground",
    "bracketContentsOptions": "bracket_contents_options",
    "tagsForeground": "tags_foreground",
    "tagsOptions": "tags_options",
    "misspelling": "misspelling",
    "gutter": "gutter",
    "gutterForeground": "gutter_foreground",
    "shadow": "shadow",
}


def candidate_dirs():
    """Where Sublime Text keeps Installed Packages, per platform."""
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", "")
    for base in (
        os.path.join(appdata, "Sublime Text"),
        os.path.join(appdata, "Sublime Text 3"),
        os.path.join(home, "Library", "Application Support", "Sublime Text"),
        os.path.join(home, ".config", "sublime-text"),
    ):
        if base and os.path.isdir(base):
            yield os.path.join(base, "Installed Packages")


def find_source():
    for d in candidate_dirs():
        p = os.path.join(d, PACKAGE)
        if os.path.exists(p):
            return p
    return None


def read_tmtheme(path):
    """Bytes of the tmTheme, from either the .sublime-package zip or a loose file."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith(".tmTheme")]
            if not names:
                sys.exit("no .tmTheme inside %s" % path)
            print("reading %s from %s" % (names[0], os.path.basename(path)))
            return z.read(names[0])
    with open(path, "rb") as fh:
        return fh.read()


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else find_source()
    if not src:
        sys.exit(
            "Could not find %s in Sublime Text's Installed Packages.\n"
            "Install Monokai Neue, or pass the path to it:\n"
            "    python extract_neue_base.py \"/path/to/%s\"" % (PACKAGE, PACKAGE)
        )
    if not os.path.exists(src):
        sys.exit("no such file: %s" % src)

    raw = read_tmtheme(src)
    # The file opens with an XML comment before the <?xml?> declaration, which is not
    # valid XML. Sublime tolerates it; plistlib does not, so drop the preamble.
    start = raw.find(b"<?xml")
    if start > 0:
        print("stripped %d bytes of preamble before the XML declaration" % start)
        raw = raw[start:]
    plist = plistlib.loads(raw)

    out_globals = {}
    out_rules = []
    dropped = 0
    for item in plist["settings"]:
        scope = item.get("scope")
        settings = dict(item.get("settings", {}))
        if scope is None:
            for k, v in settings.items():
                if k in GLOBALS:
                    out_globals[GLOBALS[k]] = v
            continue
        if ".json" in scope:
            dropped += 1  # replaced by the generated depth rules
            continue
        rule = {"scope": scope}
        if item.get("name"):
            rule["name"] = item["name"]
        for key, src_key in (("foreground", "foreground"),
                             ("background", "background"),
                             ("font_style", "fontStyle")):
            if settings.get(src_key) is not None:
                rule[key] = settings[src_key]
        out_rules.append(rule)

    doc = {
        "_comment": (
            "Globals and base syntax rules extracted from Monokai Neue's %s, by Josh "
            "Kaplan. Its own JSON depth rules are omitted; this project generates a "
            "fuller set. Regenerate with dev/extract_neue_base.py." % TMTHEME
        ),
        "source": "Monokai Neue by Josh Kaplan",
        "tmtheme_name": plist.get("name"),
        "tmtheme_author": plist.get("author"),
        "globals": out_globals,
        "rules": out_rules,
    }

    folder = os.path.dirname(DEST)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    io.open(DEST, "w", encoding="utf-8", newline="\n").write(
        json.dumps(doc, indent=1) + "\n"
    )
    print("wrote %s" % os.path.relpath(DEST, HERE))
    print("  globals: %d   base rules: %d   dropped %d json rules"
          % (len(out_globals), len(out_rules), dropped))


if __name__ == "__main__":
    main()
