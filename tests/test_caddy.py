"""Optional: check the generated directives with a real Caddy binary.

Runs only when ``caddy`` is on PATH. Set PAGES_TO_CADDY_REQUIRE_CADDY=1 to
make a missing binary a failure instead of a skip (the CI job that installs
Caddy does this, so it cannot pass by skipping).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.support import EXAMPLES, FIXTURES

CADDY = shutil.which("caddy")
REQUIRED = os.environ.get("PAGES_TO_CADDY_REQUIRE_CADDY") == "1"


def outputs() -> list:
    found = []
    for name in sorted(os.listdir(FIXTURES)):
        path = os.path.join(FIXTURES, name, "expected.caddy")
        if os.path.exists(path):
            found.append((f"fixtures/{name}", path))
    found.append(("examples/basic", os.path.join(EXAMPLES, "basic", "pages-rules.caddy")))
    return found


class Caddy(unittest.TestCase):
    def setUp(self):
        if CADDY is None:
            if REQUIRED:
                self.fail("PAGES_TO_CADDY_REQUIRE_CADDY=1 but caddy is not on PATH")
            self.skipTest("caddy is not on PATH")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def caddyfile(self, snippet_path: str) -> str:
        path = os.path.join(self.tmp.name, "Caddyfile")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "http://example.com {\n"
                "\troot * /srv/www/example.com\n"
                f"\timport {snippet_path}\n"
                "\tfile_server\n"
                "}\n"
            )
        return path

    def caddy(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, HOME=self.tmp.name, XDG_DATA_HOME=self.tmp.name, XDG_CONFIG_HOME=self.tmp.name)
        return subprocess.run([CADDY, *args], capture_output=True, text=True, env=env, check=False, timeout=60)

    def test_adapt(self):
        for label, snippet in outputs():
            with self.subTest(output=label):
                proc = self.caddy("adapt", "--config", self.caddyfile(snippet), "--adapter", "caddyfile")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                config = json.loads(proc.stdout)
                text = json.dumps(config)
                with open(snippet, encoding="utf-8") as handle:
                    names = set(re.findall(r"@(p2c_[hr]\d+) ", handle.read()))
                for name in names:
                    self.assertIn(f'"{name}"', text, f"matcher {name} is missing from the adapted JSON")

    def test_validate(self):
        for label, snippet in outputs():
            with self.subTest(output=label):
                proc = self.caddy("validate", "--config", self.caddyfile(snippet), "--adapter", "caddyfile")
                self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
