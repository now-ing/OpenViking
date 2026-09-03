# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ``vlm.keepalive_expiry`` configuration propagation (#4464).

The OpenAI SDK leaves httpx ``keepalive_expiry`` at its 5.0s default. A pooled
keep-alive connection that outlives the server-side idle timeout surfaces as an
intermittent ``httpcore.ReadTimeout`` when reused. The VLM backend therefore
injects an httpx client with an explicit ``keepalive_expiry`` (default 0 =
no connection reuse); the value is configurable via ``ov.conf``.
"""

from unittest import mock

import httpx
import pytest
from pydantic import ValidationError

from openviking.models.vlm.backends.openai_vlm import (
    OpenAIVLM,
    _build_httpx_limits,
)
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_vlm_config_keepalive_expiry_defaults_to_zero():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x")
    assert cfg.keepalive_expiry == 0.0


def test_vlm_config_accepts_positive_keepalive_expiry():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x", keepalive_expiry=60.0)
    assert cfg.keepalive_expiry == 60.0


def test_vlm_config_rejects_negative_keepalive_expiry():
    with pytest.raises(ValidationError):
        VLMConfig(model="gpt-4o-mini", api_key="sk-x", keepalive_expiry=-1.0)


def test_build_httpx_limits_overrides_keepalive_expiry():
    limits = _build_httpx_limits(0.0)
    assert limits.keepalive_expiry == 0.0
    # Remaining limits stay aligned with the OpenAI SDK defaults.
    assert limits.max_connections == 1000
    assert limits.max_keepalive_connections == 100


def test_vlm_config_propagates_keepalive_expiry_to_backend_dict():
    cfg = VLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-x",
        api_base="https://example.invalid",
        keepalive_expiry=30.0,
    )
    config_dict = cfg._build_vlm_config_dict()
    assert config_dict["keepalive_expiry"] == 30.0


def _pool_keepalive_expiry(client) -> float:
    return client._transport._pool._keepalive_expiry


def test_sync_client_injects_http_client_without_connection_reuse():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
        }
    )
    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI") as fake:
        vlm.get_client()

    http_client = fake.call_args.kwargs.get("http_client")
    assert isinstance(http_client, httpx.Client)
    assert _pool_keepalive_expiry(http_client) == 0.0


def test_sync_client_respects_configured_keepalive_expiry():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "keepalive_expiry": 60.0,
        }
    )
    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI") as fake:
        vlm.get_client()

    http_client = fake.call_args.kwargs.get("http_client")
    assert _pool_keepalive_expiry(http_client) == 60.0


def test_sync_client_keeps_configured_request_timeout():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "timeout": 120.0,
        }
    )
    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI") as fake:
        vlm.get_client()

    kwargs = fake.call_args.kwargs
    assert kwargs.get("timeout") == 120.0
    assert kwargs["http_client"].timeout == httpx.Timeout(120.0)


def test_async_client_injects_http_client_without_connection_reuse():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
        }
    )
    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI") as fake:
        vlm._build_async_client()

    http_client = fake.call_args.kwargs.get("http_client")
    assert isinstance(http_client, httpx.AsyncClient)
    assert _pool_keepalive_expiry(http_client) == 0.0


def test_azure_sync_client_injects_http_client():
    vlm = OpenAIVLM(
        {
            "provider": "azure",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
        }
    )
    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.AzureOpenAI") as fake:
        vlm.get_client()

    http_client = fake.call_args.kwargs.get("http_client")
    assert isinstance(http_client, httpx.Client)
    assert _pool_keepalive_expiry(http_client) == 0.0
