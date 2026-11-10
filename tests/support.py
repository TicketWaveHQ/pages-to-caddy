"""Helpers shared by the tests."""

from __future__ import annotations

import os
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pages_to_caddy as p2c  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
EXAMPLES = os.path.join(ROOT, "examples")


def dedent(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


class Case(unittest.TestCase):
    """Assertions for translations that pass and translations that are refused."""

    platform = p2c.CLOUDFLARE

    def run_translate(self, headers=None, redirects=None, **options):
        options.setdefault("platform", self.platform)
        if headers is not None:
            headers = dedent(headers)
        if redirects is not None:
            redirects = dedent(redirects)
        return p2c.translate(headers, redirects, **options)

    def translated(self, headers=None, redirects=None, **options) -> str:
        result = self.run_translate(headers, redirects, **options)
        self.assertTrue(result.ok, "unexpected refusal:\n" + "\n".join(map(str, result.refused)))
        self.assertTrue(result.caddyfile)
        return result.caddyfile

    def refused(self, headers=None, redirects=None, *, line: int, contains: str, **options) -> p2c.Message:
        """Assert that exactly the given line is refused, with the given words."""
        result = self.run_translate(headers, redirects, **options)
        self.assertFalse(result.ok, "expected a refusal, got:\n" + result.caddyfile)
        self.assertEqual(result.caddyfile, "", "a refused translation must produce no output")
        lines = [m.line for m in result.refused]
        self.assertEqual(lines, [line], "\n".join(map(str, result.refused)))
        message = result.refused[0]
        self.assertIn(contains, message.text)
        return message

    def messages(self, result, level: str) -> list:
        return [m for m in result.messages if m.level == level]
