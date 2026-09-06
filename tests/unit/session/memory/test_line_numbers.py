# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.utils.line_numbers import (
    add_line_numbers,
    every_line_has_line_numbers,
    extract_start_line_number,
    looks_like_line_numbered_view,
    strip_display_prefixes,
    strip_line_numbers,
)


class TestAddLineNumbers:
    def test_numbers_plain_content(self):
        assert add_line_numbers("alpha\nbeta") == "1\talpha\n2\tbeta"

    def test_respects_start_line(self):
        assert add_line_numbers("alpha\nbeta", start_line=3) == "3\talpha\n4\tbeta"

    def test_empty_content_returns_empty(self):
        assert add_line_numbers("") == ""

    def test_does_not_renumber_already_numbered_content(self):
        """Numbering already-numbered content must not stack prefixes (#4413)."""
        numbered = add_line_numbers("alpha\nbeta")
        assert add_line_numbers(numbered) == numbered


class TestStripLineNumbers:
    def test_strips_single_prefix(self):
        assert strip_line_numbers("1\talpha\n2\tbeta") == "alpha\nbeta"

    def test_strips_all_accumulated_prefixes(self):
        """Merged memories can carry several stacked prefixes (#4413)."""
        assert strip_line_numbers("1\t1\t## Title\n2\t2\t- fact one") == (
            "## Title\n- fact one"
        )

    def test_strip_is_idempotent(self):
        content = "1\t1\t1\t## Title\n2\t2\t2\t- fact one"
        once = strip_line_numbers(content)
        assert once == "## Title\n- fact one"
        assert strip_line_numbers(once) == once

    def test_round_trips_stacked_add_on_plain_content(self):
        plain = "## Title\n- fact one"
        stacked = "1\t1\t## Title\n2\t2\t- fact one"  # add_line_numbers applied twice
        assert strip_line_numbers(stacked) == plain

    def test_plain_content_unchanged(self):
        assert strip_line_numbers("alpha\nbeta") == "alpha\nbeta"

    def test_keeps_inner_tabs_after_prefix(self):
        assert strip_line_numbers("1\talpha\tkeeps inner tabs") == "alpha\tkeeps inner tabs"

    def test_non_aggressive_keeps_leading_whitespace(self):
        assert strip_line_numbers(" 1\talpha") == " 1\talpha"

    def test_aggressive_strips_all_accumulated_prefixes_with_whitespace(self):
        assert strip_line_numbers(" 1\t 1\talpha", aggressive=True) == "alpha"


class TestEveryLineHasLineNumbers:
    def test_true_for_fully_numbered_content(self):
        assert every_line_has_line_numbers("1\ta\n2\tb") is True

    def test_true_for_content_with_accumulated_prefixes(self):
        assert every_line_has_line_numbers("1\t1\ta\n2\t2\tb") is True

    def test_false_for_plain_content(self):
        assert every_line_has_line_numbers("a\nb") is False

    def test_false_for_partially_numbered_content(self):
        assert every_line_has_line_numbers("1\ta\nb") is False

    def test_false_for_empty_content(self):
        assert every_line_has_line_numbers("") is False


class TestExtractStartLineNumber:
    def test_reads_first_prefix(self):
        assert extract_start_line_number("3\talpha\n4\tbeta") == 3

    def test_reads_first_prefix_from_accumulated_prefixes(self):
        assert extract_start_line_number("3\t3\talpha") == 3

    def test_none_for_plain_content(self):
        assert extract_start_line_number("alpha\nbeta") is None


class TestLooksLikeLineNumberedView:
    def test_true_for_consecutive_run(self):
        assert looks_like_line_numbered_view("1\ta\n2\tb\n3\tc") is True

    def test_true_for_run_with_offset_start(self):
        assert looks_like_line_numbered_view("37\ta\n38\tb") is True

    def test_true_for_single_numbered_line(self):
        assert looks_like_line_numbered_view("2\t- Bind: Tailscale") is True

    def test_true_despite_trailing_newline(self):
        assert looks_like_line_numbered_view("1\ta\n2\tb\n") is True

    def test_false_for_non_consecutive_numbers(self):
        """Genuine tabular data keeps real numeric columns (#4413)."""
        assert looks_like_line_numbered_view("1\tfoo\tbar\n3\tbaz\tqux") is False

    def test_false_for_plain_content(self):
        assert looks_like_line_numbered_view("a\nb") is False

    def test_false_for_mixed_numbered_and_plain_lines(self):
        assert looks_like_line_numbered_view("1\theader\nplain\n3\tmore") is False

    def test_false_for_empty_content(self):
        assert looks_like_line_numbered_view("") is False

    def test_false_for_descending_numbers(self):
        assert looks_like_line_numbered_view("2\ta\n1\tb") is False


class TestStripDisplayPrefixes:
    def test_strips_consecutive_view(self):
        assert strip_display_prefixes("1\t## Deploy\n2\t- Auth: required") == (
            "## Deploy\n- Auth: required"
        )

    def test_strips_single_prefix(self):
        assert strip_display_prefixes("2\t- Bind: Tailscale") == "- Bind: Tailscale"

    def test_keeps_trailing_newline(self):
        assert strip_display_prefixes("1\tA\n2\tB\n") == "A\nB\n"

    def test_preserves_non_consecutive_tabular_data(self):
        tsv = "1\tfoo\tbar\n3\tbaz\tqux"
        assert strip_display_prefixes(tsv) == tsv

    def test_preserves_mixed_numbered_and_plain_lines(self):
        mixed = "1\theader\nplain line\n3\tmore"
        assert strip_display_prefixes(mixed) == mixed

    def test_noop_on_plain_content(self):
        assert strip_display_prefixes("alpha\nbeta") == "alpha\nbeta"
