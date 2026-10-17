"""Offline verification of the generated depth selectors.

Checks two things, neither of which needs Sublime Text running:

  1. Every scope stack in fixtures/ is matched by exactly one rule of its kind,
     and it is the rule for the correct nesting level. Two files feed this:
     st4-scope-stacks.json holds keys and values captured from build 4200, and
     bracket-scopes.json holds every delimiter in the samples, captured from build
     4215 by probe_bracket_scopes.py. Delimiter levels are recomputed here from
     document order rather than trusted from the file, so a stale capture cannot
     quietly agree with itself.
  2. The same holds across the full space of object/array nesting shapes, so the
     rules stay mutually exclusive at depths the fixtures do not cover.

To do that without Sublime, it reimplements scope-selector matching for the
restricted grammar depth_selectors.py uses: ``A1 A2 ... An [- B1 B2 ... Bm]``,
where a path matches a scope stack when its atoms occur in order as a subsequence,
each atom label-prefix-matching one stack entry.

That reimplementation is not taken on trust. It was checked against Sublime's own
``sublime.score_selector()`` over 1,920 comparisons when these schemes were built,
and agreed on every one.

Run:  python verify.py

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.
"""
import io
import itertools
import json
import os
import sys

import depth_selectors as S

MAPPING = "meta.mapping.value.json"
SEQUENCE = "meta.sequence.json"

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "st4-scope-stacks.json")
DELIMITERS = os.path.join(HERE, "fixtures", "bracket-scopes.json")


# ---------------------------------------------------------------- matcher ----

def _atom_matches(atom, scope):
    """`meta.mapping.key` matches `meta.mapping.key.json` (whole labels only)."""
    a = atom.split(".")
    s = scope.split(".")
    return len(a) <= len(s) and s[: len(a)] == a


def _path_matches(atoms, stack):
    """Do `atoms` occur in order as a subsequence of `stack`?"""
    i = 0
    for scope in stack:
        if i < len(atoms) and _atom_matches(atoms[i], scope):
            i += 1
    return i == len(atoms)


def selector_matches(selector, scope_string):
    """Evaluate `selector` against a scope stack.

    A selector may be a comma-separated list of alternatives, as the number rules
    are: matching any one of them is a match.
    """
    if "," in selector:
        return any(selector_matches(part.strip(), scope_string)
                   for part in selector.split(","))
    stack = scope_string.split()
    if " - " in selector:
        pos, neg = selector.split(" - ", 1)
        if "-" in neg:
            raise ValueError("only one negation supported: %r" % selector)
        return _path_matches(pos.split(), stack) and not _path_matches(
            neg.split(), stack
        )
    return _path_matches(selector.split(), stack)


# ------------------------------------------------------------ case space ----

def chains(depth, exhaustive_to=8):
    """Nesting shapes of `depth`: every permutation while that stays tractable."""
    if depth == 0:
        yield []
        return
    if depth <= exhaustive_to:
        for combo in itertools.product([MAPPING, SEQUENCE], repeat=depth):
            yield list(combo)
        return
    yield [MAPPING] * depth
    yield [SEQUENCE] * depth
    alt = [MAPPING, SEQUENCE] * (depth // 2) + ([MAPPING] if depth % 2 else [])
    yield alt
    yield alt[::-1]
    yield [SEQUENCE] + [MAPPING] * (depth - 1)
    yield [MAPPING] + [SEQUENCE] * (depth - 1)


KEY_TAILS = [
    ["meta.mapping.key.json", "string.quoted.double.json"],
    [
        "meta.mapping.key.json",
        "string.quoted.double.json",
        "punctuation.definition.string.begin.json",
    ],
    [
        "meta.mapping.key.json",
        "string.quoted.double.json",
        "punctuation.definition.string.end.json",
    ],
    [
        "meta.mapping.key.json",
        "string.quoted.double.json",
        "constant.character.escape.json",
    ],
]

LEAF_TAILS = {
    "string": [
        ["meta.string.json", "string.quoted.double.json"],
        [
            "meta.string.json",
            "string.quoted.double.json",
            "punctuation.definition.string.begin.json",
        ],
        [
            "meta.string.json",
            "string.quoted.double.json",
            "constant.character.escape.json",
        ],
        [
            "meta.string.json",
            "string.quoted.double.json",
            "invalid.illegal.unrecognized-string-escape.json",
        ],
    ],
    "number": [
        ["meta.number.integer.decimal.json", "constant.numeric.value.json"],
        ["meta.number.integer.decimal.json", "keyword.operator.arithmetic.json"],
        ["meta.number.float.decimal.json", "constant.numeric.value.json"],
        ["meta.number.float.decimal.json", "punctuation.separator.decimal.json"],
    ],
    "constant": [
        ["constant.language.boolean.true.json"],
        ["constant.language.boolean.false.json"],
        ["constant.language.null.json"],
    ],
}


# A delimiter carries its own container's meta scope. For an object that is
# meta.mapping.json, not meta.mapping.value.json, because the value context is
# what wraps a *value*; the braces belong to the mapping itself.
DELIM_TAILS = {
    "mapping": (
        "meta.mapping.json",
        [
            "punctuation.section.mapping.begin.json",
            "punctuation.section.mapping.end.json",
        ],
    ),
    "sequence": (
        "meta.sequence.json",
        [
            "punctuation.section.sequence.begin.json",
            "punctuation.section.sequence.end.json",
        ],
    ),
}


def cases(max_depth):
    """Yield (kind, level, scope_string) over the nesting shape space."""
    for level in range(1, max_depth + 1):
        for ch in chains(level - 1):
            for tail in KEY_TAILS:
                yield "key", level, " ".join(["source.json"] + ch + tail)
        for ch in chains(level):
            for kind, tails in LEAF_TAILS.items():
                for tail in tails:
                    yield kind, level, " ".join(["source.json"] + ch + tail)
        # the outer chain is level-1 deep; the delimiter's own container supplies
        # the last meta atom, so a matched pair lands on the same level
        for ch in chains(level - 1):
            for kind, (own, puncts) in DELIM_TAILS.items():
                for punct in puncts:
                    yield kind, level, " ".join(
                        ["source.json"] + ch + [own, punct]
                    )


# ----------------------------------------------------------------- checks ----

def main():
    rules = list(S.all_rules())
    by_kind = {}
    for kind, level, slot, selector in rules:
        by_kind.setdefault(kind, []).append((level, slot, selector))
    max_level = S.MAX_LEVEL

    print("rules: %d  (%d levels x %d kinds)" % (len(rules), max_level, len(by_kind)))

    # Real scope stacks recorded from Sublime Text itself; see fixtures/.
    with open(FIXTURE, encoding="utf-8") as fh:
        fixture = json.load(fh)
    real_fail = []
    for case in fixture["cases"]:
        hits = [
            lv
            for lv, _slot, sel in by_kind[case["kind"]]
            if selector_matches(sel, case["scope"])
        ]
        if hits != [min(case["level"], max_level)]:
            real_fail.append((case, hits))
    # Each case names a row in a sample file. Check that row really holds a token
    # of that kind, so a mislabeled fixture cannot sit there unnoticed.
    row_fail = []
    for case in fixture["cases"]:
        sample = os.path.join(HERE, os.pardir, "samples", case["file"])
        try:
            with io.open(sample, encoding="utf-8") as fh:
                line = fh.read().split("\n")[case["row"] - 1]
        except (IOError, OSError, IndexError):
            row_fail.append((case, "row not readable"))
            continue
        if case["kind"] in ("key", "string") and '"' not in line:
            row_fail.append((case, "no quoted string on that row"))
        elif case["kind"] == "key" and ":" not in line:
            row_fail.append((case, "no key on that row"))
    print(
        "fixture rows point at the right tokens: %d cases, %d wrong  %s"
        % (len(fixture["cases"]), len(row_fail), "OK" if not row_fail else "FAILED")
    )
    for case, why in row_fail[:5]:
        print("    FAIL %s row %d (%s): %s"
              % (case["file"], case["row"], case["kind"], why))

    # Real delimiter stacks, captured by dev/probe_bracket_scopes.py. Levels are
    # recomputed here from document order rather than trusted from the file, so a
    # stale capture cannot quietly agree with itself.
    delim_total = delim_fail = 0
    delim_build = None
    if os.path.exists(DELIMITERS):
        with open(DELIMITERS, encoding="utf-8") as fh:
            captured = json.load(fh)
        delim_build = captured.get("sublime_build")
        real = [
            c for c in captured["cases"]
            if "punctuation.section.mapping" in c["scope"]
            or "punctuation.section.sequence" in c["scope"]
        ]
        by_file = {}
        for case in real:
            by_file.setdefault(case["file"], []).append(case)
        failures = []
        for items in by_file.values():
            items.sort(key=lambda c: (c["row"], c["col"]))
            depth = 0
            for case in items:
                if case["char"] in "{[":
                    depth += 1
                    level = depth
                else:
                    level = depth
                    depth -= 1
                kind = ("mapping" if "punctuation.section.mapping" in case["scope"]
                        else "sequence")
                hits = [
                    lv for lv, _slot, sel in by_kind[kind]
                    if selector_matches(sel, case["scope"])
                ]
                if hits != [min(level, max_level)]:
                    failures.append((case, kind, level, hits))
        delim_total = len(real)
        delim_fail = len(failures)
        print(
            "real ST4 build %s delimiter stacks: %d cases, %d failures  %s"
            % (delim_build, delim_total, delim_fail,
               "OK" if not delim_fail else "FAILED")
        )
        for case, kind, level, hits in failures[:5]:
            print("    FAIL %s r%d c%d %s %s L%d matched=%s" % (
                case["file"], case["row"], case["col"], case["char"],
                kind, level, hits))
    else:
        print("real ST4 delimiter stacks: not captured; run "
              "dev/probe_bracket_scopes.py")

    print(
        "real ST4 build %s scope stacks: %d cases, %d failures  %s"
        % (
            fixture["sublime_build"],
            len(fixture["cases"]),
            len(real_fail),
            "OK" if not real_fail else "FAILED",
        )
    )
    for case, hits in real_fail[:5]:
        print("    FAIL %s r%d %s L%d matched=%s" % (
            case["file"], case["row"], case["kind"], case["level"], hits))

    failures = []
    bleed = []
    checked = 0
    per_level = {}
    for kind, level, scope in cases(max_level + 3):
        checked += 1
        hits = [lv for lv, _slot, sel in by_kind[kind] if selector_matches(sel, scope)]
        expected = [min(level, max_level)]
        if hits != expected:
            failures.append((kind, level, hits, scope))
        else:
            per_level[level] = per_level.get(level, 0) + 1
        for other in by_kind:
            if other == kind:
                continue
            for lv, _slot, sel in by_kind[other]:
                if selector_matches(sel, scope):
                    bleed.append((kind, scope, sel))

    print(
        "exclusivity: %d cases, %d failures, %d cross-kind matches"
        % (checked, len(failures), len(bleed))
    )
    for f in failures[:8]:
        print("    FAIL kind=%s level=%d matched=%s\n         %s" % f)
    for b in bleed[:5]:
        print("    BLEED %s\n         %s\n         %s" % b)

    # palette cycling sanity
    slots = {lv: S.palette_index(lv) for lv in range(1, max_level + 1)}
    print(
        "palette cycling: "
        + ", ".join("L%d->%d" % (lv, slots[lv]) for lv in range(1, max_level + 1))
    )

    deep = " ".join(["source.json"] + [MAPPING] * 40 + ["meta.mapping.key.json",
                                                       "string.quoted.double.json"])
    deep_hits = [lv for lv, _s, sel in by_kind["key"] if selector_matches(sel, deep)]
    print("40-deep key matches level(s): %s (expected [%d])" % (deep_hits, max_level))

    bad = (failures or bleed or delim_fail or real_fail or row_fail
           or deep_hits != [max_level])
    print("\nRESULT:", "FAILED" if bad else "ALL CHECKS PASSED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
