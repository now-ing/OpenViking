# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for issue #3984.

``Session._read_live_messages_strict`` used ``str.splitlines()`` to split the
``messages.jsonl`` content into records. ``splitlines()`` treats U+2028
(LINE SEPARATOR), U+2029 (PARAGRAPH SEPARATOR), NEL (U+0085), ``\\r``, ``\\v``
and ``\\f`` as line boundaries, but JSONL records are delimited by ``\\n``
only. When one of those characters appears *inside a JSON string value*
(e.g. an assistant ``tool_output``), the record was cut in half and
``json.loads`` failed with "Unterminated string".

The fix splits on ``"\\n"`` only (after normalizing CRLF).
"""

import json

import pytest

from openviking.message import Message, TextPart
from openviking.session import Session


class _FakeVikingFS:
    """Minimal stand-in for VikingFS exposing only ``read_file``.

    Lets us drive ``_read_live_messages_strict`` without loading the Rust
    ``ragfs_python`` native binding or standing up the full service stack.
    """

    def __init__(self, content: str):
        self._content = content

    async def read_file(self, uri, ctx=None):
        return self._content


def _session_returning(content: str) -> Session:
    return Session(viking_fs=_FakeVikingFS(content), session_id="test-jsonl-unicode")


# Unicode characters that str.splitlines() wrongly treats as line boundaries.
U2028 = " "  # LINE SEPARATOR
U2029 = " "  # PARAGRAPH SEPARATOR
NEL = ""  # NEXT LINE


def test_splitlines_breaks_record_inside_json_string():
    """Documents the root cause: splitlines() splits a JSON record mid-string.

    With the old ``content.splitlines()`` call, a U+2028 inside a JSON string
    value produced two halves, each of which is invalid JSON. The fix splits on
    ``\\n`` only, so the same content parses as a single record.
    """
    payload = json.dumps(
        {"role": "assistant", "parts": [{"type": "text", "text": f"a{U2028}b"}]},
        ensure_ascii=False,
    )

    # Old behavior (the bug): splitlines() yields two halves, neither is valid.
    old_halves = payload.splitlines()
    assert len(old_halves) == 2
    with pytest.raises(json.JSONDecodeError):
        json.loads(old_halves[0])

    # New behavior (the fix): split("\n") keeps the record intact.
    new_records = [r for r in payload.split("\n") if r.strip()]
    assert len(new_records) == 1
    assert json.loads(new_records[0])["parts"][0]["text"] == f"a{U2028}b"


async def test_single_record_with_unicode_line_separators_round_trips():
    """A JSON string value containing U+2028/U+2029/NEL must parse cleanly."""
    text = f"line1{U2028}line2{U2029}line3{NEL}line4"
    msg = Message(id="msg-1", role="assistant", parts=[TextPart(text=text)])
    content = json.dumps(msg.to_dict(), ensure_ascii=False)

    session = _session_returning(content)

    # Before the fix this raised: ValueError: Invalid live message JSONL at
    # line 1: Unterminated string starting at...
    messages = await session._read_live_messages_strict()

    assert len(messages) == 1
    assert messages[0].role == "assistant"
    assert messages[0].parts[0].text == text


async def test_multiple_records_separated_by_newline_with_unicode_inside():
    """Multiple \\n-delimited records, each carrying unicode separators."""
    one = Message(id="msg-1", role="user", parts=[TextPart(text=f"hello{U2028}world")])
    two = Message(id="msg-2", role="assistant", parts=[TextPart(text=f"reply{U2029}back{NEL}here")])
    content = "\n".join(
        json.dumps(m.to_dict(), ensure_ascii=False) for m in (one, two)
    )

    session = _session_returning(content)
    messages = await session._read_live_messages_strict()

    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].parts[0].text == f"hello{U2028}world"
    assert messages[1].parts[0].text == f"reply{U2029}back{NEL}here"


async def test_crlf_line_endings_are_normalized():
    """Records separated by \\r\\n must still parse (trailing CR stripped)."""
    one = Message(id="msg-1", role="user", parts=[TextPart(text="first")])
    two = Message(id="msg-2", role="assistant", parts=[TextPart(text="second")])
    content = "\r\n".join(
        json.dumps(m.to_dict(), ensure_ascii=False) for m in (one, two)
    )

    session = _session_returning(content)
    messages = await session._read_live_messages_strict()

    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].parts[0].text == "first"
    assert messages[1].parts[0].text == "second"


async def test_trailing_newline_does_not_create_extra_message():
    """A trailing \\n (common when appending records) must not error."""
    msg = Message(id="msg-1", role="user", parts=[TextPart(text=f"x{U2028}y")])
    content = json.dumps(msg.to_dict(), ensure_ascii=False) + "\n"

    session = _session_returning(content)
    messages = await session._read_live_messages_strict()

    assert len(messages) == 1
    assert messages[0].parts[0].text == f"x{U2028}y"
