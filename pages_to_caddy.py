#!/usr/bin/env python3
"""pages-to-caddy: translate Cloudflare Pages and Netlify ``_headers`` and
``_redirects`` files into Caddyfile directives.

Any rule that cannot be reproduced faithfully in Caddy is refused, with the
file and line named, instead of being dropped or approximated. A refusal is a
problem you can see and fix before you deploy; a security header or a redirect
that quietly goes missing in the move is not.

Standard library only. Python 3.10 or later.

    python3 pages_to_caddy.py public/ -o pages-rules.caddy
    python3 pages_to_caddy.py --check public/
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import os
import re
import sys
import tempfile
from urllib.parse import unquote

__version__ = "0.1.0"

CLOUDFLARE = "cloudflare"
NETLIFY = "netlify"
PLATFORMS = (CLOUDFLARE, NETLIFY)
PLATFORM_NAMES = {CLOUDFLARE: "Cloudflare Pages", NETLIFY: "Netlify"}

# The status a redirect gets when it names none, as each platform documents it.
DEFAULT_STATUS = {CLOUDFLARE: 302, NETLIFY: 301}

# Redirect statuses translated for each platform. Cloudflare Pages documents
# 301, 302, 303, 307 and 308. Netlify documents 307 as unsupported, and documents
# passing the request's query string on only for 301 and 302 (and 200).
REDIRECT_STATUSES = {CLOUDFLARE: (301, 302, 303, 307, 308), NETLIFY: (301, 302)}

# Cloudflare Pages' documented limits.
CF_MAX_HEADER_RULES = 100
CF_MAX_HEADER_LINE = 2000
CF_MAX_STATIC_REDIRECTS = 2000
CF_MAX_DYNAMIC_REDIRECTS = 100
CF_MAX_REDIRECT_LINE = 1000

PLACEHOLDER = re.compile(r":([A-Za-z][A-Za-z0-9_]*)")
HEADER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$%&'+.^_|~-]*")
HOST_SOURCE = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/.*")
STATUS = re.compile(r"(\d+)(!?)")

# Refused in paths and destinations: quotes and backslashes change how a
# Caddyfile token is read, and braces start a Caddy placeholder (or an
# environment variable, which Caddy substitutes when it parses the file).
CADDY_UNSAFE = frozenset('"`\\{}')
REGEX_SPECIAL = frozenset(".^$*+?()[]{}|\\")
# Words the header directive reads as its own options, not as header names.
CADDY_HEADER_WORDS = frozenset(("defer", "match"))

# Characters used to build an example path when two patterns overlap.
_FILLERS = "xyzqjkwvu0123456789"


class Refused(Exception):
    """A rule that cannot be translated faithfully."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


@dataclasses.dataclass(frozen=True)
class Message:
    """One line of feedback about one line of a source file."""

    file: str
    line: int
    level: str  # "refused", "skipped" or "note"
    text: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.level}: {self.text}"


@dataclasses.dataclass
class Result:
    """What :func:`translate` produced.

    ``caddyfile`` is empty whenever any rule was refused: a partial
    translation is never returned.
    """

    caddyfile: str
    messages: list
    header_blocks: int = 0
    redirects: int = 0
    skipped: int = 0

    @property
    def refused(self) -> list:
        return [m for m in self.messages if m.level == "refused"]

    @property
    def ok(self) -> bool:
        return not self.refused


# ---------------------------------------------------------------------------
# Path patterns


@dataclasses.dataclass
class Pattern:
    """A parsed path pattern: literal text, splats and placeholders.

    ``parts`` holds ("lit", text), ("splat", "") and ("ph", name) tuples.
    Literal text is percent-decoded, because Caddy matches the decoded path.
    """

    raw: str
    parts: list

    @property
    def names(self) -> list:
        """Capture names in order; a splat is called ``splat``."""
        return ["splat" if kind == "splat" else value for kind, value in self.parts if kind != "lit"]

    @property
    def static(self) -> bool:
        return all(kind == "lit" for kind, _ in self.parts)

    @property
    def literal(self) -> str:
        return "".join(value for _, value in self.parts)

    def regex(self, slash_optional: bool = False) -> str:
        """An anchored, case-sensitive regular expression for this pattern.

        With ``slash_optional`` the pattern matches with or without a trailing
        slash, which is how Netlify documents its redirect matching.
        """
        parts = list(self.parts)
        tail = "$"
        if slash_optional and self.raw != "/":
            last = parts[-1]
            if last[0] == "lit" and last[1].endswith("/"):
                parts[-1] = ("lit", last[1][:-1])
            tail = "/?$"
        out = []
        for index, (kind, value) in enumerate(parts):
            if kind == "lit":
                out.append(_regex_escape(value))
            elif kind == "splat":
                lazy = slash_optional and index == len(parts) - 1
                out.append("(.*?)" if lazy else "(.*)")
            else:
                out.append("([^/]+)")
        return "^" + "".join(out) + tail


def _regex_escape(text: str) -> str:
    return "".join("\\" + c if c in REGEX_SPECIAL else c for c in text)


def _is_control(c: str) -> bool:
    return (ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F


def _decode(text: str, raw: str) -> str:
    if re.search(r"%2[fF]", text):
        raise Refused(f"path {raw!r} encodes a / as %2F, which this tool does not translate")
    try:
        decoded = unquote(text, errors="strict")
    except UnicodeDecodeError:
        raise Refused(f"path {raw!r} has a percent-encoded sequence that is not valid UTF-8") from None
    for c in decoded:
        if c in CADDY_UNSAFE or c.isspace() or _is_control(c):
            raise Refused(f"path {raw!r} contains {c!r}, which this tool does not write into a Caddyfile")
    return decoded


def parse_pattern(raw: str, platform: str, kind: str) -> Pattern:
    """Parse a path from ``_headers`` (kind "headers") or a redirect source
    (kind "redirects"). Raises :class:`Refused` for anything ambiguous."""
    if not raw.startswith("/"):
        raise Refused(f"path {raw!r} does not start with /")
    for c in raw:
        if c in CADDY_UNSAFE or c.isspace() or _is_control(c):
            raise Refused(f"path {raw!r} contains {c!r}, which this tool does not write into a Caddyfile")
    if "?" in raw:
        raise Refused(f"path {raw!r} contains ?; a path pattern cannot match a query string")
    if "#" in raw:
        raise Refused(f"path {raw!r} contains #; browsers never send the fragment, so it cannot match")
    if "//" in raw:
        raise Refused(f"path {raw!r} contains an empty segment (//), which this tool does not translate")

    parts: list = []
    buf: list = []

    def flush() -> None:
        if buf:
            parts.append(("lit", _decode("".join(buf), raw)))
            buf.clear()

    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "*":
            flush()
            parts.append(("splat", ""))
            i += 1
            continue
        if c == ":":
            m = PLACEHOLDER.match(raw, i)
            if m:
                name = m.group(1)
                if platform == NETLIFY:
                    after = raw[m.end() : m.end() + 1]
                    if raw[i - 1] != "/" or after not in ("", "/"):
                        raise Refused(
                            f"placeholder :{name} in {raw!r} is not a whole path segment; Netlify documents "
                            "placeholders only at the start of a segment, and this tool translates them only "
                            "as whole segments"
                        )
                flush()
                parts.append(("ph", name))
                i = m.end()
                continue
        buf.append(c)
        i += 1
    flush()

    names = [value for k, value in parts if k == "ph"]
    if "splat" in names:
        raise Refused(f":splat in {raw!r} is reserved for the text a * matches and cannot name a placeholder")
    seen = set()
    for name in names:
        if name in seen:
            raise Refused(f"placeholder :{name} appears twice in {raw!r}")
        seen.add(name)
    splats = sum(1 for k, _ in parts if k == "splat")
    if platform == CLOUDFLARE and splats > 1:
        raise Refused(f"path {raw!r} has {splats} splats; Cloudflare Pages allows one * in a path")
    if platform == NETLIFY and kind == "redirects" and splats and (splats > 1 or parts[-1][0] != "splat"):
        raise Refused(f"source {raw!r} has a * that is not at the end; Netlify allows a splat only at the end")
    return Pattern(raw, parts)


# ---------------------------------------------------------------------------
# Overlap between two patterns (used for header rules)


def _items(pattern: Pattern) -> list:
    items: list = []
    for kind, value in pattern.parts:
        if kind == "lit":
            items.extend(("c", c) for c in value)
        elif kind == "splat":
            items.append(("any",))
        else:  # a placeholder: one or more characters other than /
            items.append(("one",))
            items.append(("many",))
    return items


def _close(items: list, states) -> frozenset:
    todo = list(states)
    seen = set(states)
    while todo:
        i = todo.pop()
        if i < len(items) and items[i][0] in ("any", "many") and i + 1 not in seen:
            seen.add(i + 1)
            todo.append(i + 1)
    return frozenset(seen)


def _step(items: list, states: frozenset, c: str) -> frozenset:
    nxt = set()
    for i in states:
        if i == len(items):
            continue
        kind = items[i][0]
        if kind == "c":
            if items[i][1] == c:
                nxt.add(i + 1)
        elif kind == "any":
            nxt.add(i)
        elif c != "/":
            nxt.add(i + 1 if kind == "one" else i)
    return _close(items, nxt)


def overlap_example(a: Pattern, b: Pattern) -> str | None:
    """The shortest path both patterns match, or None if no path matches both.

    This is an intersection test on the two patterns as automata, so it is
    exact: None means no request path can match both.
    """
    ia, ib = _items(a), _items(b)
    literals = {item[1] for item in ia + ib if item[0] == "c"}
    filler = next((c for c in _FILLERS if c not in literals), "é")
    # The filler goes first, so that among equally short examples the one
    # built from an ordinary letter is found.
    alphabet = [filler] + sorted(literals | {"/"})
    start = (_close(ia, {0}), _close(ib, {0}))
    queue = collections.deque([(start, "")])
    seen = {start}
    while queue:
        (sa, sb), text = queue.popleft()
        if len(ia) in sa and len(ib) in sb:
            return text
        for c in alphabet:
            na, nb = _step(ia, sa, c), _step(ib, sb, c)
            if na and nb and (na, nb) not in seen:
                seen.add((na, nb))
                queue.append(((na, nb), text + c))
    return None


# ---------------------------------------------------------------------------
# Site files (for Netlify's shadowing rule)


def scan_site(root: str) -> dict:
    """Map every path a static host could serve from ``root`` to its file.

    A file ``a/b.html`` can be served at /a/b.html and /a/b; ``a/index.html``
    also at /a/ and /a. Used to check that no file shadows a Netlify rule.
    """
    paths: dict = {}

    def add(url: str, rel: str) -> None:
        paths.setdefault(url, rel)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            if rel in ("_headers", "_redirects"):
                continue
            url = "/" + rel
            add(url, rel)
            if url.endswith(".html"):
                add(url[: -len(".html")], rel)
            if name == "index.html":
                folder = url[: -len("index.html")]
                add(folder, rel)
                if folder != "/":
                    add(folder[:-1], rel)
    return paths


# ---------------------------------------------------------------------------
# Translation


@dataclasses.dataclass
class _HeaderBlock:
    line: int
    path: str
    pattern: Pattern | None = None
    skipped: bool = False
    broken: bool = False
    sets: list = dataclasses.field(default_factory=list)  # (name, value, line)
    removes: list = dataclasses.field(default_factory=list)  # (name, line)


@dataclasses.dataclass
class _Redirect:
    line: int
    text: str
    pattern: Pattern
    regex: str
    destination: str
    status: int


def _lines(text: str):
    if text.startswith("\ufeff"):  # a byte order mark
        text = text[1:]
    for number, line in enumerate(text.split("\n"), 1):
        yield number, line.rstrip("\r")


def _caddy_quote(value: str) -> str:
    if '"' not in value and "\\" not in value:
        return '"' + value + '"'
    return "`" + value + "`"


def _safe_comment(text: str) -> bool:
    return not any(c in "{}" or _is_control(c) for c in text)


class _Translator:
    def __init__(self, platform: str, keep_query: bool, site_paths, headers_name: str, redirects_name: str):
        if platform not in PLATFORMS:
            raise ValueError(f"platform must be one of {', '.join(PLATFORMS)}")
        self.platform = platform
        self.pname = PLATFORM_NAMES[platform]
        self.keep_query = keep_query or platform == NETLIFY
        self.site_paths = site_paths
        self.headers_name = headers_name
        self.redirects_name = redirects_name
        # Generated comments name the file only, so no local path ends up in
        # a server's configuration.
        self.headers_label = os.path.basename(headers_name) or headers_name
        self.redirects_label = os.path.basename(redirects_name) or redirects_name
        self.messages: list = []
        self.skipped_comments: dict = {"headers": [], "redirects": []}

    # feedback ------------------------------------------------------------

    def refuse(self, file: str, line: int, text: str) -> None:
        self.messages.append(Message(file, line, "refused", text))

    def skip(self, file: str, line: int, text: str) -> None:
        self.messages.append(Message(file, line, "skipped", text))

    def note(self, file: str, line: int, text: str) -> None:
        self.messages.append(Message(file, line, "note", text))

    # _headers ------------------------------------------------------------

    def headers(self, text: str) -> list:
        f = self.headers_name
        cf = self.platform == CLOUDFLARE
        blocks: list = []
        cur: _HeaderBlock | None = None
        for n, line in _lines(text):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if cf and len(line) > CF_MAX_HEADER_LINE:
                limit = CF_MAX_HEADER_LINE
                self.refuse(f, n, f"line is {len(line)} characters; Cloudflare Pages allows {limit} per line")
                if cur is not None:
                    cur.broken = True
                continue
            if not line[0].isspace():
                cur = _HeaderBlock(n, stripped)
                blocks.append(cur)
                if cf and len(blocks) == CF_MAX_HEADER_RULES + 1:
                    limit = CF_MAX_HEADER_RULES
                    self.refuse(f, n, f"this is header rule {len(blocks)}; Cloudflare Pages allows {limit}")
                if len(stripped.split()) != 1:
                    self.refuse(f, n, f"a path line holds one path, found {stripped!r}")
                    cur.broken = True
                elif stripped.startswith(("http://", "https://")):
                    cur.skipped = True
                    self.skip(
                        f, n, f"host-level path {stripped}; set headers for a host in that host's Caddy site block"
                    )
                    if _safe_comment(stripped):
                        self.skipped_comments["headers"].append(
                            (n, f"\t# {self.headers_label} line {n} skipped (host-level): {stripped}")
                        )
                else:
                    try:
                        cur.pattern = parse_pattern(stripped, self.platform, "headers")
                    except Refused as e:
                        self.refuse(f, n, e.text)
                        cur.broken = True
                continue
            if cur is None:
                self.refuse(f, n, "this line is indented, but no path line comes before it")
                continue
            if cur.skipped:
                continue
            try:
                self._header_line(cur, stripped, n)
            except Refused as e:
                self.refuse(f, n, e.text)
                cur.broken = True

        good = [b for b in blocks if not b.broken and not b.skipped]
        for b in good:
            self._check_value_placeholders(b)
        good = [b for b in good if not b.broken]
        self._check_overlaps(good)
        for b in good:
            if not b.sets and not b.removes:
                self.note(f, b.line, f"path {b.path} has no headers under it; nothing to translate")
        return [b for b in good if not b.broken and (b.sets or b.removes)]

    def _header_line(self, cur: _HeaderBlock, stripped: str, n: int) -> None:
        if stripped.startswith("!"):
            if self.platform == NETLIFY:
                raise Refused("removing a header with '!' is Cloudflare Pages syntax; Netlify does not document it")
            name = stripped[1:].strip()
            if not HEADER_NAME.fullmatch(name):
                raise Refused(f"expected '! Header-Name' to remove a header, found {stripped!r}")
            self._check_duplicate(cur, name, removing=True)
            cur.removes.append((name, n))
            return
        if ":" not in stripped:
            raise Refused(f"expected 'Name: value', found {stripped!r}")
        name, value = (s.strip() for s in stripped.split(":", 1))
        if not HEADER_NAME.fullmatch(name):
            raise Refused(
                f"{name!r} is not a header name this tool writes (letters, digits and !#$%&'+.^_|~- only, "
                "starting with a letter or digit)"
            )
        if name.lower() in CADDY_HEADER_WORDS:
            raise Refused(f"a header named {name!r} would be read as Caddy's own {name.lower()!r} option")
        if not value:
            raise Refused(f"{name} has an empty value, and the platforms do not document what that does")
        for c in value:
            if c in "{}":
                raise Refused(
                    f"the value of {name} contains {c!r}; Caddy reads braces as a placeholder, and this version "
                    "does not escape them"
                )
            if _is_control(c):
                raise Refused(f"the value of {name} contains a control character")
        if "`" in value and ('"' in value or "\\" in value):
            raise Refused(
                f"the value of {name} contains a backtick and also a quote or backslash, which cannot be "
                "written as one Caddyfile token"
            )
        self._check_duplicate(cur, name, removing=False)
        cur.sets.append((name, value, n))

    @staticmethod
    def _check_duplicate(cur: _HeaderBlock, name: str, removing: bool) -> None:
        key = name.lower()
        for other, _, line in cur.sets:
            if other.lower() == key:
                if removing:
                    raise Refused(f"{name} is set on line {line} and removed here, in the same block")
                raise Refused(
                    f"{name} is already set in this block, on line {line}; the platforms join repeated values "
                    "in different ways, so write the value once"
                )
        for other, line in cur.removes:
            if other.lower() == key:
                if removing:
                    raise Refused(f"{name} is already removed in this block, on line {line}")
                raise Refused(f"{name} is removed on line {line} and set here, in the same block")

    def _check_value_placeholders(self, b: _HeaderBlock) -> None:
        names = b.pattern.names
        counts: collections.Counter = collections.Counter()
        for name, value, line in b.sets:
            for m in PLACEHOLDER.finditer(value):
                ref = m.group(1)
                if ref not in names:
                    continue
                if self.platform == NETLIFY:
                    self.refuse(
                        self.headers_name,
                        line,
                        f"the value of {name} contains :{ref}, which the path also defines; Netlify does not "
                        "document whether it substitutes placeholders in header values",
                    )
                    b.broken = True
                    return
                counts[ref] += 1
                if counts[ref] == 2:
                    self.refuse(
                        self.headers_name,
                        line,
                        f":{ref} is used a second time in this block; Cloudflare Pages documents that a "
                        "placeholder can only be referenced once",
                    )
                    b.broken = True
                    return

    def _check_overlaps(self, good: list) -> None:
        f = self.headers_name
        for j, b in enumerate(good):
            b_sets = {name.lower(): (name, line) for name, _, line in b.sets}
            for a in good[:j]:
                if b.broken:
                    break
                a_sets = {name.lower(): line for name, _, line in a.sets}
                a_removes = {name.lower(): line for name, line in a.removes}
                both = [k for k in b_sets if k in a_sets]
                after_removal = [k for k in b_sets if k in a_removes]
                if not both and not after_removal:
                    continue
                example = overlap_example(a.pattern, b.pattern)
                if example is None:
                    continue
                if both:
                    name, line = b_sets[both[0]]
                    if self.platform == CLOUDFLARE:
                        how = "Cloudflare Pages joins the two values with a comma, and Caddy would send one"
                    else:
                        how = "Caddy would send one value, and this tool does not reproduce how Netlify combines them"
                    self.refuse(
                        f,
                        line,
                        f"{name} is also set on line {a_sets[both[0]]} (path {a.path}), and both paths match "
                        f"{example}; {how}. Set {name} in one block only",
                    )
                else:
                    name, line = b_sets[after_removal[0]]
                    self.refuse(
                        f,
                        line,
                        f"{name} is removed on line {a_removes[after_removal[0]]} (path {a.path}) and set again "
                        f"here, and both paths match {example}; the platform's result for a header set after "
                        "it is removed is not documented, so this tool does not guess",
                    )
                b.broken = True

    def emit_header_block(self, b: _HeaderBlock) -> list:
        matcher = f"p2c_h{b.line}"
        names = b.pattern.names
        values = []
        captured = False
        for name, value, _ in b.sets:
            if self.platform == CLOUDFLARE and names:

                def repl(m, _names=names):
                    ref = m.group(1)
                    if ref in _names:
                        return "{re.%s.%d}" % (matcher, _names.index(ref) + 1)
                    return m.group(0)

                new = PLACEHOLDER.sub(repl, value)
                captured = captured or new != value
                value = new
            values.append((name, _caddy_quote(value)))
        out = [f"\t# {self.headers_label} line {b.line}: {b.path}"]
        if b.path == "/*" and not captured:
            target = ""
        else:
            out.append(f"\t@{matcher} path_regexp {matcher} {b.pattern.regex()}")
            target = f" @{matcher}"
        if len(values) == 1:
            out.append(f"\theader{target} {values[0][0]} {values[0][1]}")
        elif values:
            out.append(f"\theader{target} {{")
            out.extend(f"\t\t{name} {value}" for name, value in values)
            out.append("\t}")
        for name, _ in b.removes:
            out.append(f"\theader{target} -{name}")
        return out

    # _redirects ----------------------------------------------------------

    def redirects(self, text: str) -> list:
        f = self.redirects_name
        cf = self.platform == CLOUDFLARE
        rules: list = []
        static = dynamic = 0
        for n, line in _lines(text):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                if cf and len(stripped) > CF_MAX_REDIRECT_LINE:
                    raise Refused(f"rule is {len(stripped)} characters; Cloudflare Pages allows {CF_MAX_REDIRECT_LINE}")
                fields = stripped.split()
                source = fields[0]
                if source.startswith(("http://", "https://")) or HOST_SOURCE.fullmatch(source):
                    self.skip(f, n, f"host-level rule {source}; write it as a Caddy site block for that host")
                    if _safe_comment(stripped):
                        text_ = " ".join(fields)
                        self.skipped_comments["redirects"].append(
                            (n, f"\t# {self.redirects_label} line {n} skipped (host-level): {text_}")
                        )
                    continue
                rule = self._redirect(fields, n)
                if rule.pattern.static:
                    static += 1
                    if cf and static == CF_MAX_STATIC_REDIRECTS + 1:
                        limit = CF_MAX_STATIC_REDIRECTS
                        raise Refused(f"this is static redirect {static}; Cloudflare Pages allows {limit}")
                else:
                    dynamic += 1
                    if cf and dynamic == CF_MAX_DYNAMIC_REDIRECTS + 1:
                        raise Refused(
                            f"this is dynamic redirect {dynamic}; Cloudflare Pages allows {CF_MAX_DYNAMIC_REDIRECTS}"
                        )
                rules.append(rule)
            except Refused as e:
                self.refuse(f, n, e.text)
        return self._check_order(rules)

    def _redirect(self, fields: list, n: int) -> _Redirect:
        source = fields[0]
        rest = list(fields[1:])
        query = []
        while rest and "=" in rest[0] and not rest[0].startswith(("/", "http://", "https://")):
            query.append(rest.pop(0))
        if query:
            raise Refused(
                f"the rule matches query parameters ({' '.join(query)}); this is not translated "
                "(Cloudflare Pages does not support it, and Netlify's matching of extra or missing "
                "parameters is not reproduced)"
            )
        if not rest:
            raise Refused(f"the rule for {source} has no destination")
        destination = rest.pop(0)
        status_token = None
        if rest and STATUS.fullmatch(rest[0]):
            status_token = rest.pop(0)
        if rest:
            if all("=" in t for t in rest):
                raise Refused(
                    f"the rule has conditions ({' '.join(rest)}); redirects by country, language, role or "
                    "cookie depend on the platform and are not translated"
                )
            raise Refused(f"unexpected fields after the destination: {' '.join(rest)}")

        force = False
        status = DEFAULT_STATUS[self.platform]
        if status_token is not None:
            m = STATUS.fullmatch(status_token)
            if len(m.group(1)) != 3:
                raise Refused(f"status {status_token!r} is not a three-digit HTTP status")
            status = int(m.group(1))
            force = m.group(2) == "!"
        self._check_status(status, force)

        pattern = parse_pattern(source, self.platform, "redirects")
        regex = pattern.regex(slash_optional=self.platform == NETLIFY)
        if self.platform == NETLIFY and not force:
            self._check_shadowing(source, regex)
        matcher = f"p2c_r{n}"
        target = self._destination(destination, pattern, matcher)
        return _Redirect(n, " ".join(fields), pattern, regex, target, status)

    def _check_status(self, status: int, force: bool) -> None:
        if force and self.platform == CLOUDFLARE:
            raise Refused(
                "the ! suffix is Netlify syntax and Cloudflare Pages does not document it; remove it "
                "(Pages applies a redirect even when a file exists at the path) or use --platform netlify"
            )
        if status == 200:
            raise Refused("status 200 is a rewrite (a proxy), not a redirect; this version translates redirects only")
        if status == 404:
            raise Refused("status 404 serves a custom not-found page; this version translates redirects only")
        if self.platform == NETLIFY and status == 307:
            raise Refused("Netlify documents status 307 as unsupported; use 302")
        if self.platform == NETLIFY and status in (303, 308):
            raise Refused(
                f"Netlify documents passing the query string on only for 301 and 302, so its behaviour for "
                f"{status} is not known; use 301 or 302"
            )
        if status not in REDIRECT_STATUSES[self.platform]:
            raise Refused(f"status {status} is not a redirect {self.pname} supports in _redirects")

    def _check_shadowing(self, source: str, regex: str) -> None:
        if self.site_paths is None:
            raise Refused(
                "Netlify applies a rule without ! only when no file exists at the path, and Caddy's redir always "
                "applies; pass --site-dir with your build output so the tool can check, or add ! (for example "
                "301!) if the rule should always apply"
            )
        compiled = re.compile(regex)
        for url, rel in self.site_paths.items():
            if compiled.fullmatch(url):
                raise Refused(
                    f"the file {rel} is served at {url}, which {source} matches, so Netlify would serve the file "
                    "and Caddy would redirect; add ! if the rule should always apply, or remove the file"
                )

    def _destination(self, destination: str, pattern: Pattern, matcher: str) -> str:
        if not destination.startswith(("/", "http://", "https://")):
            raise Refused(f"destination {destination!r} is not a path starting with / or an http(s) URL")
        if destination.startswith("//"):
            raise Refused(f"destination {destination!r} is protocol-relative, which this tool does not translate")
        for c in destination:
            if c in CADDY_UNSAFE or _is_control(c):
                raise Refused(
                    f"destination {destination!r} contains {c!r}, which this tool does not write into a Caddyfile"
                )
        if "*" in destination:
            raise Refused(f"destination {destination!r} contains *; refer to the matched text with :splat")
        names = pattern.names
        counts: collections.Counter = collections.Counter()

        def repl(m):
            ref = m.group(1)
            if ref not in names:
                if ref == "splat":
                    raise Refused(f"destination uses :splat, but the source {pattern.raw!r} has no *")
                raise Refused(f"destination uses :{ref}, but the source {pattern.raw!r} defines no such placeholder")
            counts[ref] += 1
            if counts[ref] == 2 and self.platform == CLOUDFLARE:
                raise Refused(
                    f"destination uses :{ref} twice; Cloudflare Pages documents that a placeholder can only be "
                    "referenced once"
                )
            return "{re.%s.%d}" % (matcher, names.index(ref) + 1)

        target = PLACEHOLDER.sub(repl, destination)
        if self.keep_query:
            if "?" in destination or "#" in destination:
                if self.platform == NETLIFY:
                    why = "Netlify passes the request's query string on"
                else:
                    why = "--keep-query is set"
                raise Refused(
                    f"destination {destination!r} has its own query string or fragment, and {why}; how the two "
                    "combine is not documented, so this is not translated"
                )
            target += "{?query}"
        return target

    def _check_order(self, rules: list) -> list:
        """Note rules that can never apply, and refuse the one ordering whose
        result Cloudflare Pages does not document."""
        f = self.redirects_name
        good = []
        static_seen: dict = {}  # normalised source path -> line
        dynamic_seen: list = []  # (compiled regex, rule)
        for rule in rules:
            if not rule.pattern.static:
                dynamic_seen.append((re.compile(rule.regex), rule))
                good.append(rule)
                continue
            path = rule.pattern.literal
            key = path
            if self.platform == NETLIFY and path != "/":
                key = path[:-1] if path.endswith("/") else path
            earlier = static_seen.get(key)
            if earlier is not None:
                self.note(f, rule.line, f"this rule never applies: line {earlier} matches {path} first")
                good.append(rule)
                continue
            static_seen[key] = rule.line
            first = next((r for rx, r in dynamic_seen if rx.fullmatch(path)), None)
            if first is None:
                good.append(rule)
            elif self.platform == NETLIFY:
                self.note(f, rule.line, f"this rule never applies: line {first.line} matches {path} first")
                good.append(rule)
            else:
                self.refuse(
                    f,
                    rule.line,
                    f"this static rule comes after the dynamic rule on line {first.line}, which also matches "
                    f"{path}; Cloudflare Pages says static redirects should come before dynamic ones and does "
                    f"not say which applies here, so move this line above line {first.line}",
                )
        return good

    @staticmethod
    def emit_redirect(rule: _Redirect, file: str) -> list:
        matcher = f"p2c_r{rule.line}"
        return [
            f"\t# {file} line {rule.line}: {rule.text}",
            f"\t@{matcher} path_regexp {matcher} {rule.regex}",
            f"\tredir @{matcher} {rule.destination} {rule.status}",
        ]


def translate(
    headers: str | None = None,
    redirects: str | None = None,
    *,
    platform: str = CLOUDFLARE,
    keep_query: bool = False,
    site_paths: dict | None = None,
    headers_name: str = "_headers",
    redirects_name: str = "_redirects",
) -> Result:
    """Translate the text of a ``_headers`` and/or ``_redirects`` file.

    ``site_paths`` maps served paths to files (see :func:`scan_site`); it is
    needed for Netlify rules without ``!``. ``headers_name`` and
    ``redirects_name`` are used in messages and in the generated comments.
    """
    t = _Translator(platform, keep_query, site_paths, headers_name, redirects_name)
    blocks = t.headers(headers) if headers is not None else []
    rules = t.redirects(redirects) if redirects is not None else []
    order = {headers_name: 0, redirects_name: 1}
    t.messages.sort(key=lambda m: (order.get(m.file, 2), m.line))
    skipped = sum(1 for m in t.messages if m.level == "skipped")
    result = Result("", t.messages, len(blocks), len(rules), skipped)
    if not result.ok:
        return result

    sources = [
        label for label, text in ((t.headers_label, headers), (t.redirects_label, redirects)) if text is not None
    ]
    out = [
        f"# Generated by pages-to-caddy {__version__} from {' and '.join(sources)}",
        f"# ({t.pname} rules). Edit the source files and generate again.",
    ]
    sections = []
    redirect_items = [(r.line, t.emit_redirect(r, t.redirects_label)) for r in rules]
    redirect_items += [(n, [c]) for n, c in t.skipped_comments["redirects"]]
    header_items = [(b.line, t.emit_header_block(b)) for b in blocks]
    header_items += [(n, [c]) for n, c in t.skipped_comments["headers"]]
    # Redirects first: on both platforms a redirect wins over _headers, so a
    # redirected response carries none of the translated headers.
    for _, lines in sorted(redirect_items, key=lambda item: item[0]):
        sections.append(lines)
    for _, lines in sorted(header_items, key=lambda item: item[0]):
        sections.append(lines)
    if rules or blocks:
        out.append("route {")
        for index, lines in enumerate(sections):
            if index:
                out.append("")
            out.extend(lines)
        out.append("}")
    else:
        out.append("# No rules to translate.")
        out.extend(line.strip() for lines in sections for line in lines)
    result.caddyfile = "\n".join(out) + "\n"
    return result


# ---------------------------------------------------------------------------
# Command line


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _write_atomic(path: str, text: str) -> None:
    """Replace ``path`` in one step, so a reader never sees half a file.

    The new file keeps the old one's permissions, or gets the usual ones for
    a new file (mkstemp alone would leave it readable by its owner only,
    which a web server running as another user could not read).
    """
    folder = os.path.dirname(os.path.abspath(path))
    try:
        mode = os.stat(path).st_mode & 0o7777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=".pages-to-caddy-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pages-to-caddy",
        description=(
            "Translate Cloudflare Pages or Netlify _headers and _redirects files into Caddyfile directives. "
            "Any rule that cannot be translated faithfully is refused, with its line, and nothing is written."
        ),
        epilog="Exit status: 0 translated, 1 one or more rules refused, 2 usage or file error.",
    )
    parser.add_argument("dir", nargs="?", help="directory holding _headers and/or _redirects (default: .)")
    parser.add_argument("--headers", metavar="FILE", help="read this _headers file instead of DIR/_headers")
    parser.add_argument("--redirects", metavar="FILE", help="read this _redirects file instead of DIR/_redirects")
    parser.add_argument(
        "--platform",
        choices=PLATFORMS,
        default=CLOUDFLARE,
        help="whose rules the files follow; sets the default status and other differences (default: cloudflare)",
    )
    parser.add_argument(
        "--keep-query",
        action="store_true",
        help="carry the request's query string on to each redirect's destination (always on for netlify)",
    )
    parser.add_argument(
        "--site-dir",
        metavar="DIR",
        help="the built site, used to check that no file shadows a Netlify rule without !",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate only; write nothing")
    mode.add_argument("-o", "--output", metavar="FILE", help="write to FILE (replaced atomically) instead of stdout")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    def fail(text: str) -> int:
        sys.stderr.write(f"pages-to-caddy: error: {text}\n")
        return 2

    base = args.dir if args.dir is not None else "."
    if not os.path.isdir(base):
        return fail(f"{base} is not a directory")
    if args.site_dir is not None and not os.path.isdir(args.site_dir):
        return fail(f"--site-dir {args.site_dir} is not a directory")

    def locate(explicit: str | None, name: str) -> str | None:
        if explicit is not None:
            return explicit
        candidate = os.path.join(base, name)
        return candidate if os.path.isfile(candidate) else None

    headers_path = locate(args.headers, "_headers")
    redirects_path = locate(args.redirects, "_redirects")
    if headers_path is None and redirects_path is None:
        return fail(f"found no _headers or _redirects in {base}")
    try:
        headers = _read(headers_path) if headers_path else None
        redirects = _read(redirects_path) if redirects_path else None
    except (OSError, UnicodeDecodeError) as e:
        return fail(str(e))

    site_paths = scan_site(args.site_dir) if args.site_dir else None
    result = translate(
        headers,
        redirects,
        platform=args.platform,
        keep_query=args.keep_query,
        site_paths=site_paths,
        headers_name=headers_path or "_headers",
        redirects_name=redirects_path or "_redirects",
    )
    for message in result.messages:
        sys.stderr.write(f"{message}\n")

    if not result.ok:
        sys.stderr.write(f"pages-to-caddy: refused {_plural(len(result.refused), 'rule')}; nothing was written\n")
        return 1

    summary = (
        f"{_plural(result.header_blocks, 'header block')} and {_plural(result.redirects, 'redirect')}; "
        f"skipped {_plural(result.skipped, 'host-level rule')}"
    )
    if args.check:
        sys.stderr.write(f"pages-to-caddy: check passed: {summary}\n")
        return 0
    if args.output:
        try:
            _write_atomic(args.output, result.caddyfile)
        except OSError as e:
            return fail(str(e))
        sys.stderr.write(f"pages-to-caddy: wrote {args.output}: {summary}\n")
    else:
        sys.stdout.write(result.caddyfile)
        sys.stderr.write(f"pages-to-caddy: translated {summary}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
