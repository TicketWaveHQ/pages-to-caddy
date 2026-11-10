"""_headers: every construct translated, and every construct refused."""

from __future__ import annotations

import unittest

from tests.support import Case, p2c


class CloudflareHeaders(Case):
    platform = p2c.CLOUDFLARE

    # translated -----------------------------------------------------------

    def test_global_block_needs_no_matcher(self):
        out = self.translated(
            """
            /*
              X-Frame-Options: DENY
              X-Content-Type-Options: nosniff
            """
        )
        self.assertIn('\theader {\n\t\tX-Frame-Options "DENY"\n\t\tX-Content-Type-Options "nosniff"\n\t}', out)
        self.assertNotIn("path_regexp", out)

    def test_exact_path_is_anchored_and_case_sensitive(self):
        out = self.translated(
            """
            /favicon.ico
              Cache-Control: public, max-age=86400
            """
        )
        self.assertIn("@p2c_h1 path_regexp p2c_h1 ^/favicon\\.ico$", out)
        self.assertIn('header @p2c_h1 Cache-Control "public, max-age=86400"', out)
        self.assertNotIn("(?i)", out)

    def test_trailing_splat(self):
        out = self.translated("/assets/*\n  Cache-Control: immutable\n")
        self.assertIn("path_regexp p2c_h1 ^/assets/(.*)$", out)

    def test_leading_and_middle_splat(self):
        out = self.translated("/*.jpg\n  X-A: 1\n/files/*/raw\n  X-B: 2\n")
        self.assertIn("^/(.*)\\.jpg$", out)
        self.assertIn("^/files/(.*)/raw$", out)

    def test_placeholder_in_path_and_value(self):
        out = self.translated(
            """
            /movies/:title
              x-movie-name: You are watching ":title"
            """
        )
        self.assertIn("^/movies/([^/]+)$", out)
        self.assertIn('header @p2c_h1 x-movie-name `You are watching "{re.p2c_h1.1}"`', out)

    def test_splat_in_value_gives_global_path_a_matcher(self):
        out = self.translated("/*\n  Link: </mirror/:splat>; rel=canonical\n")
        self.assertIn("@p2c_h1 path_regexp p2c_h1 ^/(.*)$", out)
        self.assertIn('header @p2c_h1 Link "</mirror/{re.p2c_h1.1}>; rel=canonical"', out)

    def test_unknown_colon_word_in_value_stays_literal(self):
        out = self.translated("/a/:id\n  Content-Security-Policy: img-src https: data:\n  X-Note: see :other\n")
        self.assertIn('Content-Security-Policy "img-src https: data:"', out)
        self.assertIn('X-Note "see :other"', out)

    def test_removal_is_a_separate_deferred_directive(self):
        out = self.translated(
            """
            /*
              Content-Security-Policy: default-src 'self';

            /*.jpg
              ! Content-Security-Policy
            """
        )
        self.assertIn("\theader @p2c_h4 -Content-Security-Policy\n", out)

    def test_removal_and_set_in_one_block_are_separate_directives(self):
        out = self.translated("/*\n  X-A: 1\n/embed/*\n  ! X-A\n  X-B: 2\n")
        self.assertIn('\theader @p2c_h3 X-B "2"\n\theader @p2c_h3 -X-A\n', out)

    def test_values_with_quotes_or_backslashes_use_backticks(self):
        out = self.translated('/a\n  X-Quote: say "hi"\n  X-Slash: a\\b\n')
        self.assertIn('X-Quote `say "hi"`', out)
        self.assertIn("X-Slash `a\\b`", out)

    def test_same_header_on_disjoint_paths_is_allowed(self):
        out = self.translated("/\n  Cache-Control: short\n/assets/*\n  Cache-Control: long\n")
        self.assertIn('header @p2c_h1 Cache-Control "short"', out)
        self.assertIn('header @p2c_h3 Cache-Control "long"', out)

    def test_same_path_twice_with_different_headers_is_allowed(self):
        out = self.translated("/a\n  X-A: 1\n/a\n  X-B: 2\n")
        self.assertIn('X-A "1"', out)
        self.assertIn('X-B "2"', out)

    def test_set_then_remove_on_overlapping_paths_is_allowed(self):
        self.translated("/*\n  X-A: 1\n/private/*\n  ! X-A\n")

    def test_percent_encoded_path_is_decoded(self):
        out = self.translated("/caf%C3%A9\n  Content-Language: fr\n")
        self.assertIn("^/caf\u00e9$", out)

    def test_comments_tabs_bom_and_crlf(self):
        out = self.translated("\ufeff# top\r\n/a\r\n\t# inside\r\n\tX-A: 1\r\n\r\n")
        self.assertIn('header @p2c_h2 X-A "1"', out)

    def test_header_name_case_is_kept(self):
        out = self.translated("/a\n  x-lower-case: v\n")
        self.assertIn('x-lower-case "v"', out)

    def test_host_level_path_is_skipped_and_reported(self):
        result = self.run_translate("https://preview.example.com/*\n  X-Robots-Tag: noindex\n/a\n  X-A: 1\n")
        self.assertTrue(result.ok)
        skipped = self.messages(result, "skipped")
        self.assertEqual([m.line for m in skipped], [1])
        self.assertIn("host-level", skipped[0].text)
        self.assertNotIn("noindex", result.caddyfile)
        self.assertIn("# _headers line 1 skipped (host-level): https://preview.example.com/*", result.caddyfile)
        self.assertEqual(result.skipped, 1)

    def test_block_without_headers_is_noted(self):
        result = self.run_translate("/empty\n/a\n  X-A: 1\n")
        self.assertTrue(result.ok)
        self.assertEqual([m.line for m in self.messages(result, "note")], [1])
        self.assertEqual(result.header_blocks, 1)

    def test_only_skipped_rules_gives_no_route(self):
        result = self.run_translate("https://preview.example.com/*\n  X-A: 1\n")
        self.assertTrue(result.ok)
        self.assertNotIn("route", result.caddyfile)
        self.assertIn("# No rules to translate.", result.caddyfile)

    def test_header_rule_limit_is_exactly_one_hundred(self):
        self.translated("".join(f"/p{i}\n  X-A: 1\n" for i in range(100)))

    # refused --------------------------------------------------------------

    def test_same_header_on_overlapping_paths(self):
        message = self.refused(
            """
            /*
              Cache-Control: public, max-age=300

            /assets/*
              Cache-Control: public, max-age=31536000, immutable
            """,
            line=5,
            contains="joins the two values with a comma",
        )
        self.assertIn("also set on line 2", message.text)
        self.assertIn("/assets/", message.text)

    def test_same_header_on_overlapping_patterns_names_an_example(self):
        message = self.refused("/blog/*\n  X-A: 1\n/*.css\n  X-A: 2\n", line=4, contains="match /blog/.css")
        self.assertIn("X-A", message.text)

    def test_overlap_is_case_insensitive_on_header_names(self):
        self.refused("/*\n  x-a: 1\n/a\n  X-A: 2\n", line=4, contains="also set on line 2")

    def test_set_after_removal_on_overlapping_paths(self):
        self.refused("/*\n  ! X-A\n/a\n  X-A: 1\n", line=4, contains="removed on line 2")

    def test_header_set_twice_in_one_block(self):
        self.refused("/a\n  Cache-Control: no-cache\n  Cache-Control: no-store\n", line=3, contains="already set")

    def test_header_set_and_removed_in_one_block(self):
        self.refused("/a\n  X-A: 1\n  ! X-A\n", line=3, contains="set on line 2 and removed here")

    def test_header_removed_twice_in_one_block(self):
        self.refused("/a\n  ! X-A\n  ! X-A\n", line=3, contains="already removed")

    def test_indented_line_before_any_path(self):
        self.refused("  X-A: 1\n/a\n  X-B: 2\n", line=1, contains="no path line comes before it")

    def test_line_without_colon(self):
        self.refused("/a\n  just some words\n", line=2, contains="expected 'Name: value'")

    def test_path_not_starting_with_slash(self):
        self.refused("assets/*\n  X-A: 1\n", line=1, contains="does not start with /")

    def test_path_line_with_two_fields(self):
        self.refused("/a /b\n  X-A: 1\n", line=1, contains="holds one path")

    def test_two_splats(self):
        self.refused("/*/print/*\n  X-A: 1\n", line=1, contains="allows one *")

    def test_duplicate_placeholder(self):
        self.refused("/:a/:a\n  X-A: 1\n", line=1, contains="appears twice")

    def test_placeholder_named_splat(self):
        self.refused("/:splat\n  X-A: 1\n", line=1, contains="reserved")

    def test_placeholder_referenced_twice(self):
        self.refused("/a/:id\n  X-A: :id\n  X-B: :id\n", line=3, contains="referenced once")

    def test_invalid_header_name(self):
        self.refused("/a\n  Bad Name: 1\n", line=2, contains="not a header name")

    def test_header_name_starting_with_an_operator(self):
        self.refused("/a\n  -X-A: 1\n", line=2, contains="not a header name")

    def test_header_name_that_caddy_reads_as_an_option(self):
        self.refused("/a\n  Defer: 1\n", line=2, contains="Caddy's own 'defer'")

    def test_malformed_removal(self):
        self.refused("/a\n  ! X-A: 1\n", line=2, contains="expected '! Header-Name'")

    def test_empty_value(self):
        self.refused("/a\n  X-A:\n", line=2, contains="empty value")

    def test_braces_in_value(self):
        self.refused('/a\n  Report-To: {"group":"csp"}\n', line=2, contains="placeholder")

    def test_backtick_with_quote_in_value(self):
        self.refused('/a\n  X-A: `a` "b"\n', line=2, contains="backtick")

    def test_control_character_in_value(self):
        self.refused("/a\n  X-A: a\x01b\n", line=2, contains="control character")

    def test_query_string_in_path(self):
        self.refused("/a?x=1\n  X-A: 1\n", line=1, contains="query string")

    def test_fragment_in_path(self):
        self.refused("/a#top\n  X-A: 1\n", line=1, contains="fragment")

    def test_empty_segment(self):
        self.refused("/a//b\n  X-A: 1\n", line=1, contains="empty segment")

    def test_encoded_slash(self):
        self.refused("/a%2Fb\n  X-A: 1\n", line=1, contains="%2F")

    def test_encoded_space(self):
        self.refused("/my%20file.pdf\n  X-A: 1\n", line=1, contains="' '")

    def test_brace_in_path(self):
        self.refused("/a{b}\n  X-A: 1\n", line=1, contains="'{'")

    def test_line_longer_than_cloudflare_allows(self):
        self.refused("/a\n  X-A: " + "v" * 2000 + "\n", line=2, contains="2000 per line")

    def test_more_header_rules_than_cloudflare_allows(self):
        self.refused("".join(f"/p{i}\n  X-A: 1\n" for i in range(101)), line=201, contains="allows 100")

    def test_every_refusal_is_reported_at_once(self):
        result = self.run_translate("/a\n  Bad Name: 1\n/b\n  X-A:\nnope\n  X-A: 1\n")
        self.assertEqual([m.line for m in result.refused], [2, 4, 5])
        self.assertEqual(result.caddyfile, "")


class NetlifyHeaders(Case):
    platform = p2c.NETLIFY

    def test_wildcards_anywhere_and_more_than_one(self):
        out = self.translated("/images/*.png\n  X-A: 1\n/*/print/*\n  X-B: 2\n")
        self.assertIn("^/images/(.*)\\.png$", out)
        self.assertIn("^/(.*)/print/(.*)$", out)

    def test_placeholder_as_whole_segment(self):
        out = self.translated("/docs/:page\n  X-A: 1\n")
        self.assertIn("^/docs/([^/]+)$", out)

    def test_value_with_undefined_colon_word_is_literal(self):
        out = self.translated("/docs/:page\n  X-A: see :other\n")
        self.assertIn('X-A "see :other"', out)

    def test_removal_is_refused(self):
        self.refused("/*\n  X-A: 1\n/a\n  ! X-A\n", line=4, contains="Netlify does not document it")

    def test_placeholder_reference_in_value_is_refused(self):
        self.refused("/docs/:page\n  X-A: :page\n", line=2, contains="does not document whether it substitutes")

    def test_placeholder_inside_a_segment_is_refused(self):
        self.refused("/docs/v:page\n  X-A: 1\n", line=1, contains="not a whole path segment")

    def test_placeholder_and_wildcard_in_one_segment_is_refused(self):
        self.refused("/templates/:placeholder*\n  X-A: 1\n", line=1, contains="not a whole path segment")
        self.refused("/templates/*:placeholder\n  X-A: 1\n", line=1, contains="not a whole path segment")

    def test_repeated_header_in_block_is_refused(self):
        self.refused("/*\n  cache-control: max-age=0\n  cache-control: no-cache\n", line=3, contains="already set")

    def test_overlap_message_does_not_claim_netlify_behaviour(self):
        message = self.refused("/*\n  X-A: 1\n/a\n  X-A: 2\n", line=4, contains="does not reproduce how Netlify")
        self.assertNotIn("comma", message.text)

    def test_cloudflare_limits_do_not_apply(self):
        self.translated("".join(f"/p{i}\n  X-A: 1\n" for i in range(101)))


if __name__ == "__main__":
    unittest.main()
