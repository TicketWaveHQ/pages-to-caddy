# Contributing

Thank you for helping. Issues and pull requests are welcome.

## The one rule

If a construct cannot be translated exactly, the tool refuses it. It never approximates, and it never drops a rule without saying so. A refusal names the file and line, says what was found and why it cannot be translated, and gives the fix when there is one.

So a change that adds support for a construct needs:

1. **The platform's own documentation for the behaviour.** Quote it in the pull request. Where the documentation is silent on something that changes the result (precedence, an empty match, how two values combine), keep refusing that case and say why.
2. **A test for the translation**, either a unit test or a case in a golden fixture under `tests/fixtures/<name>/` (`_headers`, `_redirects`, an optional `args.txt` and `expected.caddy`).
3. **A test for every case that is still refused**, asserting the line number and the words of the message.
4. **The README tables updated** (supported syntax, and what it refuses).

## Ground rules

- Standard library only, and Python 3.10 or later. A test parses every source file with the Python 3.10 grammar.
- The tool stays one file, `pages_to_caddy.py`, so it can be copied into a deploy.
- Output is deterministic: no timestamps, no absolute paths, and the same input gives the same file.
- Anything written into the Caddyfile must be safe as a Caddyfile token. Braces, quotes, backslashes and control characters are refused unless the change shows how Caddy reads them.

## Running the tests

```sh
python3 -m unittest -v
```

If `caddy` is on your `PATH`, `tests/test_caddy.py` also runs `caddy adapt` and `caddy validate` on every fixture's output. Please run it when you change the output format.

When the output changes on purpose, regenerate the expected files and read the diff before committing:

```sh
python3 pages_to_caddy.py tests/fixtures/cloudflare > tests/fixtures/cloudflare/expected.caddy
python3 pages_to_caddy.py examples/basic -o examples/basic/pages-rules.caddy
```

Fixtures with an `args.txt` need those arguments too (replace `{fixture}` with the fixture's folder). A test checks that the README still shows the real output of `examples/basic` and `examples/refused`, so update the README with it.

## Writing

Documentation, messages and comments use UK English. Please do not use em-dashes; a comma, a colon or a plain hyphen does the job. State only what you have checked.

## Security

Please do not report security problems in public issues. See [SECURITY.md](SECURITY.md).
