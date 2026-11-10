"""Path patterns, their regular expressions, overlap and the site scan."""

from __future__ import annotations

import os
import re
import tempfile
import unittest

from tests.support import p2c


def cf(raw: str, kind: str = "headers") -> p2c.Pattern:
    return p2c.parse_pattern(raw, p2c.CLOUDFLARE, kind)


class Patterns(unittest.TestCase):
    def test_parts(self):
        pattern = cf("/news/:year/*")
        self.assertEqual(pattern.parts, [("lit", "/news/"), ("ph", "year"), ("lit", "/"), ("splat", "")])
        self.assertEqual(pattern.names, ["year", "splat"])
        self.assertFalse(pattern.static)

    def test_static(self):
        pattern = cf("/about")
        self.assertTrue(pattern.static)
        self.assertEqual(pattern.literal, "/about")

    def test_colon_not_followed_by_a_letter_is_literal(self):
        self.assertEqual(cf("/a:1").parts, [("lit", "/a:1")])

    def test_placeholder_name_is_ascii_only(self):
        self.assertEqual(cf("/:aé").parts, [("lit", "/"), ("ph", "a"), ("lit", "é")])

    def test_regex_escapes_specials(self):
        self.assertEqual(cf("/a.b+c(d)[e]|f^g$").regex(), r"^/a\.b\+c\(d\)\[e\]\|f\^g\$$")

    def test_regex_behaviour(self):
        cases = {
            "/*": (["/", "/a", "/a/b"], []),
            "/a/*": (["/a/", "/a/b/c"], ["/a", "/ab"]),
            "/*.jpg": (["/x.jpg", "/a/b.jpg"], ["/x.jpeg", "/x.JPG"]),
            "/a/:id": (["/a/1"], ["/a/", "/a/1/", "/a/1/2"]),
            "/About": (["/About"], ["/about"]),
        }
        for raw, (hits, misses) in cases.items():
            rx = re.compile(cf(raw).regex())
            for path in hits:
                with self.subTest(pattern=raw, path=path):
                    self.assertIsNotNone(rx.fullmatch(path))
            for path in misses:
                with self.subTest(pattern=raw, path=path):
                    self.assertIsNone(rx.fullmatch(path))

    def test_slash_optional_regex(self):
        pattern = p2c.parse_pattern("/blog/", p2c.NETLIFY, "redirects")
        rx = re.compile(pattern.regex(slash_optional=True))
        self.assertIsNotNone(rx.fullmatch("/blog"))
        self.assertIsNotNone(rx.fullmatch("/blog/"))
        self.assertIsNone(rx.fullmatch("/blog//"))
        root = p2c.parse_pattern("/", p2c.NETLIFY, "redirects")
        self.assertEqual(root.regex(slash_optional=True), "^/$")


class Overlap(unittest.TestCase):
    def check(self, a: str, b: str, expected):
        self.assertEqual(p2c.overlap_example(cf(a), cf(b)), expected)
        self.assertEqual(p2c.overlap_example(cf(b), cf(a)) is None, expected is None)

    def test_disjoint(self):
        self.check("/", "/assets/*", None)
        self.check("/a", "/b", None)
        self.check("/a/:x", "/a/b/c", None)
        self.check("/*.css", "/*.js", None)

    def test_overlapping(self):
        self.check("/*", "/assets/*", "/assets/")
        self.check("/a*", "/a", "/a")
        self.check("/a/:x", "/a/*", "/a/x")
        self.check("/:a/x", "/y/:b", "/y/x")

    def test_example_matches_both_regexes(self):
        pairs = [("/blog/*", "/*.css"), ("/*.jpg", "/static/*"), ("/files/*/raw", "/files/a/*"), ("/:a/:b", "/x/*")]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                example = p2c.overlap_example(cf(a), cf(b))
                self.assertIsNotNone(example)
                self.assertIsNotNone(re.fullmatch(cf(a).regex(), example))
                self.assertIsNotNone(re.fullmatch(cf(b).regex(), example))

    def test_placeholder_needs_a_character(self):
        self.check("/a/:x", "/a/", None)


class SiteScan(unittest.TestCase):
    def test_paths_a_static_host_serves(self):
        with tempfile.TemporaryDirectory() as root:
            for rel in ("index.html", "about.html", "docs/index.html", "assets/app.css", "_headers", "_redirects"):
                path = os.path.join(root, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
            paths = p2c.scan_site(root)
        self.assertEqual(
            paths,
            {
                "/about.html": "about.html",
                "/about": "about.html",
                "/assets/app.css": "assets/app.css",
                "/docs/index.html": "docs/index.html",
                "/docs/index": "docs/index.html",
                "/docs/": "docs/index.html",
                "/docs": "docs/index.html",
                "/index.html": "index.html",
                "/index": "index.html",
                "/": "index.html",
            },
        )


if __name__ == "__main__":
    unittest.main()
