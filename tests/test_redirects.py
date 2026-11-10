"""_redirects: every construct translated, and every construct refused."""

from __future__ import annotations

import re
import unittest

from tests.support import Case, p2c


class CloudflareRedirects(Case):
    platform = p2c.CLOUDFLARE

    # translated -----------------------------------------------------------

    def test_exact_path(self):
        out = self.translated(redirects="/old /new 301\n")
        self.assertIn("\t@p2c_r1 path_regexp p2c_r1 ^/old$\n\tredir @p2c_r1 /new 301\n", out)

    def test_default_status_is_302(self):
        self.assertIn("redir @p2c_r1 /new 302", self.translated(redirects="/old /new\n"))

    def test_every_documented_status(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertIn(f"redir @p2c_r1 /b {status}", self.translated(redirects=f"/a /b {status}\n"))

    def test_trailing_splat(self):
        out = self.translated(redirects="/blog/* /news/:splat 301\n")
        self.assertIn("path_regexp p2c_r1 ^/blog/(.*)$", out)
        self.assertIn("redir @p2c_r1 /news/{re.p2c_r1.1} 301", out)

    def test_splat_in_the_middle(self):
        out = self.translated(redirects="/docs/*/index.html /docs/:splat 301\n")
        self.assertIn("^/docs/(.*)/index\\.html$", out)

    def test_placeholders_in_any_order(self):
        out = self.translated(redirects="/news/:year/:month/:slug /:slug/:year 301\n")
        self.assertIn("^/news/([^/]+)/([^/]+)/([^/]+)$", out)
        self.assertIn("redir @p2c_r1 /{re.p2c_r1.3}/{re.p2c_r1.1} 301", out)

    def test_placeholder_inside_a_segment(self):
        out = self.translated(redirects="/v:version/docs /docs/:version 301\n")
        self.assertIn("^/v([^/]+)/docs$", out)

    def test_unused_placeholder_is_fine(self):
        self.translated(redirects="/a/:x /b 301\n")

    def test_absolute_destination(self):
        out = self.translated(redirects="/ext/* https://elsewhere.example.com/:splat 301\n")
        self.assertIn("redir @p2c_r1 https://elsewhere.example.com/{re.p2c_r1.1} 301", out)

    def test_destination_with_port_is_not_a_placeholder(self):
        out = self.translated(redirects="/a http://elsewhere.example.com:8080/b 302\n")
        self.assertIn("redir @p2c_r1 http://elsewhere.example.com:8080/b 302", out)

    def test_destination_query_string_is_kept_as_written(self):
        out = self.translated(redirects="/querystrings /?query=string 301\n")
        self.assertIn("redir @p2c_r1 /?query=string 301", out)

    def test_destination_fragment_is_kept(self):
        out = self.translated(redirects="/faq /help#faq 301\n")
        self.assertIn("redir @p2c_r1 /help#faq 301", out)

    def test_trailing_slashes_are_distinct(self):
        out = self.translated(redirects="/trailing /trailing/ 301\n/notrailing/ /notrailing 301\n")
        self.assertIn("^/trailing$", out)
        self.assertIn("^/notrailing/$", out)

    def test_keep_query(self):
        out = self.translated(redirects="/a /b 301\n/c/* https://elsewhere.example.com/:splat 302\n", keep_query=True)
        self.assertIn("redir @p2c_r1 /b{?query} 301", out)
        self.assertIn("redir @p2c_r2 https://elsewhere.example.com/{re.p2c_r2.1}{?query} 302", out)

    def test_order_is_kept_inside_one_route(self):
        out = self.translated(redirects="/a/* /x 301\n/b /y 301\n/a/deeper/* /z 301\n")
        self.assertEqual(out.count("route {"), 1)
        lines = [m.group(1) for m in re.finditer(r"redir @p2c_r(\d+)", out)]
        self.assertEqual(lines, ["1", "2", "3"])

    def test_redirects_come_before_headers(self):
        out = self.translated("/*\n  X-A: 1\n", "/a /b 301\n")
        self.assertLess(out.index("\tredir "), out.index("\theader "))

    def test_duplicate_source_is_noted_and_kept(self):
        result = self.run_translate(redirects="/a /b 301\n/a /c 301\n")
        self.assertTrue(result.ok)
        notes = self.messages(result, "note")
        self.assertEqual([m.line for m in notes], [2])
        self.assertIn("never applies", notes[0].text)

    def test_static_before_a_dynamic_rule_that_matches_it_is_fine(self):
        self.translated(redirects="/blog/archive /news/archive 301\n/blog/* /news/:splat 301\n")

    def test_host_level_rules_are_skipped_and_reported(self):
        result = self.run_translate(
            redirects=(
                "https://www.example.com/* https://example.com/:splat 301\n"
                "http://example.com/* https://example.com/:splat 301\n"
                "workers.example.com/* https://example.com/blog/:splat 301\n"
                "/a /b 301\n"
            )
        )
        self.assertTrue(result.ok)
        self.assertEqual([m.line for m in self.messages(result, "skipped")], [1, 2, 3])
        self.assertEqual(result.skipped, 3)
        self.assertEqual(result.redirects, 1)
        self.assertEqual(result.caddyfile.count("skipped (host-level)"), 3)

    def test_host_level_rule_with_force_is_skipped_not_refused(self):
        result = self.run_translate(redirects="https://www.example.com/* https://example.com/:splat 301!\n")
        self.assertTrue(result.ok)

    def test_comments_blank_lines_and_crlf(self):
        out = self.translated(redirects="# moved\r\n\r\n   /a   /b   301   \r\n")
        self.assertIn("# _redirects line 3: /a /b 301", out)

    def test_redirect_limits_are_exact(self):
        self.translated(redirects="".join(f"/d{i}/* /x/:splat 301\n" for i in range(100)))
        self.translated(redirects="/" + "a" * 992 + " /b 301\n")  # 1,000 characters

    # refused --------------------------------------------------------------

    def test_status_200(self):
        self.refused(redirects="/shop/* /store/:splat 200\n", line=1, contains="rewrite")

    def test_status_404(self):
        self.refused(redirects="/gone /404.html 404\n", line=1, contains="not-found page")

    def test_other_status(self):
        self.refused(redirects="/a /b 410\n", line=1, contains="not a redirect Cloudflare Pages supports")

    def test_status_not_three_digits(self):
        self.refused(redirects="/a /b 3011\n", line=1, contains="three-digit")

    def test_force_suffix(self):
        self.refused(redirects="/a /b 301!\n", line=1, contains="Netlify syntax")

    def test_query_parameter_matching(self):
        self.refused(redirects="/shop id=:id /blog/:id 301\n", line=1, contains="query parameters (id=:id)")

    def test_conditions(self):
        self.refused(redirects="/ /us 302 Country=us\n", line=1, contains="conditions (Country=us)")
        self.refused(redirects="/* /preview/:splat 302 Cookie=preview\n", line=1, contains="conditions")

    def test_unexpected_fields(self):
        self.refused(redirects="/a /b 301 # note\n", line=1, contains="unexpected fields")

    def test_missing_destination(self):
        self.refused(redirects="/a\n", line=1, contains="no destination")

    def test_status_where_the_destination_should_be(self):
        self.refused(redirects="/a 301\n", line=1, contains="not a path starting with /")

    def test_relative_destination(self):
        self.refused(redirects="/a b.html 301\n", line=1, contains="not a path starting with /")

    def test_protocol_relative_destination(self):
        self.refused(redirects="/a //elsewhere.example.com/b 301\n", line=1, contains="protocol-relative")

    def test_braces_in_destination(self):
        self.refused(redirects="/a /b{uri} 301\n", line=1, contains="'{'")

    def test_star_in_destination(self):
        self.refused(redirects="/a /b/* 301\n", line=1, contains="refer to the matched text with :splat")

    def test_undefined_placeholder_in_destination(self):
        self.refused(redirects="/a/:x /b/:y 301\n", line=1, contains="defines no such placeholder")

    def test_splat_reference_without_a_splat(self):
        self.refused(redirects="/a /b/:splat 301\n", line=1, contains="has no *")

    def test_placeholder_referenced_twice(self):
        self.refused(redirects="/a/:x /b/:x/:x 301\n", line=1, contains="referenced once")

    def test_duplicate_placeholder_in_source(self):
        self.refused(redirects="/:a/:a /b 301\n", line=1, contains="appears twice")

    def test_two_splats(self):
        self.refused(redirects="/*/a/* /b 301\n", line=1, contains="allows one *")

    def test_source_not_starting_with_slash(self):
        self.refused(redirects="news /blog 301\n", line=1, contains="does not start with /")

    def test_query_string_in_source(self):
        self.refused(redirects="/a?x=1 /b 301\n", line=1, contains="query string")

    def test_keep_query_with_own_query_string(self):
        self.refused(redirects="/a /b?x=1 301\n", line=1, contains="--keep-query is set", keep_query=True)

    def test_keep_query_with_fragment(self):
        self.refused(redirects="/a /b#top 301\n", line=1, contains="fragment", keep_query=True)

    def test_static_after_a_dynamic_rule_that_matches_it(self):
        message = self.refused(
            redirects="/blog/* /news/:splat 301\n/blog/archive /news/archive 301\n",
            line=2,
            contains="static redirects should come before dynamic ones",
        )
        self.assertIn("move this line above line 1", message.text)

    def test_line_longer_than_cloudflare_allows(self):
        self.refused(redirects="/" + "a" * 993 + " /b 301\n", line=1, contains="allows 1000")

    def test_more_dynamic_redirects_than_cloudflare_allows(self):
        rules = "".join(f"/d{i}/* /x/:splat 301\n" for i in range(101))
        self.refused(redirects=rules, line=101, contains="allows 100")

    def test_more_static_redirects_than_cloudflare_allows(self):
        rules = "".join(f"/s{i} /x 301\n" for i in range(2001))
        self.refused(redirects=rules, line=2001, contains="allows 2000")

    def test_every_refusal_is_reported_at_once(self):
        result = self.run_translate(redirects="/a /b 200\n/ok /fine 301\n/c /d 301!\n/e\n")
        self.assertEqual([m.line for m in result.refused], [1, 3, 4])
        self.assertEqual(result.caddyfile, "")


class NetlifyRedirects(Case):
    platform = p2c.NETLIFY
    site = {"/": "index.html", "/index.html": "index.html", "/docs/": "docs/index.html",
            "/docs": "docs/index.html", "/docs/index.html": "docs/index.html",
            "/about": "about.html", "/about.html": "about.html"}

    def translated(self, headers=None, redirects=None, **options):
        options.setdefault("site_paths", self.site)
        return super().translated(headers, redirects, **options)

    def refused(self, headers=None, redirects=None, **options):
        options.setdefault("site_paths", self.site)
        return super().refused(headers, redirects, **options)

    # translated -----------------------------------------------------------

    def test_default_status_is_301_and_query_is_passed_on(self):
        out = self.translated(redirects="/old /new\n")
        self.assertIn("redir @p2c_r1 /new{?query} 301", out)

    def test_302(self):
        self.assertIn("redir @p2c_r1 /new{?query} 302", self.translated(redirects="/old /new 302\n"))

    def test_trailing_slash_is_optional(self):
        out = self.translated(redirects="/old /new\n/gone/ /new\n/ /home 301!\n")
        self.assertIn("^/old/?$", out)
        self.assertIn("^/gone/?$", out)
        self.assertIn("path_regexp p2c_r3 ^/$", out)

    def test_trailing_splat_is_lazy_before_an_optional_slash(self):
        out = self.translated(redirects="/news/* /blog/:splat\n")
        self.assertIn("^/news/(.*?)/?$", out)
        self.assertIsNotNone(re.fullmatch(r"^/news/(.*?)/?$", "/news/a/b/"))
        self.assertEqual(re.fullmatch(r"^/news/(.*?)/?$", "/news/a/b/").group(1), "a/b")

    def test_placeholders(self):
        out = self.translated(redirects="/news/:month/:date/:year/:slug /blog/:year/:month/:date/:slug\n")
        self.assertIn("/blog/{re.p2c_r1.3}/{re.p2c_r1.1}/{re.p2c_r1.2}/{re.p2c_r1.4}{?query} 301", out)

    def test_placeholder_referenced_twice_is_allowed(self):
        self.translated(redirects="/a/:x /b/:x/:x\n")

    def test_forced_rule_needs_no_site_check(self):
        out = self.translated(redirects="/about /team 301!\n", site_paths=None)
        self.assertIn("redir @p2c_r1 /team{?query} 301", out)

    def test_unshadowed_rule_with_site_check(self):
        self.translated(redirects="/old-docs/* /docs/:splat\n")

    def test_later_rule_matched_first_is_noted(self):
        result = self.run_translate(redirects="/blog/* /news/:splat\n/blog/x /elsewhere\n", site_paths=self.site)
        self.assertTrue(result.ok)
        self.assertEqual([m.line for m in self.messages(result, "note")], [2])

    def test_host_level_rule_is_skipped(self):
        result = self.run_translate(redirects="https://www.example.com/* https://example.com/:splat 301!\n")
        self.assertTrue(result.ok)
        self.assertEqual(result.skipped, 1)

    # refused --------------------------------------------------------------

    def test_rule_without_force_and_no_site_dir(self):
        self.refused(redirects="/old /new\n", line=1, contains="pass --site-dir", site_paths=None)

    def test_rule_shadowed_by_an_html_file(self):
        message = self.refused(redirects="/about /team\n", line=1, contains="Netlify would serve the file")
        self.assertIn("about.html", message.text)

    def test_rule_shadowed_by_an_index_file(self):
        self.refused(redirects="/docs/ /manual\n", line=1, contains="docs/index.html")

    def test_splat_rule_shadowed_by_a_file(self):
        self.refused(redirects="/* /index.html 301\n", line=1, contains="Netlify would serve the file")

    def test_status_200(self):
        self.refused(redirects="/* /index.html 200\n", line=1, contains="rewrite")

    def test_status_404(self):
        self.refused(redirects="/ecommerce /store-closed 404\n", line=1, contains="not-found page")

    def test_status_307(self):
        self.refused(redirects="/a /b 307!\n", line=1, contains="307 as unsupported")

    def test_statuses_303_and_308(self):
        for status in (303, 308):
            with self.subTest(status=status):
                self.refused(redirects=f"/a /b {status}!\n", line=1, contains="only for 301 and 302")

    def test_splat_not_at_the_end(self):
        self.refused(redirects="/jobs/*.html /careers/:splat 301!\n", line=1, contains="only at the end")

    def test_placeholder_not_a_whole_segment(self):
        self.refused(redirects="/v:version /docs 301!\n", line=1, contains="not a whole path segment")

    def test_destination_with_its_own_query(self):
        self.refused(redirects="/a /?query=string 301!\n", line=1, contains="Netlify passes the request's query")

    def test_query_parameter_matching(self):
        self.refused(redirects="/store id=:id /blog/:id 301\n", line=1, contains="query parameters")

    def test_role_condition(self):
        self.refused(redirects="/admin/* /admin/:splat 200! Role=admin\n", line=1, contains="conditions")


if __name__ == "__main__":
    unittest.main()
