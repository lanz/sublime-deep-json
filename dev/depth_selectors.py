"""Selector model for depth-colored JSON in Sublime Text 4.

How Sublime Text 4 scopes nested JSON (JSON.sublime-syntax version 2), confirmed
against a running build 4200:

  object key   at level N : source.json + (N-1) nesting scopes + meta.mapping.key.json
                                                               + string.quoted.double.json
  string value at level N : source.json + N nesting scopes + meta.string.json
                                                           + string.quoted.double.json
  number       at level N : source.json + N nesting scopes + meta.number.*.json
  true/false/null at lvl N: source.json + N nesting scopes + constant.language.*.json
  { } [ ] at level N      : source.json + N nesting scopes
                                        + punctuation.section.{mapping,sequence}.*

A delimiter carries its own container's meta scope, so a matched pair sits at the
same depth and takes the same color. That is worth stating because the syntax
pushes the object context *after* matching the opening brace, which suggests the
opener would be one level shallower. It is not: meta_scope covers the text that
pushes and pops a context, and JSON.sublime-syntax uses meta_scope rather than
meta_content_scope. Its own syntax tests assert as much, and
dev/fixtures/bracket-scopes.json records it from a running build.

The delimiter rules name punctuation.section.mapping and
punctuation.section.sequence rather than the shorter punctuation.section, which
would also match merge-conflict markers. Those carry a meta.block.conflict.* atom
of their own, so inside an object they would count one level too deep and be
painted as a bracket.

A "nesting scope" is either ``meta.mapping.value.json`` (a value inside an object)
or ``meta.sequence.json`` (an element inside an array). Both are matched by the
bare selector atom ``meta``, and no *other* meta scope can appear among them, so a
run of K ``meta`` atoms means "nested at least K deep".

A run of K atoms also matches anything deeper than K, so every shallower rule
matches a deep token too. Sublime Text 4 build 4200 paints the most deeply nested
of the matching rules, in either file order, but that is not documented anywhere.
Rather than depend on it, every rule is made mutually exclusive with
``- <deeper chain>``: level N matches "at least N deep" AND NOT "at least N+1
deep". Exactly one rule can then match a given token, whatever the ranking rules
happen to be.

The key/string rules also carry a trailing ``string`` atom purely to raise their
score, so they still win if an older same-named scheme is merged in by Sublime.
Numbers deliberately stop at ``meta.number`` rather than adding
``constant.numeric``: the sign in ``-3`` is ``keyword.operator.arithmetic.json``,
and requiring ``constant.numeric`` would leave it uncolored.

Copyright (C) 2026 Lance Duvall
SPDX-License-Identifier: AGPL-3.0-or-later
Project: <https://github.com/lanz/sublime-deep-json>
Licensed under the GNU Affero General Public License, version 3 or later.
See LICENSE, or <https://www.gnu.org/licenses/>.
"""

# Default number of colors a palette walks before repeating. A theme whose
# ramp is a different length passes its own size instead.
PALETTE_SIZE = 10
# Deepest level given its own rule; anything deeper keeps the last color.
MAX_LEVEL = 20


def _meta(count):
    """A run of `count` wildcard nesting atoms, space-terminated."""
    return "meta " * count


def key_selector(level, last):
    """Selector for an object key at `level`."""
    sel = "source.json {0}meta.mapping.key string".format(_meta(level - 1))
    if not last:
        sel += " - {0}meta.mapping.key".format(_meta(level))
    return sel


def _leaf_selector(level, last, leaf, tail=""):
    sel = "source.json {0}{1}{2}".format(_meta(level), leaf, tail)
    if not last:
        sel += " - {0}{1}".format(_meta(level + 1), leaf)
    return sel


def string_selector(level, last):
    """Selector for a string value / array element at `level`."""
    return _leaf_selector(level, last, "meta.string", " string")


# The parts of a number that a base color scheme also claims. Monokai's own
# rules target constant.numeric for the digits and keyword for a leading minus,
# both of which are the token's innermost scope. Sublime weights the innermost
# match heavily, so a depth rule that stops at the outer meta.number loses to
# them and the number comes out the base palette's color rather than its depth
# color. Naming the inner scopes as well is what wins. The decimal point has no
# competing rule, so the plain meta.number form still covers it.
NUMBER_PARTS = ("", " constant.numeric", " keyword.operator")


def number_selector(level, last):
    """Selector for a numeric value / array element at `level`."""
    return ", ".join(
        _leaf_selector(level, last, "meta.number", part) if not part else
        _number_part(level, last, part)
        for part in NUMBER_PARTS
    )


def _number_part(level, last, part):
    sel = "source.json {0}meta.number{1}".format(_meta(level), part)
    if not last:
        sel += " - {0}meta.number{1}".format(_meta(level + 1), part)
    return sel


def constant_selector(level, last):
    """Selector for true / false / null at `level`."""
    return _leaf_selector(level, last, "constant.language")


def mapping_selector(level, last):
    """Selector for a { or } delimiting an object at `level`."""
    return _leaf_selector(level, last, "punctuation.section.mapping")


def sequence_selector(level, last):
    """Selector for a [ or ] delimiting an array at `level`."""
    return _leaf_selector(level, last, "punctuation.section.sequence")


BUILDERS = (
    ("key", key_selector),
    ("string", string_selector),
    ("number", number_selector),
    ("constant", constant_selector),
    ("mapping", mapping_selector),
    ("sequence", sequence_selector),
)

# Which palette variable a kind's color comes from. Delimiters read their own
# variable so a scheme can mute them without touching the content colors.
COLOR_VARIABLE = {
    "key": "json_level",
    "string": "json_level",
    "number": "json_level",
    "constant": "json_level",
    "mapping": "json_bracket",
    "sequence": "json_bracket",
}


def palette_index(level, palette_size=PALETTE_SIZE):
    """1-based palette slot for `level`, cycling every `palette_size` levels."""
    return (level - 1) % palette_size + 1


def all_rules(max_level=MAX_LEVEL, palette_size=PALETTE_SIZE):
    """Yield (kind, level, palette_index, selector) for every rule, in order."""
    for kind, build in BUILDERS:
        for level in range(1, max_level + 1):
            last = level == max_level
            yield (kind, level, palette_index(level, palette_size),
                   build(level, last))
