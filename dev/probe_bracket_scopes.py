"""Dump real scope stacks for JSON brackets, straight out of a running ST4.

The depth-colored bracket rules depend on how many ``meta`` atoms a delimiter
carries. That can be read off JSON.sublime-syntax and its shipped syntax tests,
but those only demonstrate depths 1 and 2, and a model that looks right on paper
has been wrong in this project before. So the rules are built against real data
instead.

HOW TO RUN

  1. Copy this file into Packages/User/ (Preferences > Browse Packages...).
  2. Open the Sublime console with Ctrl+` and run:

       sublime.run_command("dump_bracket_scopes")

  3. Watch the console. It opens each sample, waits for the syntax to settle,
     records every brace and bracket, and writes
     dev/fixtures/bracket-scopes.json in the repository.
  4. Delete the copy from Packages/User/ afterwards. Nothing else is touched.

Files are processed one at a time, because a view's scopes are not available
until it has finished loading and highlighting.

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.
"""
import json
import os

import sublime
import sublime_plugin

# The repository this plugin reports into. Change if the checkout moves.
REPO = r"C:\git\Personal\sublime-deep-json"
FALLBACK_REPO = r"C:\git\Personal\sublime-json-color-schemes"

SAMPLES = ("object.json", "array.json", "mixed.json", "openapi.json")
OPENERS = "{["
CLOSERS = "}]"


def repo_root():
    for candidate in (REPO, FALLBACK_REPO):
        if os.path.isdir(os.path.join(candidate, "samples")):
            return candidate
    return None


def nesting_depth(scope_name):
    """How many nesting `meta` atoms the stack carries.

    Every atom beginning with "meta." counts. For a delimiter that is exactly the
    container depth, because a delimiter carries its own container's meta scope and
    nothing else contributes one.
    """
    return sum(1 for atom in scope_name.split() if atom.startswith("meta."))


def is_delimiter(scope_name):
    """Is this character a real delimiter rather than a brace inside a string?

    A path template like "/assets/{assetId}" puts braces inside a quoted string,
    where the editor gives them the string's scopes and no punctuation.section
    atom. Counting those as nesting is wrong, and doing so made the first run of
    this probe report the model broken when it was the counter that was broken.
    """
    return ("punctuation.section.mapping" in scope_name
            or "punctuation.section.sequence" in scope_name)


class DumpBracketScopesCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        root = repo_root()
        if root is None:
            print("probe: could not find the repository; edit REPO in this file")
            return
        self.root = root
        self.queue = list(SAMPLES)
        self.results = []
        print("probe: repository is %s" % root)
        self.next_file()

    def next_file(self):
        if not self.queue:
            self.finish()
            return
        name = self.queue.pop(0)
        path = os.path.join(self.root, "samples", name)
        if not os.path.exists(path):
            print("probe: missing %s, skipping" % path)
            self.next_file()
            return
        view = sublime.active_window().open_file(path)
        self.wait_for(view, name, 0)

    def wait_for(self, view, name, tries):
        if tries > 100:
            print("probe: %s never finished loading" % name)
            self.next_file()
            return
        syntax = view.settings().get("syntax") or ""
        if view.is_loading() or "JSON" not in syntax:
            sublime.set_timeout(lambda: self.wait_for(view, name, tries + 1), 100)
            return
        # one more tick, so highlighting has certainly caught up
        sublime.set_timeout(lambda: self.collect(view, name), 120)

    def collect(self, view, name):
        text = view.substr(sublime.Region(0, view.size()))
        found = 0
        depth = 0
        skipped = 0
        for point, char in enumerate(text):
            if char not in OPENERS and char not in CLOSERS:
                continue
            scope = view.scope_name(point).strip()
            if not is_delimiter(scope):
                skipped += 1
                continue
            if char in OPENERS:
                depth += 1
                expected = depth
            else:
                expected = depth
                depth -= 1
            row, col = view.rowcol(point)
            self.results.append({
                "file": name,
                "row": row + 1,
                "col": col + 1,
                "char": char,
                "expected_depth": expected,
                "meta_atoms": nesting_depth(scope),
                "scope": scope,
            })
            found += 1
        print("probe: %-13s %d delimiters, %d braces inside strings skipped"
              % (name, found, skipped))
        self.next_file()

    def finish(self):
        out = os.path.join(self.root, "dev", "fixtures", "bracket-scopes.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        agree = sum(1 for r in self.results
                    if r["meta_atoms"] == r["expected_depth"])
        payload = {
            "_comment": (
                "Real ST4 scope stacks for JSON delimiters, captured by "
                "dev/probe_bracket_scopes.py. meta_atoms is how many meta.* atoms "
                "the stack carries; expected_depth is the nesting depth counted "
                "from the text. They should be equal for every delimiter."
            ),
            "sublime_build": int(sublime.version()),
            "delimiters": len(self.results),
            "agree_with_depth": agree,
            "cases": self.results,
        }
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("probe: build %s" % sublime.version())
        print("probe: %d delimiters, %d agree with the text depth"
              % (len(self.results), agree))
        if agree != len(self.results):
            print("probe: MODEL IS WRONG for %d of them; see the file"
                  % (len(self.results) - agree))
        print("probe: wrote %s" % out)
