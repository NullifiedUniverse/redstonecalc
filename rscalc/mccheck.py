"""Check a datapack's commands against the game's own grammar.

    python3 -m rscalc.mccheck <pack dir> [--versions 1.20.3 26.2 ...]

`packlint` reads a pack the way its author would: it knows which commands exist
and which functions call which. What it cannot know is whether a command
*parses*, and that is the failure that has cost the most in this project. A
function is parsed when the pack loads, whole; one command in it that does not
parse gets the entire function rejected, and from inside the game that is
indistinguishable from a missing file. "Unknown function rscalc:build" was one
wrong word on line 20.

So this reads the Brigadier tree the game itself generates (`tools/mc_reports.py`
distils it into `vendor/mc/<version>.json.gz`) and walks each command through it
the way the game does: literals, then typed arguments, with redirects for
`execute ... run`. It carries real parsers for the argument types this pack
actually uses — selectors, coordinates, SNBT, block states with their property
tables, item components, registry ids — and skims the rest, saying which.

What that buys, concretely:

  * every closed vocabulary comes from the game, not from memory. `bossbar set
    <id> color` is a set of literal nodes in the tree, so `aqua` fails without
    anyone having to know it should.
  * mixing `^` and `~` in one position, a selector that can match many where
    one is required, an id that is not in the registry, a block property that
    does not exist: all parse-time errors in the game, all found here.
  * the same pack can be run through several versions' grammars, which turns
    "this needs 1.20.5" from a hand-kept table into a measurement.

What it does not do: run anything. It says the game will *accept* a command, not
that the command does what was meant. That gap is real and is stated wherever
this is quoted.
"""

from __future__ import annotations

import collections
import gzip
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
VENDOR = os.path.join(ROOT, "vendor", "mc")

#: Text-component colours: a different vocabulary from the boss-bar one, and
#: the one that was confused with it.
TEXT_COLOURS = {
    "black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple",
    "gold", "gray", "dark_gray", "blue", "green", "aqua", "red", "light_purple",
    "yellow", "white",
}

#: Selector options, and which of them may not be repeated. `tag` repeats
#: freely; `type` repeats only when negated, which is treated as repeatable
#: here rather than modelled.
SELECTOR_KEYS = {
    "x", "y", "z", "distance", "dx", "dy", "dz", "scores", "tag", "team", "name",
    "type", "predicate", "nbt", "level", "gamemode", "x_rotation", "y_rotation",
    "limit", "sort", "advancements",
}
SINGLE_USE = {"x", "y", "z", "distance", "dx", "dy", "dz", "scores", "level",
              "x_rotation", "y_rotation", "limit", "sort", "advancements"}
SORTS = {"nearest", "furthest", "random", "arbitrary"}

#: `/item replace` and friends.
SLOT = re.compile(
    r"^(container\.\d+|hotbar\.[0-8]|inventory\.\d+|enderchest\.\d+|"
    r"villager\.\d+|horse\.\d+|player\.crafting\.\d|"
    r"weapon(\.mainhand|\.offhand)?|armor\.(head|chest|legs|feet|body)|"
    r"horse\.(saddle|chest|armor)|saddle|contents|player\.cursor)$")

_NUM = re.compile(r"-?(?:\d+\.?\d*|\.\d+)")
_INT = re.compile(r"-?\d+")
_WORD = re.compile(r"[A-Za-z0-9_\-.+]+")
_RESLOC = re.compile(r"[a-z0-9_.\-]*:?[a-z0-9_./\-]*")
_MACRO = re.compile(r"\$\(([A-Za-z0-9_]+)\)")


class Fail(Exception):
    """A parser refusing its input; carries where and why."""

    def __init__(self, pos, msg):
        super().__init__(msg)
        self.pos, self.msg = pos, msg


# ---------------------------------------------------------------------------
# SNBT — also covers JSON, which is a subset of it as far as a reader cares
# ---------------------------------------------------------------------------

def _ws(s, i):
    while i < len(s) and s[i] == " ":
        i += 1
    return i


def _quoted(s, i):
    q = s[i]
    j = i + 1
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == q:
            return j + 1
        j += 1
    raise Fail(i, "unterminated string")


#: A numeric literal, as the game reads one. An unquoted token that *starts
#: like* a number but is not one (`01b`, `1..2`) is an error, not a string.
_NUMLIT = re.compile(
    r"[+-]?(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|"
    r"(?:0|[1-9][\d_]*)(?:\.[\d_]*)?(?:[eE][+-]?\d+)?|"
    r"\.\d[\d_]*(?:[eE][+-]?\d+)?)[bBsSlLfFdD]?")
_BARE = re.compile(r"[0-9A-Za-z_\-.+]+")


def snbt(s, i, keys=None):
    """Read one SNBT/JSON value starting at ``i``; return the index after it.

    If ``keys`` is a list, the top-level compound's keys are appended to it —
    which is how a text component is told from a compound that is merely
    shaped like one.
    """
    if i >= len(s):
        raise Fail(i, "expected a value")
    c = s[i]
    if c in "\"'":
        return _quoted(s, i)
    if c == "{":
        i = _ws(s, i + 1)
        if i < len(s) and s[i] == "}":
            return i + 1
        while True:
            i = _ws(s, i)
            if i >= len(s):
                raise Fail(i, "unterminated compound")
            if s[i] in "\"'":
                j = _quoted(s, i)
                key = s[i + 1: j - 1]
                i = j
            else:
                m = _BARE.match(s, i)
                if not m:
                    raise Fail(i, f"expected a key, found {s[i]!r}")
                key = m.group()
                i = m.end()
            if keys is not None:
                keys.append(key)
            i = _ws(s, i)
            if i >= len(s) or s[i] != ":":
                raise Fail(i, "expected ':' after a key")
            i = snbt(s, _ws(s, i + 1))
            i = _ws(s, i)
            if i < len(s) and s[i] == ",":
                i += 1
                continue
            if i < len(s) and s[i] == "}":
                return i + 1
            raise Fail(i, "expected ',' or '}'")
    if c == "[":
        i += 1
        if s[i:i + 2] in ("B;", "I;", "L;"):
            i += 2
        i = _ws(s, i)
        if i < len(s) and s[i] == "]":
            return i + 1
        while True:
            i = snbt(s, _ws(s, i))
            i = _ws(s, i)
            if i < len(s) and s[i] == ",":
                i += 1
                continue
            if i < len(s) and s[i] == "]":
                return i + 1
            raise Fail(i, "expected ',' or ']'")
    m = _BARE.match(s, i)
    if not m:
        raise Fail(i, f"unexpected {c!r} in a value")
    tok = m.group()
    if tok[0] in "0123456789+-." and not _NUMLIT.fullmatch(tok):
        raise Fail(i, f"{tok!r} looks like a number but is not one")
    return m.end()


def _json_colours(text):
    """Text colours named in a component, so `aqua` and friends can be checked
    where they belong. Anything that is not JSON-shaped is left alone."""
    out = []
    for m in re.finditer(r'["\']?color["\']?\s*:\s*["\']([^"\']*)["\']', text):
        out.append(m.group(1))
    return out


# ---------------------------------------------------------------------------
# the checker
# ---------------------------------------------------------------------------

class Grammar:
    def __init__(self, data):
        self.version = data["version"]
        self.tree = data["commands"]
        self.regs = {k: set(v) for k, v in data["registries"].items()}
        self.blocks = data["blocks"]
        self.tags = data.get("tags", {})
        self._info = data.get("info", {})
        self.skimmed = collections.Counter()
        #: things the game *accepts* but that cannot do what was meant; reset
        #: on every `check`. A dead sound id is the canonical case.
        self.warnings = []
        #: what each `$(name)` is replaced with. The default, `1`, is legal
        #: wherever a number, a word or a time is — but NOT everywhere: the game
        #: validates component values at parse time, so a profile's `id` fed a
        #: bare 1 is "Malformed", where the int array it really receives is not.
        #: Set the real value for any macro that lands in a typed component.
        self.macros = {}
        self._state_memo = {}
        self._best = (-1, [])

    # -- public ------------------------------------------------------------
    def check(self, line):
        """``None`` if the game would accept this command, else why not.

        ``line`` is one line of a function file: a leading ``$`` marks a macro
        line, whose ``$(name)`` placeholders are replaced with a value that is
        legal wherever a number, a word or a time is.
        """
        s = line.strip()
        if not s or s.startswith("#"):
            return None
        if s.startswith("$"):
            if not _MACRO.search(s):
                # the game refuses this outright: a macro line with nothing to
                # substitute is "Can't parse function line", not a plain command
                return "a `$` macro line must contain at least one $(...)"
            s = _MACRO.sub(lambda m: str(self.macros.get(m.group(1), "1")), s[1:])
        elif _MACRO.search(s):
            return "a `$(...)` placeholder on a line that does not start with `$`"
        self._best = (-1, [])
        self.warnings = []
        try:
            if self._kids(s, 0, self.tree.get("children", {})):
                return None
        except Fail as f:
            return f"at {f.pos}: {f.msg}: {self._ctx(s, f.pos)}"
        except (AttributeError, IndexError, KeyError, ValueError,
                RecursionError) as e:
            # A checker that crashes on bad input is worse than one that is
            # wrong: it stops the build for the wrong reason, or is skipped.
            # Refuse loudly and name it, so the gap is found and fixed.
            return f"internal error in the checker ({type(e).__name__}: {e})"
        pos, want = self._best
        return f"at {pos}: {self._ctx(s, pos)} — expected {self._fmt(want)}"

    # -- tree walking ---------------------------------------------------------
    def _kids(self, s, pos, kids):
        for name, node in kids.items():
            try:
                end = self._match(s, pos, name, node)
            except Fail as f:
                self._note(f.pos, [f.msg])
                continue
            if end is None:
                continue
            if self._after(s, end, node):
                return True
        self._note(pos, [n if kids[n]["type"] == "literal" else f"<{n}>"
                         for n in kids])
        return False

    def _after(self, s, pos, node):
        if pos == len(s):
            if node.get("executable"):
                return True
            self._note(pos, ["more arguments"] + list(self._children(node))[:6])
            return False
        if s[pos] != " ":
            self._note(pos, ["a space"])
            return False
        kids = self._children(node)
        if not kids:
            self._note(pos + 1, ["end of command"])
            return False
        return self._kids(s, pos + 1, kids)

    def _children(self, node):
        if "redirect" in node:
            tgt = self.tree
            for k in node["redirect"]:
                tgt = tgt["children"][k]
            return tgt.get("children", {})
        kids = node.get("children", {})
        # A redirect back to the ROOT is serialised by leaving `redirect` out
        # altogether (the game writes it only when the path is non-empty), so a
        # node that has no children, is not executable and has no redirect is
        # exactly `execute ... run`: what follows is a whole new command. Miss
        # this and every `run` in the pack looks like a syntax error.
        if not kids and not node.get("executable"):
            return self.tree.get("children", {})
        return kids

    def _match(self, s, pos, name, node):
        if node["type"] == "literal":
            if s.startswith(name, pos):
                e = pos + len(name)
                if e == len(s) or s[e] == " ":
                    return e
            return None
        return self._arg(s, pos, node["parser"], node.get("properties", {}), name)

    def _note(self, pos, want):
        if pos > self._best[0]:
            self._best = (pos, list(want))
        elif pos == self._best[0]:
            self._best[1].extend(want)

    @staticmethod
    def _fmt(want):
        seen, out = set(), []
        for w in want:
            if w not in seen:
                seen.add(w)
                out.append(w)
        return " | ".join(out[:14]) + (" | ..." if len(out) > 14 else "")

    @staticmethod
    def _ctx(s, pos):
        return repr(s[max(0, pos - 12): pos + 24])

    # -- argument parsers -----------------------------------------------------
    def _token(self, s, pos):
        """One argument, bracket- and quote-aware, ending at a space."""
        depth, i = 0, pos
        while i < len(s):
            c = s[i]
            if c in "\"'":
                i = _quoted(s, i)
                continue
            if c in "[{(":
                depth += 1
            elif c in "]})":
                depth -= 1
            elif c == " " and depth <= 0:
                break
            i += 1
        if depth != 0:
            raise Fail(pos, "unbalanced brackets")
        return i

    def _close(self, s, pos):
        """Index just past the bracket that opens at ``pos``.

        Unlike `_token` this stops at the matching close bracket rather than at
        the next space, which matters wherever more text follows it directly —
        `oak_wall_sign[facing=north]{front_text:...}` has NBT glued to the
        state, and reading to the next space swallowed it.
        """
        pairs = {"[": "]", "{": "}", "(": ")"}
        stack, i = [], pos
        while i < len(s):
            c = s[i]
            if c in "\"'":
                i = _quoted(s, i)
                continue
            if c in pairs:
                stack.append(pairs[c])
            elif c in "]})":
                if not stack or stack.pop() != c:
                    raise Fail(i, f"unexpected {c!r}")
                if not stack:
                    return i + 1
            i += 1
        raise Fail(pos, "unbalanced brackets")

    def _arg(self, s, pos, parser, props, name=None):
        p = parser
        rest = s[pos:]
        # an argument cannot begin at a space: `a  b` (two spaces) is an error
        # in the game, and every parser below may assume its first character is
        # part of the argument
        if not rest or rest[0] == " ":
            return None

        if p == "brigadier:bool":
            m = re.match(r"(true|false)(?= |$)", rest)
            return pos + m.end() if m else None
        if p == "brigadier:integer":
            m = _INT.match(rest)
            if not m or not self._end(rest, m.end()):
                raise Fail(pos, "expected an integer")
            self._bounds(int(m.group()), props, pos)
            return pos + m.end()
        if p in ("brigadier:double", "brigadier:float"):
            m = _NUM.match(rest)
            if not m or not self._end(rest, m.end()):
                raise Fail(pos, "expected a number (no d/f suffix outside NBT)")
            self._bounds(float(m.group()), props, pos)
            return pos + m.end()
        if p == "brigadier:string":
            kind = props.get("type", "word")
            if kind == "greedy":
                return len(s)
            if rest[0] in "\"'":
                if kind != "phrase":
                    raise Fail(pos, "a single word cannot be quoted")
                return _quoted(s, pos)
            m = _WORD.match(rest)
            if not m or not self._end(rest, m.end()):
                raise Fail(pos, "expected a word")
            return pos + m.end()

        if p == "minecraft:entity" or p == "minecraft:game_profile":
            return self._selector(
                s, pos, props,
                p == "minecraft:game_profile" or props.get("type") == "players")
        if p == "minecraft:score_holder":
            if rest[0] == "@":
                return self._selector(s, pos, props, False)
            return pos + self._word_or_fail(rest, pos, "a score holder")
        if p in ("minecraft:objective", "minecraft:team",
                 "minecraft:scoreboard_slot"):
            m = _WORD.match(rest)
            if not m:
                raise Fail(pos, "expected a name")
            return pos + m.end()
        if p == "minecraft:objective_criteria":
            return self._criteria(s, pos)
        if p == "minecraft:operation":
            m = re.match(r"(><|[+\-*/%]?=|<|>)(?= |$)", rest)
            if not m:
                raise Fail(pos, "expected one of = += -= *= /= %= < > ><")
            return pos + m.end()
        if p == "minecraft:entity_anchor":
            m = re.match(r"(feet|eyes)(?= |$)", rest)
            if not m:
                raise Fail(pos, "expected feet or eyes")
            return pos + m.end()
        if p == "minecraft:swizzle":
            m = re.match(r"[xyz]+(?= |$)", rest)
            if not m or len(set(m.group())) != len(m.group()):
                raise Fail(pos, "expected distinct axes from x y z")
            return pos + m.end()
        if p == "minecraft:gamemode":
            m = re.match(r"(survival|creative|adventure|spectator)(?= |$)", rest)
            if not m:
                raise Fail(pos, "expected a game mode")
            return pos + m.end()

        if p in ("minecraft:block_pos", "minecraft:vec3"):
            return self._coords(s, pos, 3, p == "minecraft:block_pos")
        if p == "minecraft:column_pos":
            return self._coords(s, pos, 2, True)
        if p in ("minecraft:vec2", "minecraft:rotation"):
            return self._coords(s, pos, 2, False, local=False)

        if p == "minecraft:time":
            m = re.match(r"(-?(?:\d+\.?\d*|\.\d+))[dst]?(?= |$)", rest)
            if not m:
                raise Fail(pos, "expected a time such as 20t")
            if float(m.group(1)) < 0:
                raise Fail(pos, "a time cannot be negative")
            return pos + m.end()
        if p in ("minecraft:int_range", "minecraft:float_range"):
            return pos + self._range(rest, pos, p == "minecraft:float_range")

        if p in ("minecraft:resource_location", "minecraft:function",
                 "minecraft:loot_table", "minecraft:loot_predicate",
                 "minecraft:loot_modifier", "minecraft:dimension",
                 "minecraft:resource_key"):
            hashed = p == "minecraft:function" and rest[0] == "#"
            body = rest[1:] if hashed else rest
            m = _RESLOC.match(body)
            end = (1 if hashed else 0) + m.end()
            if end == 0 or not self._end(rest, end):
                raise Fail(pos, "expected a lowercase resource location")
            if name == "sound" and "minecraft:sound_event" in self.regs:
                ident = rest[:end]
                ident = ident if ":" in ident else f"minecraft:{ident}"
                if ident not in self.regs["minecraft:sound_event"]:
                    # NOT a parse error: the game accepts any sound id and
                    # simply plays nothing for one that does not exist. That
                    # makes it worse, not better — the function loads, the
                    # build runs, and the knock is just absent.
                    self.warnings.append(f"{ident} is not a sound, so this "
                                         f"will play silently")
            return pos + end
        if p in ("minecraft:resource", "minecraft:resource_or_tag",
                 "minecraft:resource_or_tag_key", "minecraft:mob_effect",
                 "minecraft:entity_summon", "minecraft:item_enchantment"):
            return self._resource(s, pos, p, props)
        if p == "minecraft:particle":
            return self._particle(s, pos)

        if p in ("minecraft:block_state", "minecraft:block_predicate"):
            return self._block(s, pos, p == "minecraft:block_predicate")
        if p in ("minecraft:item_stack", "minecraft:item_predicate"):
            return self._item(s, pos)
        if p in ("minecraft:item_slot", "minecraft:item_slots"):
            m = re.match(r"[a-z0-9_.]+(?= |$)", rest)
            if not m or not SLOT.match(m.group()):
                raise Fail(pos, "not an inventory slot name")
            return pos + m.end()

        if p == "minecraft:nbt_compound_tag":
            if rest[0] != "{":
                raise Fail(pos, "expected a compound '{...}'")
            return snbt(s, pos)
        if p == "minecraft:nbt_tag":
            return snbt(s, pos)
        if p == "minecraft:nbt_path":
            return self._nbt_path(s, pos)
        if p == "minecraft:component":
            end = snbt(s, pos)
            self._component(s[pos:end], pos)
            return end
        if p == "minecraft:message":
            return len(s)
        if p == "minecraft:uuid":
            m = re.match(r"[0-9a-fA-F\-]{1,36}(?= |$)", rest)
            if not m:
                raise Fail(pos, "expected a uuid")
            return pos + m.end()

        # not modelled: take one argument and say so
        self.skimmed[p] += 1
        return self._token(s, pos) if rest[0] != " " else None

    def _nbt_path(self, s, pos):
        """`Pos[0]`, `Items[{Slot:0b}].id`, `{a:1}.b`, `"odd key".x`.

        A path is a chain of keys, `[index]`, `[]`, `[{filter}]` and `{filter}`,
        joined by dots. It used to be accepted as "any token", which is how a
        stray bracket in `Pos[0` would have reached a player.
        """
        i, n = pos, len(s)
        bare = re.compile(r"[^\s\"'\[\]\.{}]+")
        first = True
        while True:
            c = s[i] if i < n else ""
            if c == "{" and first:
                i = snbt(s, i)
            elif c == "[" and first:
                pass                              # `[]`, `[0]`, `[{...}]` alone
            elif c in "\"'" and c:
                i = _quoted(s, i)
            elif c and bare.match(s, i):
                i = bare.match(s, i).end()
                if i < n and s[i] == "{":          # `Items{Slot:0b}`
                    i = snbt(s, i)
            else:
                raise Fail(i, "expected a key in an NBT path")
            first = False
            while i < n and s[i] == "[":
                j = i + 1
                if j < n and s[j] == "]":
                    i = j + 1
                elif j < n and s[j] == "{":
                    i = snbt(s, j)
                    if i >= n or s[i] != "]":
                        raise Fail(i, "expected ']'")
                    i += 1
                else:
                    m = _INT.match(s, j)
                    if not m or m.end() >= n or s[m.end()] != "]":
                        raise Fail(j, "expected an index, {filter} or ']'")
                    i = m.end() + 1
            if i < n and s[i] == ".":
                i += 1
                continue
            if i < n and s[i] not in " ":
                raise Fail(i, f"unexpected {s[i]!r} in an NBT path")
            return i

    #: keys that make a compound a text component at all
    _CONTENT = {"text", "translate", "score", "selector", "keybind", "nbt",
                "type", "object", "extra"}

    def _component(self, body, pos):
        """A text component is a string, a list, or an object that says what it
        contains. `-1`, `0` and `{}` are all refused by the game, which is the
        sort of thing a mistyped macro produces."""
        head = body[:1]
        if head == "[":
            if re.fullmatch(r"\[\s*\]", body):
                raise Fail(pos, "an empty list is not a component")
        elif head in "\"'":
            pass
        elif head == "{":
            keys = []
            snbt(body, 0, keys)
            if not set(keys) & self._CONTENT:
                raise Fail(pos, "a component object needs text, translate, "
                                "score, selector, keybind or nbt")
        elif re.fullmatch(r"-?[\d.]+[bBsSlLfFdD]?|true|false", body):
            raise Fail(pos, f"{body!r} is a number or boolean, not a component")
        for col in _json_colours(body):
            if col not in TEXT_COLOURS and not re.fullmatch(r"#[0-9a-fA-F]{6}", col):
                raise Fail(pos, f"{col!r} is not a text colour")

    # -- small helpers ----------------------------------------------------------
    @staticmethod
    def _name_or_uuid(rest, pos):
        """A player name or a UUID: what an entity argument is when it is not a
        selector. `kill ~` and `clear rscalc:tick` both *look* like commands but
        are a name the game cannot read — 1 to 16 characters from a small set,
        or a UUID — and were accepted here until the game said otherwise."""
        m = re.match(r'"(?:[^"\\]|\\.)*"|[A-Za-z0-9_\-.+]+', rest)
        if not m:
            raise Fail(pos, "invalid name or UUID")
        tok = m.group()
        if not tok.startswith('"') and len(tok) > 16 and not re.fullmatch(
                r"[0-9a-fA-F]{1,8}(-[0-9a-fA-F]{1,4}){3}-[0-9a-fA-F]{1,12}", tok):
            raise Fail(pos, "invalid name or UUID (a name is at most 16 characters)")
        if m.end() != len(rest) and rest[m.end()] != " ":
            raise Fail(pos + m.end(), "expected whitespace to end one argument")
        return m.end()

    @staticmethod
    def _end(rest, n):
        return n == len(rest) or rest[n] == " "

    def _word_or_fail(self, rest, pos, what):
        m = re.match(r"[^\s@]\S*", rest)
        if not m:
            raise Fail(pos, f"expected {what}")
        return m.end()

    @staticmethod
    def _bounds(v, props, pos):
        lo, hi = props.get("min"), props.get("max")
        if lo is not None and v < lo:
            raise Fail(pos, f"{v} is below the minimum {lo}")
        if hi is not None and v > hi:
            raise Fail(pos, f"{v} is above the maximum {hi}")

    @staticmethod
    def _range(rest, pos, floaty):
        m = re.match(r"[^\s]+", rest)
        tok = m.group()
        num = r"-?(?:\d+(?:\.\d*)?|\.\d+)" if floaty else r"-?\d+"
        if not re.fullmatch(rf"(?:{num})|(?:{num})?\.\.(?:{num})?", tok) \
                or tok == "..":
            raise Fail(pos, f"{tok!r} is not a range")
        return m.end()

    def _coords(self, s, pos, n, ints, local=True):
        """`~1 ~ ~-2`, `^ ^ ^0.5`, `1 2 3` — with the rules the game enforces:
        local and world coordinates cannot be mixed, and an absolute block
        position is a whole number."""
        i, kinds = pos, []
        for k in range(n):
            if k:
                if i >= len(s) or s[i] != " ":
                    raise Fail(i, f"expected {n} coordinates, found {k}")
                i += 1
            c = s[i] if i < len(s) else ""
            rel = c in "~^"
            if c == "^":
                if not local:
                    raise Fail(i, "local coordinates are not allowed here")
                kinds.append("^")
            else:
                kinds.append("~" if rel else "n")
            j = i + 1 if rel else i
            m = (_INT if (ints and not rel) else _NUM).match(s, j)
            if m and self._end(s[i:], m.end() - i):
                i = m.end()
            elif j < len(s) and s[j] != " ":
                raise Fail(i, "not a coordinate")
            else:
                if not rel:
                    raise Fail(i, "expected a coordinate")
                i = j
        if "^" in kinds and any(k != "^" for k in kinds):
            raise Fail(pos, "cannot mix ^ (local) and ~ or plain (world) "
                            "coordinates in one position")
        return i

    _SIMPLE_CRITERIA = {"dummy", "trigger", "deathCount", "playerKillCount",
                        "totalKillCount", "health", "food", "air", "armor",
                        "xp", "level"}
    _TEAM_COLOURS = TEXT_COLOURS | {"reset"}
    _STAT_REGISTRY = {"used": "minecraft:item", "mined": "minecraft:block",
                      "broken": "minecraft:item", "crafted": "minecraft:item",
                      "picked_up": "minecraft:item", "dropped": "minecraft:item",
                      "killed": "minecraft:entity_type",
                      "killed_by": "minecraft:entity_type",
                      "custom": "minecraft:custom_stat"}

    def _criteria(self, s, pos):
        """`dummy`, `health`, `teamkill.red`, or `stat.type:stat` — closed set.

        The game answers "Unknown criterion" for anything else, and a stat
        criterion names a real item, block, entity or custom stat: the pack's
        `minecraft.used:minecraft.carrot_on_a_stick` is only as good as that
        item existing.
        """
        m = re.match(r"[^\s]+", s[pos:])
        tok = m.group()
        if tok in self._SIMPLE_CRITERIA:
            return pos + m.end()
        for prefix in ("teamkill.", "killedByTeam."):
            if tok.startswith(prefix) and tok[len(prefix):] in self._TEAM_COLOURS:
                return pos + m.end()
        if ":" in tok:
            kind, what = tok.split(":", 1)
            stat = kind.replace(".", ":", 1)               # minecraft.used
            types = self.regs.get("minecraft:stat_type")
            short = stat.split(":", 1)[-1]
            if (types is None or stat in types) and short in self._STAT_REGISTRY:
                ident = what.replace(".", ":", 1)
                reg = self._STAT_REGISTRY[short]
                if reg in self.regs and ident not in self.regs[reg]:
                    raise Fail(pos, f"unknown criterion: {ident} is not in {reg}")
                return pos + m.end()
        raise Fail(pos, f"unknown criterion {tok!r}")

    # -- selectors ----------------------------------------------------------------
    def _selector(self, s, pos, props, players_only):
        rest = s[pos:]
        single = props.get("amount") == "single"
        if rest[0] != "@":
            return pos + self._name_or_uuid(rest, pos)
        kind = rest[1:2]
        if kind not in ("a", "e", "p", "r", "s", "n") or (
                len(rest) > 2 and rest[2] not in "[ "):
            raise Fail(pos, f"@{kind} is not a selector")
        i = pos + 2
        opts = []
        if i < len(s) and s[i] == "[":
            end = self._close(s, i)
            opts = self._selector_options(s[i + 1: end - 1], i + 1)
            i = end
        limit = {"p": 1, "r": 1, "s": 1, "n": 1}.get(kind)
        seen_type_player = False
        for k, v, at in opts:
            if k == "limit":
                limit = int(v)
            if k == "type" and v.lstrip("!") == "player":
                seen_type_player = True
            if k == "type" and kind in ("a", "p"):
                raise Fail(pos + at, f"'type' is not applicable to @{kind}")
        if single and (limit is None or limit > 1):
            raise Fail(pos, "this argument takes ONE entity but this selector "
                            "can match many — add limit=1")
        if players_only and kind in ("e", "n") and not seen_type_player:
            raise Fail(pos, f"this argument takes players only, and @{kind} is "
                            f"not restricted to type=player")
        return i

    def _selector_options(self, body, base):
        out, seen = [], set()
        depth, start = 0, 0
        parts = []
        i = 0
        while i <= len(body):
            if i == len(body) or (body[i] == "," and depth == 0):
                parts.append((body[start:i], start))
                start = i + 1
            elif body[i] in "\"'":
                i = _quoted(body, i) - 1
            elif body[i] in "[{":
                depth += 1
            elif body[i] in "]}":
                depth -= 1
            i += 1
        for part, off in parts:
            if not part.strip():
                if len(parts) == 1:
                    continue
                raise Fail(base + off, "empty selector option")
            if "=" not in part:
                raise Fail(base + off, f"{part!r} is not key=value")
            k, v = part.split("=", 1)
            # the game skips whitespace around keys and values inside `[...]`
            k, v = k.strip(), v.strip()
            if k not in SELECTOR_KEYS:
                raise Fail(base + off, f"{k!r} is not a selector option")
            if k in SINGLE_USE:
                if k in seen:
                    raise Fail(base + off, f"'{k}' given twice")
                seen.add(k)
            self._selector_value(k, v, base + off)
            out.append((k, v, off))
        return out

    def _selector_value(self, k, v, at):
        if k == "limit":
            if not _INT.fullmatch(v) or int(v) < 1:
                raise Fail(at, f"limit={v!r} must be a whole number of 1 or more")
        elif k == "sort":
            if v not in SORTS:
                raise Fail(at, f"sort={v!r} is not one of {', '.join(sorted(SORTS))}")
        elif k in ("distance", "level", "x_rotation", "y_rotation"):
            self._range(v, at, True)
        elif k in ("x", "y", "z", "dx", "dy", "dz"):
            if not _NUM.fullmatch(v):
                raise Fail(at, f"{k}={v!r} is not a number")
        elif k == "type":
            t = v.lstrip("!")
            if t.startswith("#"):
                name = t[1:]
                if not re.fullmatch(r"[a-z0-9_.\-]*:?[a-z0-9_./\-]+", name):
                    raise Fail(at, f"{t!r} is not a tag id")
                known = self.tags.get("entity_type")
                if known is not None and name.startswith("minecraft:") \
                        and name[len("minecraft:"):] not in known:
                    raise Fail(at, f"{t} is not an entity type tag")
                return
            # a bare `:marker` means the default namespace, as the game reads it
            ident = t if ":" in t and not t.startswith(":") else \
                f"minecraft:{t.lstrip(':')}"
            if "minecraft:entity_type" in self.regs and \
                    ident not in self.regs["minecraft:entity_type"]:
                raise Fail(at, f"type={t!r} is not an entity type")
        elif k in ("scores", "advancements"):
            if not (v.startswith("{") and v.endswith("}")):
                raise Fail(at, f"{k} needs {{...}}")
            if k == "scores":
                for pair in filter(None, v[1:-1].split(",")):
                    if "=" not in pair:
                        raise Fail(at, f"score {pair!r} is not name=range")
                    name, rng = pair.split("=", 1)
                    if not re.fullmatch(r"[A-Za-z0-9_\-.+]+", name):
                        raise Fail(at, f"{name!r} is not an objective name")
                    self._range(rng, at, False)
        elif k == "nbt":
            snbt(v, 0)
        elif k == "gamemode":
            if v.lstrip("!") not in ("survival", "creative", "adventure", "spectator"):
                raise Fail(at, f"gamemode={v!r} is not a game mode")
        elif k in ("tag", "team", "name"):
            # an unquoted string: letters, digits and _ - . + only. `tag=a~b`
            # is "Expected end of options", and the pack builds its tags from
            # a namespace so a stray character would be a build bug
            if not re.fullmatch(r"!?(?:[A-Za-z0-9_\-.+]*|\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", v):
                raise Fail(at, f"{k}={v!r} is not a name")
        elif k == "predicate":
            if not re.fullmatch(r"!?[a-z0-9_.\-]*:?[a-z0-9_./\-]+", v):
                raise Fail(at, f"predicate={v!r} is not a resource location")

    # -- registries, blocks, items ------------------------------------------------
    def _resource(self, s, pos, parser, props):
        rest = s[pos:]
        hashed = rest[0] == "#"
        if hashed and "tag" not in parser and "selector" not in parser:
            raise Fail(pos, "a tag is not allowed here, only a plain id")
        m = _RESLOC.match(rest[1:] if hashed else rest)
        end = (1 if hashed else 0) + m.end()
        if end == 0 or not self._end(rest, end):
            raise Fail(pos, "expected a resource location")
        if hashed:
            return pos + end
        ident = rest[:end]
        ident = ident if ":" in ident else f"minecraft:{ident}"
        reg = props.get("registry") or {
            "minecraft:mob_effect": "minecraft:mob_effect",
            "minecraft:entity_summon": "minecraft:entity_type",
            "minecraft:item_enchantment": "minecraft:enchantment",
        }.get(parser)
        if reg and reg in self.regs and ident not in self.regs[reg]:
            raise Fail(pos, f"{ident} is not in {reg}")
        return pos + end

    def _particle(self, s, pos):
        m = _RESLOC.match(s[pos:])
        ident = m.group()
        full = ident if ":" in ident else f"minecraft:{ident}"
        reg = self.regs.get("minecraft:particle_type")
        if reg is not None and full not in reg:
            raise Fail(pos, f"{full} is not a particle")
        if full in ("minecraft:dust", "minecraft:dust_color_transition",
                    "minecraft:entity_effect", "minecraft:block",
                    "minecraft:falling_dust", "minecraft:item",
                    "minecraft:vibration", "minecraft:sculk_charge",
                    "minecraft:shriek", "minecraft:trail",
                    "minecraft:block_marker", "minecraft:dragon_breath"):
            self.skimmed[f"particle options for {full}"] += 1
        return pos + m.end()

    def _block(self, s, pos, predicate):
        rest = s[pos:]
        if predicate and rest[0] == "#":
            m = _RESLOC.match(rest[1:])
            return pos + 1 + m.end()
        m = _RESLOC.match(rest)
        ident = m.group()
        if not ident:
            raise Fail(pos, "expected a block id")
        full = ident if ":" in ident else f"minecraft:{ident}"
        if full not in self.blocks:
            raise Fail(pos, f"{full} is not a block")
        i = pos + m.end()
        if i < len(s) and s[i] == "[":
            end = self._close(s, i)
            body = s[i + 1: end - 1]
            key = (full, body)
            if key not in self._state_memo:
                self._state_memo[key] = self._props(full, body, i)
            if self._state_memo[key]:
                raise Fail(i, self._state_memo[key])
            i = end
        if i < len(s) and s[i] == "{":
            i = snbt(s, i)
        if i < len(s) and s[i] != " ":
            raise Fail(i, "unexpected text after a block")
        return i

    def _props(self, block, body, at):
        legal = self.blocks[block]
        seen = set()
        for pair in filter(None, body.split(",")):
            if "=" not in pair:
                return f"{pair!r} is not property=value"
            k, v = (x.strip() for x in pair.split("=", 1))
            if k not in legal:
                return (f"{block} has no property {k!r} "
                        f"(it has: {', '.join(sorted(legal)) or 'none'})")
            if k in seen:
                return f"property {k!r} given twice"
            seen.add(k)
            if v not in legal[k]:
                return (f"{block}[{k}] cannot be {v!r} "
                        f"(one of: {', '.join(legal[k])})")
        return None

    def _item(self, s, pos):
        m = _RESLOC.match(s[pos:])
        ident = m.group()
        full = ident if ":" in ident else f"minecraft:{ident}"
        items = self.regs.get("minecraft:item")
        if items is not None and full not in items:
            raise Fail(pos, f"{full} is not an item")
        i = pos + m.end()
        if i < len(s) and s[i] == "[":
            comps = self.regs.get("minecraft:data_component_type")
            if comps is None:
                raise Fail(i, "item components do not exist in this version "
                              "(they arrived in 1.20.5)")
            end = self._close(s, i)
            # entries are `name=value` at depth 0; the name is checked
            depth, start, body = 0, i + 1, s[i + 1: end - 1]
            j = 0
            while j <= len(body):
                if j == len(body) or (body[j] == "," and depth == 0):
                    piece = body[start - i - 1: j]
                    name = piece.split("=", 1)[0].strip().lstrip("!")
                    if name:
                        full_c = name if ":" in name else f"minecraft:{name}"
                        if full_c not in comps:
                            raise Fail(i, f"{full_c} is not an item component")
                    start = i + 1 + j + 1
                elif body[j] in "\"'":
                    j = _quoted(body, j) - 1
                elif body[j] in "[{":
                    depth += 1
                elif body[j] in "]}":
                    depth -= 1
                j += 1
            i = end
        if i < len(s) and s[i] == "{":
            i = snbt(s, i)
        return i


# ---------------------------------------------------------------------------
# loading, and checking whole packs
# ---------------------------------------------------------------------------

_CACHE = {}


def versions():
    """Versions that have distilled grammar on disk, oldest first."""
    def key(v):
        return tuple(int(p) for p in re.findall(r"\d+", v))
    return sorted((f[:-8] for f in os.listdir(VENDOR) if f.endswith(".json.gz")),
                  key=key)


def load(version):
    if version not in _CACHE:
        path = os.path.join(VENDOR, f"{version}.json.gz")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"no grammar for {version}: run tools/mc_reports.py {version}")
        with gzip.open(path, "rt", encoding="utf-8") as f:
            _CACHE[version] = Grammar(json.load(f))
    return _CACHE[version]


_COORD = re.compile(r"(?<=[ ~^])-?\d+(?:\.\d+)?(?=[ ]|$)")


def _shape(line):
    """A line with its plain numbers zeroed, so the 70,000 near-identical
    `fill`s in the block batches are checked as the ~30 distinct commands they
    are. Numbers inside brackets, selectors and strings are left alone."""
    return _COORD.sub("0", line)


def problems(root, version, dedupe=True):
    """Every command in the pack the game of ``version`` would refuse.

    Returns ``[(function id, line number, command, why), ...]``.
    """
    g = load(version)
    out, memo = [], {}
    fdir = os.path.join(root, "data")
    for ns in sorted(os.listdir(fdir)):
        base = os.path.join(fdir, ns, "function")
        if not os.path.isdir(base):
            continue
        for dp, _, files in os.walk(base):
            for f in sorted(files):
                if not f.endswith(".mcfunction"):
                    continue
                rel = os.path.relpath(os.path.join(dp, f), base)[:-11]
                fid = f"{ns}:{rel.replace(os.sep, '/')}"
                with open(os.path.join(dp, f), encoding="utf-8") as fh:
                    for n, line in enumerate(fh, 1):
                        s = line.strip()
                        if not s or s.startswith("#"):
                            continue
                        key = _shape(s) if dedupe else s
                        if key not in memo:
                            memo[key] = g.check(s)
                        if memo[key]:
                            out.append((fid, n, s[:90], memo[key]))
    return out


#: Which registry each tag directory's plain ids must come from.
_TAG_REGISTRY = {"block": "minecraft:block", "item": "minecraft:item",
                 "entity_type": "minecraft:entity_type",
                 "fluid": "minecraft:fluid", "game_event": "minecraft:game_event",
                 "damage_type": "minecraft:damage_type"}


def tag_problems(root, version):
    """Tag files whose *required* entries do not exist in ``version``.

    A tag naming an id the version does not have is not skipped: **the whole tag
    fails to load**, which for `#rscalc:passable` reads as a grappling hook that
    does nothing. So an entry that may be absent has to say so with
    `"required": false`, and this checks that every entry that does not say so
    is real in every version the pack claims.

    Returns ``[(file, entry, why), ...]``.
    """
    g = load(version)
    out = []
    data = os.path.join(root, "data")
    if not os.path.isdir(data):
        return out
    have = set()                                        # tags this pack defines
    for ns in os.listdir(data):
        tags = os.path.join(data, ns, "tags")
        for kind in os.listdir(tags) if os.path.isdir(tags) else ():
            for dp, _, files in os.walk(os.path.join(tags, kind)):
                for f in files:
                    rel = os.path.relpath(os.path.join(dp, f), os.path.join(tags, kind))
                    have.add((kind, f"{ns}:{rel[:-5]}".replace(os.sep, "/")))
    for ns in sorted(os.listdir(data)):
        tags = os.path.join(data, ns, "tags")
        for kind in sorted(os.listdir(tags)) if os.path.isdir(tags) else ():
            reg = _TAG_REGISTRY.get(kind)
            for dp, _, files in os.walk(os.path.join(tags, kind)):
                for f in sorted(files):
                    if not f.endswith(".json"):
                        continue
                    path = os.path.join(dp, f)
                    rel = os.path.relpath(path, data).replace(os.sep, "/")
                    try:
                        values = json.load(open(path, encoding="utf-8"))["values"]
                    except Exception as e:                    # noqa: BLE001
                        out.append((rel, "", f"unreadable: {e}"))
                        continue
                    for v in values:
                        ident = v["id"] if isinstance(v, dict) else v
                        required = v.get("required", True) if isinstance(v, dict) else True
                        if ident.startswith("#"):
                            ref = ident[1:]
                            if ref.startswith("minecraft:"):
                                known = g.tags.get(kind)
                                if known is not None and ref[10:] not in known:
                                    out.append((rel, ident, "no such vanilla tag"))
                            elif (kind, ref) not in have and required:
                                out.append((rel, ident, "a tag this pack does not define"))
                        elif reg and reg in g.regs and required:
                            full = ident if ":" in ident else f"minecraft:{ident}"
                            if full not in g.regs[reg]:
                                out.append((rel, ident,
                                            f"required, but not in {reg} on {version}"))
    return out


def format_of(version):
    """The data-pack format the game itself reports for ``version``."""
    pv = load(version)._info["pack_version"]
    return pv.get("data_major", pv.get("data"))


def mcmeta_compatible(meta, version):
    """Would the game call a pack with this `pack` section compatible with
    ``version``? Compatible means loading quietly; anything else is the
    "made for an older version" warning a player reads as "this is broken".

    The game compares its own data-pack format with the pack's declared range,
    and a pack that declares only `pack_format` claims exactly that one format.
    """
    sup = meta.get("supported_formats")
    lo = (sup or {}).get("min_inclusive", meta.get("min_format",
                                                   meta.get("pack_format")))
    hi = (sup or {}).get("max_inclusive", meta.get("max_format",
                                                   meta.get("pack_format")))
    cur = format_of(version)
    # a malformed range (the `[major, minor]` arrays this project once wrote)
    # is simply not compatible; it is `packlint.mcmeta_problems` that says why
    if not (isinstance(lo, int) and isinstance(hi, int)):
        return False
    return lo <= cur <= hi


def _key(v):
    return tuple(int(p) for p in re.findall(r"\d+", v))


def floors(root, optional=(), only=None):
    """Which versions load this pack — measured, not asserted.

    ``optional`` lists function ids that may fail without the pack being
    considered broken (the pet's skin, which degrades to a plain head on a game
    too old for item components). Returns::

        {"per_version": {version: [refused commands + refused tag entries]},
         "core":  oldest version from which every *required* part loads,
         "full":  oldest version from which everything does,
         "newest": newest version checked and clean}

    "From which" means every later version is also clean: a version that loads
    the pack in between two that do not is not a floor.
    """
    vs = sorted(only or versions(), key=_key)
    skip = set(optional)
    per = {}
    for v in vs:
        found = [(fid, n, cmd, why) for fid, n, cmd, why in problems(root, v)]
        found += [(rel, 0, ent, why) for rel, ent, why in tag_problems(root, v)]
        per[v] = found

    def floor(ignore):
        good = [not [p for p in per[v] if p[0] not in ignore] for v in vs]
        first = None
        for i in range(len(vs) - 1, -1, -1):
            if not good[i]:
                break
            first = vs[i]
        return first

    clean = [v for v in vs if not per[v]]
    return {"per_version": per, "core": floor(skip), "full": floor(set()),
            "newest": clean[-1] if clean else None}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="check a pack against the game's grammar")
    ap.add_argument("pack")
    ap.add_argument("--versions", nargs="*", default=None)
    args = ap.parse_args(argv)
    vs = args.versions or versions()
    bad = 0
    for v in vs:
        found = problems(args.pack, v)
        by_fn = collections.Counter(f for f, *_ in found)
        print(f"{v:8s} {'OK' if not found else str(len(found)) + ' commands refused'}"
              + (f" in {len(by_fn)} functions" if found else ""))
        for fid, n, cmd, why in found[:8]:
            print(f"    {fid}:{n}  {why}\n        {cmd}")
        bad += bool(found)
        g = load(v)
        if g.skimmed:
            print(f"    (skimmed, not parsed: {', '.join(sorted(g.skimmed))})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
