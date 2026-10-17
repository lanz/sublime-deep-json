"""Extract Dark 2026, the theme Visual Studio Code ships, into Sublime's format.

Deep JSON - VS Code Dark is built on it, the way Deep JSON - Monokai Neue is built
on Josh Kaplan's theme: the base palette and syntax rules are kept as they are and
the depth colors are layered on top. Its VS Code sibling reads the same theme, so
the two editors show the same palette.

The theme is spread across a chain of files. 2026-dark.json includes
dark_modern.json, which includes dark_plus.json, which includes dark_vs.json, and
each level overrides the one below. This resolves that chain the way VS Code does,
nearest file winning, then translates the result and writes it to
palettes/vscode-dark-base.json so the build does not depend on an editor being
installed.

Translating is mostly mechanical, because a VS Code theme's tokenColors are
TextMate rules and so are Sublime's: a scope selector, a foreground, a font style.
Only three things need handling.

  - The workbench colors are a flat namespace rather than tmTheme's fixed keys, so
    GLOBALS below maps the editor ones onto their Sublime equivalents by hand.
  - A VS Code rule's scope may be a list, meaning any of these. Sublime spells
    that as one comma-separated string.
  - Sublime has no strikethrough font style, so that one is dropped. Nothing in
    this theme uses it outside of Markdown.

Usage:
    python extract_vscode_dark_base.py [path to theme-defaults/themes]

With no argument it looks for the themes inside a local Visual Studio Code or
VSCodium install.

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.

The data this produces is from the MIT-licensed microsoft/vscode repository.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "palettes", "vscode-dark-base.json")
ENTRY = "2026-dark.json"

# A Microsoft build keeps its resources under a directory named for the build
# hash, which changes with every update, so the themes are searched for rather
# than hardcoded.
SEARCH_ROOTS = (
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code"),
    os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft VS Code"),
    os.path.join(os.environ.get("ProgramFiles", ""), "VSCodium"),
    "/Applications/Visual Studio Code.app/Contents",
    "/usr/share/code",
)
TAIL = os.path.join("resources", "app", "extensions", "theme-defaults", "themes")

# VS Code workbench color -> .sublime-color-scheme global. Sublime has a fixed
# set of these and VS Code has hundreds, so this is the editor surface the two
# actually share.
GLOBALS = {
    "editor.background": "background",
    "editor.foreground": "foreground",
    "editorCursor.foreground": "caret",
    "editorWhitespace.foreground": "invisibles",
    "editor.lineHighlightBackground": "line_highlight",
    "editor.selectionBackground": "selection",
    "editor.findMatchBackground": "find_highlight",
    "editorGutter.background": "gutter",
    "editorLineNumber.foreground": "gutter_foreground",
    "editorIndentGuide.background1": "guide",
    "editorIndentGuide.activeBackground1": "active_guide",
}

# A few globals have no VS Code counterpart. The matched-bracket color is the one
# Dark 2026 fills the match with, underlined to match the rest of the family,
# since Sublime draws a fill as a foreground. The find highlight is a dark blue,
# so its text stays the editor foreground rather than tmTheme's usual black.
EXTRA = {
    "find_highlight_foreground": "editor.foreground",
    "brackets_foreground": "#3994BC",
    "bracket_contents_foreground": "#3994BC",
    "brackets_options": "underline",
    "bracket_contents_options": "underline",
    "tags_options": "stippled_underline",
}

# Sublime's font styles. strikethrough is VS Code's alone.
FONT_STYLES = ("bold", "italic", "underline")


def find_themes():
    if len(sys.argv) > 1:
        return sys.argv[1]
    for root in SEARCH_ROOTS:
        if not root or not os.path.isdir(root):
            continue
        direct = os.path.join(root, TAIL)
        if os.path.exists(os.path.join(direct, ENTRY)):
            return direct
        for entry in sorted(os.listdir(root)):
            nested = os.path.join(root, entry, TAIL)
            if os.path.exists(os.path.join(nested, ENTRY)):
                return nested
    return None


def resolve(folder, name):
    """A theme with its include chain flattened, nearest file winning."""
    with io.open(os.path.join(folder, name), encoding="utf-8") as fh:
        theme = json.load(fh)
    parent = {"colors": {}, "tokenColors": [], "chain": []}
    if theme.get("include"):
        parent = resolve(folder, theme["include"].lstrip("./"))
    colors = dict(parent["colors"])
    colors.update(theme.get("colors", {}))
    # later rules win in VS Code, so the child's go last
    tokens = list(parent["tokenColors"]) + list(theme.get("tokenColors", []))
    return {"colors": colors, "tokenColors": tokens,
            "name": theme.get("name"), "chain": parent["chain"] + [name]}


def to_globals(colors):
    out = {}
    for source, key in GLOBALS.items():
        if source in colors:
            out[key] = colors[source]
    for key, value in EXTRA.items():
        out[key] = colors.get(value, value)
    return out


def font_style(declared):
    """A VS Code fontStyle as a Sublime one, minus what Sublime cannot draw."""
    kept = [word for word in declared.split() if word in FONT_STYLES]
    return " ".join(kept)


def to_rules(token_colors):
    rules = []
    for rule in token_colors:
        scope = rule.get("scope")
        if not scope:
            continue
        if not isinstance(scope, str):
            scope = ", ".join(scope)
        settings = rule.get("settings", {})
        out = {"scope": scope}
        if rule.get("name"):
            out["name"] = rule["name"]
        if settings.get("foreground"):
            out["foreground"] = settings["foreground"]
        if settings.get("background"):
            out["background"] = settings["background"]
        if "fontStyle" in settings:
            # an empty fontStyle is meaningful: it clears inherited emphasis
            out["font_style"] = font_style(settings["fontStyle"])
        rules.append(out)
    return rules


def main():
    folder = find_themes()
    if not folder:
        print("Could not find VS Code's bundled themes. Pass the path to the")
        print("theme-defaults/themes folder, or to a checkout of the")
        print("extensions/theme-defaults/themes folder in microsoft/vscode.")
        return 1

    theme = resolve(folder, ENTRY)
    payload = {
        "_comment": (
            "Dark 2026, the theme Visual Studio Code ships, with its include chain "
            "flattened and translated into Sublime's format. Generated by "
            "dev/extract_vscode_dark_base.py; do not edit by hand."
        ),
        "source": "Dark 2026, from the MIT-licensed microsoft/vscode repository",
        "vscode_name": theme["name"],
        "chain": theme["chain"],
        "globals": to_globals(theme["colors"]),
        "rules": to_rules(theme["tokenColors"]),
    }
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    with io.open(DEST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print("  read     %s" % folder)
    print("  chain    %s" % " -> ".join(theme["chain"]))
    print("  globals  %d of %d colors" % (len(payload["globals"]),
                                          len(theme["colors"])))
    print("  rules    %d of %d token rules" % (len(payload["rules"]),
                                               len(theme["tokenColors"])))
    print("  wrote    %s" % os.path.relpath(DEST, os.path.dirname(HERE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
