# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import re
from typing import Optional

_LINE_NUMBER_PREFIX_RE = re.compile(r"^(\d+)\t")
_LINE_NUMBER_PREFIX_WITH_LEADING_SPACE_RE = re.compile(r"^\s*(\d+)\t")
# Repeated variants used only by strip_line_numbers: line-number prefixes can
# stack when add_line_numbers() runs on content that already carries a prefix
# (e.g. a patch whose REPLACE block echoes the numbered view of the file). A
# single anchored substitution removes only one prefix per line, so stripping
# must match every leading prefix to stay idempotent (issue #4413).
_LINE_NUMBER_PREFIXES_RE = re.compile(r"^(?:\d+\t)+")
_LINE_NUMBER_PREFIXES_WITH_LEADING_SPACE_RE = re.compile(r"^(?:\s*\d+\t)+")
_LINE_SPLIT_RE = re.compile(r"\r?\n")


def split_content_lines(content: str) -> list[str]:
    if content == "":
        return []
    return _LINE_SPLIT_RE.split(content)


def add_line_numbers(content: str, start_line: int = 1) -> str:
    if not content:
        return ""
    if every_line_has_line_numbers(content):
        # Already line numbered (possibly by a previous call): numbering again
        # would stack prefixes (issue #4413).
        return content
    return "\n".join(
        f"{index + start_line}\t{line}" for index, line in enumerate(split_content_lines(content))
    )


def slice_content_lines(content: str, offset: int = 0, limit: int = -1) -> str:
    lines = split_content_lines(content)
    if offset >= len(lines):
        return ""
    end = None if limit < 0 else offset + limit
    return "\n".join(lines[offset:end])


def line_count(content: str) -> int:
    return len(split_content_lines(content))


def extract_start_line_number(content: str) -> Optional[int]:
    first_line = content.split("\n", 1)[0]
    match = _LINE_NUMBER_PREFIX_WITH_LEADING_SPACE_RE.match(first_line)
    if match is None:
        return None
    return int(match.group(1))


def strip_line_numbers(content: str, aggressive: bool = False) -> str:
    pattern = (
        _LINE_NUMBER_PREFIXES_WITH_LEADING_SPACE_RE
        if aggressive
        else _LINE_NUMBER_PREFIXES_RE
    )
    return "\n".join(pattern.sub("", line) for line in split_content_lines(content))


def every_line_has_line_numbers(content: str) -> bool:
    lines = split_content_lines(content)
    if not lines:
        return False
    return all(_LINE_NUMBER_PREFIX_RE.match(line) for line in lines)


def looks_like_line_numbered_view(content: str) -> bool:
    """Whether the whole content reads as a numbered view add_line_numbers() produced.

    Every line (a trailing newline allowed) must start with an ``N\\t`` prefix
    and the numbers must form one consecutive increasing run. Genuine
    tabular data whose first column happens to be numeric almost never forms
    an exact consecutive run, so this keeps real content intact.
    """
    lines = split_content_lines(content)
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines:
        return False
    numbers = []
    for line in lines:
        match = _LINE_NUMBER_PREFIX_RE.match(line)
        if match is None:
            return False
        numbers.append(int(match.group(1)))
    return all(right - left == 1 for left, right in zip(numbers, numbers[1:]))


def strip_display_prefixes(content: str) -> str:
    """Strip line-number prefixes when content looks like a numbered view.

    No-op otherwise, so genuine numeric/tabular content is preserved
    (issue #4413 write-path cleanup).
    """
    if not looks_like_line_numbered_view(content):
        return content
    return strip_line_numbers(content)
