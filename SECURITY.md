# Security policy

## Reporting a problem

Please report security problems privately to **hello@ticketwavehq.com**, not in a public issue. Our disclosure policy is at <https://ticketwavehq.com/security>.

A useful report includes the version (`python3 pages_to_caddy.py --version`), the smallest `_headers` or `_redirects` that shows the problem, the command you ran, the output you got and the output you expected.

## What counts

This tool writes web server configuration, so a translation that differs from its source is a security problem when it is weaker than the source. For example:

- a header from the source that is missing from the output, or applied to fewer paths;
- a redirect that matches more, or fewer, paths than its source rule;
- output that differs from the source where the tool should have refused;
- rule text that ends up as Caddyfile structure (a new directive, matcher or placeholder) rather than as a value.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |
