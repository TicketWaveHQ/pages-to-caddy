"""The command line, the golden fixtures and the README example."""

from __future__ import annotations

import ast
import contextlib
import io
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest

from tests.support import EXAMPLES, FIXTURES, ROOT, p2c

SCRIPT = os.path.join(ROOT, "pages_to_caddy.py")


def run(*args: str):
    """Run main() in-process and return (status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = p2c.main(list(args))
        except SystemExit as e:  # argparse exits for --version and usage errors
            status = e.code
    return status, out.getvalue(), err.getvalue()


def fixture_args(name: str) -> list:
    folder = os.path.join(FIXTURES, name)
    path = os.path.join(folder, "args.txt")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return shlex.split(handle.read().replace("{fixture}", folder))


def fixture_names() -> list:
    return sorted(n for n in os.listdir(FIXTURES) if os.path.isdir(os.path.join(FIXTURES, n)))


def write(folder: str, name: str, text: str) -> str:
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class Golden(unittest.TestCase):
    """Each fixture's output must match its expected.caddy byte for byte."""

    def test_fixtures(self):
        names = fixture_names()
        self.assertGreaterEqual(len(names), 3)
        for name in names:
            with self.subTest(fixture=name):
                folder = os.path.join(FIXTURES, name)
                status, out, err = run(*fixture_args(name), folder)
                self.assertEqual(status, 0, err)
                with open(os.path.join(folder, "expected.caddy"), encoding="utf-8") as handle:
                    self.assertEqual(out, handle.read())

    def test_readme_example_output_is_current(self):
        folder = os.path.join(EXAMPLES, "basic")
        status, out, err = run(folder)
        self.assertEqual(status, 0, err)
        with open(os.path.join(folder, "pages-rules.caddy"), encoding="utf-8") as handle:
            self.assertEqual(out, handle.read())

    def test_readme_shows_the_real_output(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        with open(os.path.join(EXAMPLES, "basic", "pages-rules.caddy"), encoding="utf-8") as handle:
            self.assertIn(handle.read(), readme)
        status, _, err = run(os.path.join(EXAMPLES, "refused"))
        self.assertEqual(status, 1)
        shown = err.replace(os.path.join(EXAMPLES, "refused") + os.sep, "examples/refused/")
        self.assertIn(shown, readme)

    def test_refused_example_is_refused(self):
        status, out, err = run(os.path.join(EXAMPLES, "refused"))
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("nothing was written", err)


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_stdout_and_summary(self):
        write(self.dir, "_redirects", "/a /b 301\nhttps://www.example.com/* https://example.com/:splat 301\n")
        status, out, err = run(self.dir)
        self.assertEqual(status, 0)
        self.assertIn("redir @p2c_r1 /b 301", out)
        self.assertIn("skipped: host-level rule", err)
        self.assertIn("translated 0 header blocks and 1 redirect; skipped 1 host-level rule", err)

    def test_generated_comments_name_files_without_their_folders(self):
        write(self.dir, "_redirects", "/a /b 301\n")
        _, out, _ = run(self.dir)
        self.assertNotIn(self.dir, out)
        self.assertIn("# _redirects line 1:", out)

    def test_messages_name_the_file_as_given(self):
        write(self.dir, "_redirects", "/a /b 200\n")
        _, _, err = run(self.dir)
        self.assertIn(os.path.join(self.dir, "_redirects") + ":1: refused:", err)

    def test_output_file(self):
        write(self.dir, "_headers", "/*\n  X-A: 1\n")
        target = os.path.join(self.dir, "out.caddy")
        status, out, err = run(self.dir, "-o", target)
        self.assertEqual(status, 0, err)
        self.assertEqual(out, "")
        with open(target, encoding="utf-8") as handle:
            self.assertIn('header X-A "1"', handle.read())
        self.assertIn("wrote", err)

    def test_output_file_keeps_permissions(self):
        write(self.dir, "_headers", "/*\n  X-A: 1\n")
        target = write(self.dir, "out.caddy", "old\n")
        os.chmod(target, 0o640)
        status, _, _ = run(self.dir, "-o", target)
        self.assertEqual(status, 0)
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)

    def test_new_output_file_is_not_private(self):
        write(self.dir, "_headers", "/*\n  X-A: 1\n")
        target = os.path.join(self.dir, "new.caddy")
        old = os.umask(0o022)
        try:
            run(self.dir, "-o", target)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o644)

    def test_refusal_leaves_the_existing_output_untouched(self):
        write(self.dir, "_redirects", "/a /b 200\n")
        target = write(self.dir, "out.caddy", "previous good output\n")
        status, out, err = run(self.dir, "-o", target)
        self.assertEqual(status, 1)
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "previous good output\n")
        self.assertIn("nothing was written", err)
        self.assertEqual(sorted(os.listdir(self.dir)), ["_redirects", "out.caddy"])

    def test_check_writes_nothing(self):
        write(self.dir, "_redirects", "/a /b 301\n")
        status, out, err = run("--check", self.dir)
        self.assertEqual(status, 0)
        self.assertEqual(out, "")
        self.assertIn("check passed", err)

    def test_check_reports_refusals(self):
        write(self.dir, "_redirects", "/a /b 200\n/c /d 301!\n")
        status, out, err = run("--check", self.dir)
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("refused 2 rules", err)

    def test_check_and_output_are_exclusive(self):
        write(self.dir, "_redirects", "/a /b 301\n")
        with contextlib.redirect_stderr(io.StringIO()):
            status, _, _ = run("--check", "-o", os.path.join(self.dir, "x"), self.dir)
        self.assertEqual(status, 2)

    def test_explicit_files(self):
        headers = write(self.dir, "my-headers", "/*\n  X-A: 1\n")
        redirects = write(self.dir, "my-redirects", "/a /b 301\n")
        status, out, err = run("--headers", headers, "--redirects", redirects)
        self.assertEqual(status, 0, err)
        self.assertIn("from my-headers and my-redirects", out)

    def test_platform_option(self):
        write(self.dir, "_redirects", "/a /b\n")
        _, out, _ = run(self.dir)
        self.assertIn(" 302\n", out)
        status, out, err = run("--platform", "netlify", "--site-dir", self.dir, self.dir)
        self.assertEqual(status, 0, err)
        self.assertIn("/b{?query} 301\n", out)

    def test_keep_query_option(self):
        write(self.dir, "_redirects", "/a /b 301\n")
        _, out, _ = run("--keep-query", self.dir)
        self.assertIn("redir @p2c_r1 /b{?query} 301", out)

    def test_site_dir_option(self):
        site = os.path.join(self.dir, "site")
        os.makedirs(site)
        write(site, "about.html", "x")
        write(self.dir, "_redirects", "/about /team\n")
        status, _, err = run("--platform", "netlify", "--site-dir", site, self.dir)
        self.assertEqual(status, 1)
        self.assertIn("about.html", err)

    def test_missing_directory(self):
        status, _, err = run(os.path.join(self.dir, "nope"))
        self.assertEqual(status, 2)
        self.assertIn("is not a directory", err)

    def test_missing_site_dir(self):
        write(self.dir, "_redirects", "/a /b 301\n")
        status, _, err = run("--site-dir", os.path.join(self.dir, "nope"), self.dir)
        self.assertEqual(status, 2)

    def test_no_rule_files(self):
        status, _, err = run(self.dir)
        self.assertEqual(status, 2)
        self.assertIn("found no _headers or _redirects", err)

    def test_explicit_file_that_does_not_exist(self):
        status, _, err = run("--headers", os.path.join(self.dir, "nope"))
        self.assertEqual(status, 2)

    def test_file_that_is_not_utf8(self):
        with open(os.path.join(self.dir, "_headers"), "wb") as handle:
            handle.write(b"/a\n  X-A: \xff\n")
        status, _, err = run(self.dir)
        self.assertEqual(status, 2)

    def test_version(self):
        status, out, _ = run("--version")
        self.assertEqual(status, 0)
        self.assertEqual(out.strip(), f"pages-to-caddy {p2c.__version__}")

    def test_runs_as_a_script(self):
        write(self.dir, "_redirects", "/a /b 301\n")
        proc = subprocess.run([sys.executable, SCRIPT, self.dir], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("redir @p2c_r1 /b 301", proc.stdout)

    def test_script_exit_status_on_refusal(self):
        write(self.dir, "_redirects", "/a /b 200\n")
        proc = subprocess.run([sys.executable, SCRIPT, self.dir], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")


class Compatibility(unittest.TestCase):
    def test_source_parses_as_python_3_10(self):
        for path in [SCRIPT] + [os.path.join(ROOT, "tests", n) for n in os.listdir(os.path.join(ROOT, "tests"))]:
            if path.endswith(".py"):
                with self.subTest(path=os.path.basename(path)):
                    with open(path, encoding="utf-8") as handle:
                        ast.parse(handle.read(), filename=path, feature_version=(3, 10))

    def test_standard_library_only(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is None:  # Python 3.10 has it; this guards older tools only
            self.skipTest("sys.stdlib_module_names is not available")
        self.assertEqual(sorted(m for m in modules if m not in stdlib and m != "__future__"), [])


if __name__ == "__main__":
    unittest.main()
