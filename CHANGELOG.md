# Changelog

Notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## 0.1.0 - 2026-11-10

### Added

- `pages_to_caddy.py`, a single-file command-line tool and module (standard library only, Python 3.10 or later) that translates Cloudflare Pages and Netlify `_headers` and `_redirects` files into Caddyfile directives inside one `route` block.
- Headers: exact paths, splats, placeholders, placeholders and splats used in values (Cloudflare), header removal with `! Name` (Cloudflare), and values that need backtick quoting.
- Redirects: exact paths, splats, placeholders, absolute destinations, statuses 301, 302, 303, 307 and 308 (301 and 302 on Netlify), each platform's default status, Netlify's `!` force, trailing-slash matching and query-string pass-through, and `--keep-query` for Cloudflare.
- Refusals, each naming the file and line, for every rule that cannot be translated faithfully, including headers set by two blocks on overlapping paths (found by intersecting the patterns), static redirects placed behind matching dynamic ones on Cloudflare, Netlify rules a built file would shadow (`--site-dir`), rewrites, conditions, query-parameter matching and Cloudflare's documented limits.
- Host-level rules are skipped and reported, and quoted as comments in the output.
- `--check` to validate without writing, `-o` to replace an output file atomically and only on success, and exit statuses 0, 1 and 2.
- Offline unittest suite with golden fixtures, and optional tests that run `caddy adapt` and `caddy validate` on the output when `caddy` is on `PATH`.
- GitHub Actions workflow running the tests on Python 3.10 to 3.14, and a job that installs Caddy 2.11.9 to run the Caddy tests.
