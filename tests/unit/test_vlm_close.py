# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the VLM close chain and singleton reset (issue #4726)."""

import asyncio
import threading
from unittest.mock import MagicMock

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.base import FailoverVLM, MultiCredentialVLM
from openviking.storage.viking_fs import _base as viking_fs_base
from openviking.storage.viking_fs import reset_viking_fs
from openviking.utils.async_client_cache import LoopScopedAsyncClientCache
from openviking_cli.utils.config.vlm_config import VLMConfig


class _StubClient:
    def __init__(self, closed, name):
        self._closed = closed
        self.name = name

    def close(self):
        self._closed.append(self.name)


def test_openai_vlm_close_closes_sync_and_cached_async_clients():
    vlm = OpenAIVLM({"model": "gpt-x", "provider": "openai", "api_key": "k"})
    closed = []

    sync_client = _StubClient(closed, "sync")
    vlm._sync_client = sync_client

    holder = {}

    def build_on_worker_loop():
        # Build the cached client on a loop that stays alive (a closed and
        # collected loop auto-evicts its weak cache entry).
        loop = asyncio.new_event_loop()
        holder["loop"] = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                _await_nothing(vlm._async_client_cache, closed)
            )
        finally:
            asyncio.set_event_loop(None)

    thread = threading.Thread(target=build_on_worker_loop)
    thread.start()
    thread.join()

    assert vlm._async_client_cache.has_clients()

    vlm.close()

    assert sorted(closed) == ["async", "sync"]
    assert vlm._sync_client is None
    assert not vlm._async_client_cache.has_clients()

    # Closing twice is safe.
    vlm.close()
    holder["loop"].close()


async def _await_nothing(cache, closed):
    cache.get(lambda: _StubClient(closed, "async"))


def _wrap_mock(mock_vlm):
    mock_vlm.model = "m"
    mock_vlm.provider = "p"
    mock_vlm.thinking = False
    return mock_vlm


def test_failover_vlm_close_propagates_and_tolerates_failures():
    primary, backup = _wrap_mock(MagicMock()), _wrap_mock(MagicMock())
    primary.close.side_effect = RuntimeError("boom")
    failover = FailoverVLM(primary, backup)

    failover.close()

    assert primary.close.called
    assert backup.close.called


def test_multi_credential_vlm_close_propagates_to_every_backend():
    first, second = _wrap_mock(MagicMock()), _wrap_mock(MagicMock())
    multi = MultiCredentialVLM([first, second], credential_ids=["a", "b"])

    multi.close()

    assert first.close.called
    assert second.close.called


def test_close_vlm_instance_closes_and_drops_the_cached_instance():
    config = VLMConfig()
    instance = MagicMock()
    config._vlm_instance = instance

    config.close_vlm_instance()

    instance.close.assert_called_once()
    assert config._vlm_instance is None

    # Second call is a no-op, and a raising closer is swallowed.
    raising = MagicMock()
    raising.close.side_effect = RuntimeError("boom")
    config._vlm_instance = raising
    config.close_vlm_instance()
    raising.close.assert_called_once()
    assert config._vlm_instance is None


def test_reset_viking_fs_clears_and_returns_the_singleton(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(viking_fs_base, "_instance", sentinel)

    assert reset_viking_fs() is sentinel
    assert viking_fs_base._instance is None
