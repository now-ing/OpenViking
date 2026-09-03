# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Budget clamp for the session-extraction trajectory (#3226)."""

from types import SimpleNamespace

import pytest

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)


def _message(idx: int, text: str) -> Message:
    return Message(
        id=f"msg-{idx}",
        role="user",
        parts=[TextPart(text=text)],
        created_at="2026-09-03T00:00:00Z",
    )


@pytest.fixture
def budget_config(monkeypatch):
    def _install(budget: int):
        config = SimpleNamespace(
            semantic=SimpleNamespace(max_session_extract_prompt_chars=budget),
            memory=SimpleNamespace(eager_prefetch=False, prefetch_search_topn=5, link_enabled=True),
            language_fallback="en",
        )
        for target in (
            "openviking.session.memory.session_extract_context_provider.get_openviking_config",
            "openviking.session.memory.utils.language.get_openviking_config",
        ):
            monkeypatch.setattr(target, lambda: config)

    return _install


def test_over_budget_drops_oldest_first(budget_config):
    budget_config(3500)
    msgs = [_message(i, "x" * 1000) for i in range(10)]  # 10k chars total
    provider = SessionExtractContextProvider(messages=list(msgs))
    kept = provider._apply_session_extract_budget(provider.messages)
    assert len(kept) == 3  # 3 x 1000 <= 3500 < 4 x 1000
    assert [m.id for m in kept] == ["msg-7", "msg-8", "msg-9"]


def test_within_budget_keeps_everything(budget_config):
    budget_config(60000)
    msgs = [_message(i, "x" * 1000) for i in range(10)]
    provider = SessionExtractContextProvider(messages=list(msgs))
    kept = provider._apply_session_extract_budget(provider.messages)
    assert kept is provider.messages


def test_single_over_budget_message_is_kept(budget_config):
    budget_config(100)
    msgs = [_message(0, "x" * 5000)]
    provider = SessionExtractContextProvider(messages=list(msgs))
    kept = provider._apply_session_extract_budget(provider.messages)
    assert [m.id for m in kept] == ["msg-0"]


def test_zero_budget_disables_clamp(budget_config):
    budget_config(0)
    msgs = [_message(i, "x" * 1000) for i in range(10)]
    provider = SessionExtractContextProvider(messages=list(msgs))
    kept = provider._apply_session_extract_budget(provider.messages)
    assert len(kept) == 10
