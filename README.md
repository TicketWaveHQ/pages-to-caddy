# pages-to-caddy

`pages-to-caddy` translates the `_headers` and `_redirects` files that Cloudflare Pages and Netlify read into Caddyfile directives. Any rule it cannot reproduce faithfully in Caddy is refused, with the file and line named, instead of being dropped or approximated.

It is for anyone moving a static site from Cloudflare Pages or Netlify to a Caddy server, or serving one build from both, who wants a single source of truth for headers and redirects instead of a hand port. It is one Python file, standard library only, for Python 3.10 or later.

## Start in 60 seconds

```sh
git clone https://github.com/TicketWaveHQ/pages-to-caddy.git
cd pages-to-caddy
python3 pages_to_caddy.py --check path/to/public                # validate only
python3 pages_to_caddy.py path/to/public -o pages-rules.caddy   # translate
```

`path/to/public` is the folder that holds your `_headers` and `_redirects`. Import the result inside your site block, before `file_server`, and let Caddy check it:

```caddyfile
example.com {
	root * /srv/www/example.com
	import /etc/caddy/pages-rules.caddy
	file_server
}
```

```sh
caddy validate --config /etc/caddy/Caddyfile
```

For Netlify files, add `--platform netlify --site-dir path/to/build` (see [Netlify](#netlify)).

## Why it refuses instead of guessing

A hand port of these files fails quietly. The rules someone compares before the switch carry over; a rule nobody compares can be lost without any error. A lost redirect answers 404, a lost security header leaves the site weaker, and neither says so until someone happens to check.

Generating the Caddy rules from the files the platform already reads removes the hand port. Refusing what cannot be translated exactly removes the quiet loss. A deploy that refuses is recoverable; a deploy that drops a security header or a redirect without saying so is the problem this tool exists to stop.

## How it works

1. It reads `DIR/_headers` and `DIR/_redirects` (either may be missing, but not both) and parses every rule under one platform's documented behaviour: `--platform cloudflare` (the default) or `--platform netlify`.
2. Each path pattern becomes an anchored, case-sensitive `path_regexp` matcher. A splat becomes `(.*)`, a placeholder `([^/]+)`, and a reference to either in a destination or header value becomes a Caddy capture placeholder such as `{re.p2c_r8.1}`. Matchers are named after their source line (`@p2c_r8` is `_redirects` line 8, `@p2c_h3` is `_headers` line 3), and each rule is preceded by a comment quoting it.
3. Everything goes into one `route` block, so Caddy keeps the order of the file instead of sorting directives by its own rules. Redirects come first, in file order, and the first match wins, as on both platforms. Headers follow, so a redirected response carries none of them. That is how this tool reads Cloudflare's "Redirects execute before headers, so in the case of a request matching rules in both files, the redirect will win out", and Netlify's "Custom headers apply only to files Netlify serves from our own backing store".
4. Headers are set with `header`. A removal (`! Name`) becomes its own `header -Name` directive, which Caddy defers until the response is written, so it wins over a value a broader block set: the `/*` and `/*.jpg` example in Cloudflare's documentation behaves the same in Caddy.
5. Before writing anything it checks the rules against each other. Two blocks that set the same header on paths that can both match are found by intersecting the two patterns exactly, not by comparing strings, and the refusal names an example path. It also finds a static redirect placed behind a dynamic one that matches it, and Netlify rules that a file in the build would shadow.
6. If any rule is refused, it reports every refusal, writes nothing and exits with status 1. With `-o`, the output file is replaced in one step and keeps its permissions, and only on success, so a refused run leaves the previous good file in place.

## Supported syntax

### `_headers`

| Construct | Example | Caddy output | Notes |
|---|---|---|---|
| Every request | `/*` | `header { ... }`, no matcher | |
| Exact path | `/favicon.ico` | `path_regexp ^/favicon\.ico$` | Case-sensitive |
| Splat | `/assets/*`, `/*.jpg`, `/files/*/raw` | `(.*)` | Cloudflare: one per path. Netlify: any number |
| Placeholder | `/movies/:title` | `([^/]+)` | Netlify: whole segments only |
| Splat or placeholder in a value | `x-movie-name: You are watching ":title"` | `{re.p2c_h21.1}` | Cloudflare only |
| Remove a header | `! Content-Security-Policy` | `header @m -Content-Security-Policy` | Cloudflare only |
| Value with `"` or `\` | `Link: </a>; rel="canonical"` | Backtick-quoted token | |
| Percent-encoded path | `/caf%C3%A9` | Decoded: `^/café$` | Caddy matches the decoded path |
| Host-level path | `https://preview.example.com/*` | Skipped and reported | Put these headers in that host's site block |
| Comments, tab or space indentation, CRLF, a byte order mark | | | |

### `_redirects`

| Construct | Example | Caddy output | Notes |
|---|---|---|---|
| Exact source | `/about-us /about 301` | `redir @p2c_r5 /about 301` | |
| Status | `301`, `302`, `303`, `307`, `308` | The same status | Netlify: `301` and `302` only. With none given: `302` on Cloudflare, `301` on Netlify |
| Splat | `/blog/* /news/:splat` | `(.*)` and `{re.p2c_r8.1}` | Cloudflare: one, anywhere. Netlify: one, at the end |
| Placeholders | `/news/:year/:month/:slug /news/:slug` | `([^/]+)` and `{re.p2c_r11.3}` | |
| Absolute destination | `/ext https://elsewhere.example.com/landing` | Kept as written | |
| Query string in the destination | `/q /?query=string` | Kept as written | Not with `--keep-query` |
| Force | `/a /b 301!` | A plain `redir`, which always applies | Netlify only |
| Request's query string passed on | | `{?query}` appended to the destination | Netlify always; Cloudflare with `--keep-query` |
| Trailing slash | `/blog` | `^/blog/?$` | Netlify only, since it matches with or without one. Cloudflare matches exactly |
| Host-level rule | `https://www.example.com/* https://example.com/:splat 301` | Skipped and reported | Write it as a Caddy site block |
| A rule that can never apply | A second `/a`, or `/blog/x` after `/blog/*` on Netlify | Kept, with a note | |

## What it refuses, and why

Every refusal names the file and line, says what was found and why it cannot be translated, and where there is one, gives the fix.

### In `_headers`

| Refused | Why |
|---|---|
| The same header set by two blocks whose paths can both match | Cloudflare says "the values are joined with a comma separator"; Caddy would send one value. Set the header in one block |
| The same header twice in one block | Netlify documents joining them as `a,b`; Cloudflare says only "a comma separator". Write the value once |
| A header set by a later block on paths where an earlier block removed it | Which one applies is not documented |
| `! Name` with `--platform netlify` | Netlify does not document header removal |
| A placeholder used in a value, with `--platform netlify` | Netlify does not document whether it substitutes placeholders in values |
| A placeholder used twice in one block, on Cloudflare | Cloudflare documents that a placeholder "can only be referenced once" |
| Braces in a value, such as a JSON `Report-To` | Caddy reads `{...}` as a placeholder, and this version does not escape braces |
| An empty value | What the platforms do with it is not documented |
| A header name Caddy would misread: `defer`, `match`, or one starting with `-`, `+`, `?` or `>` | Caddy's `header` directive reads these as its own options and operators |
| A value with a backtick and also a quote or backslash | It cannot be written as one Caddyfile token |
| More than 100 header rules, or a line over 2,000 characters, on Cloudflare | Cloudflare's documented limits |

### In `_redirects`

| Refused | Why |
|---|---|
| Status `200` (a rewrite or proxy) and `404` (a custom not-found page) | This version translates redirects only |
| A status the platform does not support; on Netlify also `307`, `303` and `308` | Netlify documents `307` as unsupported, and documents passing the query string on only for `301` and `302` |
| `!` on Cloudflare | Netlify syntax that Cloudflare Pages does not document; Pages applies a redirect even when a file exists |
| A Netlify rule without `!`, with no `--site-dir` or with a file at its path | Netlify serves the file instead; Caddy's `redir` always applies |
| Query-parameter matching, such as `/shop id=:id /blog/:id` | Not supported on Cloudflare; Netlify's handling of extra or missing parameters is not reproduced |
| Conditions: `Country=`, `Language=`, `Role=`, `Cookie=` | They depend on the platform's own request data |
| A static rule after a dynamic rule that matches it, on Cloudflare | Cloudflare says "Static redirects should appear before dynamic redirects" and does not say which applies otherwise. Move the static rule up |
| A destination that is relative, protocol-relative (`//host`), or contains `*`, quotes, backslashes or braces | No faithful Caddy form, or a `*` that is not a reference (use `:splat`) |
| `:name` in a destination that the source does not define, or `:splat` with no `*` | Nothing to substitute |
| A placeholder used twice in a destination, on Cloudflare | "Every named placeholder can only be referenced once" |
| `--keep-query` (or Netlify) with a destination that has its own query string or fragment | How the two combine is not documented |
| More than 2,000 static or 100 dynamic redirects, or a rule over 1,000 characters, on Cloudflare | Cloudflare's documented limits |

### In any path

Two splats on Cloudflare; a splat that is not at the end of a Netlify redirect source; a placeholder that appears twice, is named `:splat`, or on Netlify is not a whole path segment; and a path with `?`, `#`, `//`, `%2F`, whitespace (including `%20`), quotes, a backslash, braces or a control character.

## Netlify

`--platform netlify` applies Netlify's documented differences: `301` when no status is given, matching with or without a trailing slash, the request's query string passed on to the destination, and `!` to force a rule.

A Netlify rule without `!` applies only when no file exists at the path, while Caddy's `redir` always applies. To translate such a rule, pass the built site with `--site-dir`. The tool lists every path a file could be served at (`a/b.html` as `/a/b.html` and `/a/b`; `a/index.html` also as `/a/` and `/a`) and refuses the rule if any of them matches. When none does, Netlify and Caddy agree for that build, so run the tool again after every build, as part of the deploy.

## Limits

- **Query strings on Cloudflare.** Cloudflare's redirects documentation does not say whether a request's query string is carried on to the destination. By default the output sends exactly the destination you wrote. Check one of your redirects on Pages (`curl -sI 'https://example.com/old?x=1'`) and pass `--keep-query` if the query string is kept.
- **Headers the platform adds by itself**, with no line in `_headers`, are not in the file and so are not translated. Compare a response from the platform with one from Caddy.
- **Redirected responses carry none of the translated headers** (see [How it works](#how-it-works)). Put any header you want on every response, redirects included, in your site block outside the import.
- **Other handlers.** Headers are set before Caddy serves the file. If another handler in your site sets the same header later, its value may replace yours. Check the headers you rely on with `curl` against Caddy.
- **Case.** Matching is case-sensitive. Netlify documents its paths as case-sensitive; Cloudflare's documentation does not say.
- **Percent-encoding.** Patterns are decoded before matching, because Caddy matches the decoded path. Text captured by a splat or placeholder is the decoded form too.
- **Empty placeholders.** A placeholder matches one or more characters other than `/`. Cloudflare's documentation says placeholders "match all characters apart from the delimiter" and does not say whether an empty match counts.
- **Netlify details.** With trailing-slash matching, `/news/*` does not match `/news` itself. Header wildcards are translated as matching any characters, `/` included. Rules in `netlify.toml` are not read, and Netlify's Pretty URLs redirects are its own and are not reproduced.
- **Rewrites, proxies and custom 404 pages** are refused, not translated, in this version.
- **Host-level rules** are skipped and reported. Write them as Caddy site blocks, for example:

  ```caddyfile
  www.example.com {
  	redir https://example.com{uri} 301
  }
  ```

- **Caddy versions.** The output uses `route`, `path_regexp`, `redir`, `header` and the `{?query}` placeholder. The CI workflow installs Caddy 2.11.9 and runs `caddy adapt` and `caddy validate` on every fixture's output; no other version is checked.

## Worked example

This is a real run on the files in [`examples/basic`](examples/basic). A test checks that the output below is what the tool produces today.

`examples/basic/_headers`:

```text
# Security headers for every response
/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Strict-Transport-Security: max-age=63072000; includeSubDomains
  Content-Security-Policy: default-src 'self'; img-src 'self' data:; base-uri 'self'

# Fingerprinted assets never change
/assets/*
  Cache-Control: public, max-age=31536000, immutable

# A build-time image written without a file extension
/social-card
  Content-Type: image/png

# Partners may frame the embed pages
/embed/*
  ! X-Frame-Options

# Offer every download under its own name
/downloads/:file
  Content-Disposition: attachment; filename=":file"
```

`examples/basic/_redirects`:

```text
# Host-level: Caddy does this in a site block
https://www.example.com/*  https://example.com/:splat  301

# A page that moved
/about-us                  /about                      301

# A section that moved, with everything under it
/blog/*                    /news/:splat                301

# Dated addresses to flat ones
/news/:year/:month/:slug   /news/:slug                 308

# No status: Cloudflare Pages' default is 302
/sale                      /offers
```

Run:

```console
$ python3 pages_to_caddy.py examples/basic -o examples/basic/pages-rules.caddy
examples/basic/_redirects:2: skipped: host-level rule https://www.example.com/*; write it as a Caddy site block for that host
pages-to-caddy: wrote examples/basic/pages-rules.caddy: 5 header blocks and 4 redirects; skipped 1 host-level rule
```

`examples/basic/pages-rules.caddy`:

```caddyfile
# Generated by pages-to-caddy 0.1.0 from _headers and _redirects
# (Cloudflare Pages rules). Edit the source files and generate again.
route {
	# _redirects line 2 skipped (host-level): https://www.example.com/* https://example.com/:splat 301

	# _redirects line 5: /about-us /about 301
	@p2c_r5 path_regexp p2c_r5 ^/about-us$
	redir @p2c_r5 /about 301

	# _redirects line 8: /blog/* /news/:splat 301
	@p2c_r8 path_regexp p2c_r8 ^/blog/(.*)$
	redir @p2c_r8 /news/{re.p2c_r8.1} 301

	# _redirects line 11: /news/:year/:month/:slug /news/:slug 308
	@p2c_r11 path_regexp p2c_r11 ^/news/([^/]+)/([^/]+)/([^/]+)$
	redir @p2c_r11 /news/{re.p2c_r11.3} 308

	# _redirects line 14: /sale /offers
	@p2c_r14 path_regexp p2c_r14 ^/sale$
	redir @p2c_r14 /offers 302

	# _headers line 2: /*
	header {
		X-Frame-Options "DENY"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		Strict-Transport-Security "max-age=63072000; includeSubDomains"
		Content-Security-Policy "default-src 'self'; img-src 'self' data:; base-uri 'self'"
	}

	# _headers line 10: /assets/*
	@p2c_h10 path_regexp p2c_h10 ^/assets/(.*)$
	header @p2c_h10 Cache-Control "public, max-age=31536000, immutable"

	# _headers line 14: /social-card
	@p2c_h14 path_regexp p2c_h14 ^/social-card$
	header @p2c_h14 Content-Type "image/png"

	# _headers line 18: /embed/*
	@p2c_h18 path_regexp p2c_h18 ^/embed/(.*)$
	header @p2c_h18 -X-Frame-Options

	# _headers line 22: /downloads/:file
	@p2c_h22 path_regexp p2c_h22 ^/downloads/([^/]+)$
	header @p2c_h22 Content-Disposition `attachment; filename="{re.p2c_h22.1}"`
}
```

Things to notice:

- The `www` rule is host-level, so it is reported and left to a Caddy site block.
- `/sale` names no status, so it gets Cloudflare's default, `302`.
- `/embed/*` removes `X-Frame-Options` with a deferred `header -X-Frame-Options`, so it wins over the value `/*` sets.
- The `Content-Disposition` value contains quotes, so it is written as a backtick token, and `:file` becomes the capture from `/downloads/:file`.

### A file it refuses

[`examples/refused`](examples/refused) holds five rules that cannot be translated faithfully. `_headers`:

```text
/*
  Cache-Control: public, max-age=300

/assets/*
  Cache-Control: public, max-age=31536000, immutable
```

`_redirects`:

```text
/shop/*        /store/:splat   200
/old-page      /new-page       301!
/search q=:q   /find/:q        301
/blog/*        /news/:splat    301
/blog/archive  /news/archive   301
```

```console
$ python3 pages_to_caddy.py --check examples/refused
examples/refused/_headers:5: refused: Cache-Control is also set on line 2 (path /*), and both paths match /assets/; Cloudflare Pages joins the two values with a comma, and Caddy would send one. Set Cache-Control in one block only
examples/refused/_redirects:1: refused: status 200 is a rewrite (a proxy), not a redirect; this version translates redirects only
examples/refused/_redirects:2: refused: the ! suffix is Netlify syntax and Cloudflare Pages does not document it; remove it (Pages applies a redirect even when a file exists at the path) or use --platform netlify
examples/refused/_redirects:3: refused: the rule matches query parameters (q=:q); this is not translated (Cloudflare Pages does not support it, and Netlify's matching of extra or missing parameters is not reproduced)
examples/refused/_redirects:5: refused: this static rule comes after the dynamic rule on line 4, which also matches /blog/archive; Cloudflare Pages says static redirects should come before dynamic ones and does not say which applies here, so move this line above line 4
pages-to-caddy: refused 5 rules; nothing was written
```

The exit status is 1 and nothing is written. The first refusal is the common case: on Cloudflare Pages, `/assets/app.css` gets both values joined into one `Cache-Control` header, which is rarely what was meant, and Caddy would send only one of them. Setting the short cache only on the paths that need it gives one intended value on both hosts.

## Options

```text
usage: pages-to-caddy [-h] [--headers FILE] [--redirects FILE]
                      [--platform {cloudflare,netlify}] [--keep-query]
                      [--site-dir DIR] [--check | -o FILE] [--version]
                      [dir]

Translate Cloudflare Pages or Netlify _headers and _redirects files into
Caddyfile directives. Any rule that cannot be translated faithfully is
refused, with its line, and nothing is written.

positional arguments:
  dir                   directory holding _headers and/or _redirects (default:
                        .)

options:
  -h, --help            show this help message and exit
  --headers FILE        read this _headers file instead of DIR/_headers
  --redirects FILE      read this _redirects file instead of DIR/_redirects
  --platform {cloudflare,netlify}
                        whose rules the files follow; sets the default status
                        and other differences (default: cloudflare)
  --keep-query          carry the request's query string on to each redirect's
                        destination (always on for netlify)
  --site-dir DIR        the built site, used to check that no file shadows a
                        Netlify rule without !
  --check               validate only; write nothing
  -o, --output FILE     write to FILE (replaced atomically) instead of stdout
  --version             show program's version number and exit

Exit status: 0 translated, 1 one or more rules refused, 2 usage or file error.
```

## In a deploy

- Exit status `0` means everything was translated, `1` means at least one rule was refused (and nothing was written), and `2` means a usage or file error.
- Run with `--check` on pull requests, so a rule that cannot be translated fails review rather than the deploy.
- In the deploy, generate with `-o`, then run `caddy validate` before reloading Caddy. Generate again from the same files on every deploy; the output is deterministic, so an unchanged source gives an unchanged file.
- Notes and skipped rules go to standard error and do not change the exit status.

## Tests

```sh
python3 -m unittest -v
```

The tests are offline and need nothing beyond Python. Unit tests and the golden fixtures in [`tests/fixtures`](tests/fixtures) cover every translated construct, and each refusal has its own test. The tests in `tests/test_caddy.py` also run `caddy adapt` and `caddy validate` on the output when `caddy` is on `PATH`, and are skipped otherwise; set `PAGES_TO_CADDY_REQUIRE_CADDY=1` to make a missing `caddy` a failure, as the CI job that installs Caddy does.

## Sources

Platform behaviour is taken from the platforms' own documentation, read on 2026-11-10, and quoted above where it decides a translation or a refusal:

- Cloudflare Pages: [Redirects](https://developers.cloudflare.com/pages/configuration/redirects/) and [Headers](https://developers.cloudflare.com/pages/configuration/headers/)
- Netlify: [Redirects and rewrites](https://docs.netlify.com/routing/redirects/), [Redirect options](https://docs.netlify.com/manage/routing/redirects/redirect-options/), [Rewrites and proxies](https://docs.netlify.com/manage/routing/redirects/rewrites-proxies/) and [Custom headers](https://docs.netlify.com/routing/headers/)
- Caddy: [Request matchers](https://caddyserver.com/docs/caddyfile/matchers), [header](https://caddyserver.com/docs/caddyfile/directives/header), [redir](https://caddyserver.com/docs/caddyfile/directives/redir), [route](https://caddyserver.com/docs/caddyfile/directives/route) and [Caddyfile concepts](https://caddyserver.com/docs/caddyfile/concepts)

If a platform changes its behaviour, please open an issue with a link to the change.

## Contributing, security and licence

See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately, as [SECURITY.md](SECURITY.md) describes, not in a public issue. The code is released under the MIT licence; see [LICENSE](LICENSE).

Maintained by [TicketWave HQ](https://ticketwavehq.com), a UK company (Companies House 17143167).
