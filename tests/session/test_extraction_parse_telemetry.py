# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Structured parse-failure telemetry for the extraction loop (RFC #4243 slice 1)."""

import sys
import types
from types import SimpleNamespace

_ark = types.ModuleType("volcenginesdkarkruntime")
_ark_exc = types.ModuleType("volcenginesdkarkruntime._exceptions")


class _ArkRateLimitError(Exception):
    pass


_ark_exc.ArkRateLimitError = _ArkRateLimitError
_ark._exceptions = _ark_exc
sys.modules.setdefault("volcenginesdkarkruntime", _ark)
sys.modules.setdefault("volcenginesdkarkruntime._exceptions", _ark_exc)

from openviking.session.compressor_v3 import _report_extraction_telemetry


class _CaptureTelemetry:
    def __init__(self):
        self.values = {}

    def set(self, name, value):
        self.values[name] = value


def _fake_result():
    return SimpleNamespace(
        written_uris=[],
        edited_uris=[],
        deleted_uris=[],
        skipped_operations=[],
        errors=[(
            "viking://user/u/memories/entities/unknown.md",
            "Final response could not be parsed as JSON operations "
            "after 4 iterations (failure_kind=parse_error)",
        )],
    )


def _fake_operations():
    return SimpleNamespace(upsert_operations=[], delete_file_contents=[])


def test_parse_stats_emitted_for_zero_extraction(monkeypatch):
    captured = _CaptureTelemetry()
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_current_telemetry", lambda: captured
    )
    stats = {
        "failure_kind": "parse_error",
        "format_retries_used": 1,
        "iterations_used": 4,
        "max_iterations": 4,
        "exhausted": True,
    }

    _report_extraction_telemetry(_fake_result(), _fake_operations(), stats)

    assert captured.values["memory.extract.parse.failure.parse_error"] == 1
    assert captured.values["memory.extract.parse.format_retries_used"] == 1
    assert captured.values["memory.extract.parse.iterations_used"] == 4
    assert captured.values["memory.extract.parse.exhausted"] == 1
    # existing counters keep working for the zero-extraction shape
    assert captured.values["memory.extract.failed"] == 1


def test_parse_stats_absent_emits_nothing(monkeypatch):
    captured = _CaptureTelemetry()
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_current_telemetry", lambda: captured
    )

    _report_extraction_telemetry(_fake_result(), _fake_operations())

    assert not [k for k in captured.values if k.startswith("memory.extract.parse.")]


def test_parse_stats_no_failure_kind_skips_kind_counter(monkeypatch):
    captured = _CaptureTelemetry()
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_current_telemetry", lambda: captured
    )
    stats = {
        "failure_kind": None,
        "format_retries_used": 0,
        "iterations_used": 2,
        "max_iterations": 3,
        "exhausted": False,
    }

    _report_extraction_telemetry(_fake_result(), _fake_operations(), stats)

    assert "memory.extract.parse.failure.parse_error" not in captured.values
    assert "memory.extract.parse.failure.refusal_text" not in captured.values
    assert captured.values["memory.extract.parse.iterations_used"] == 2
    assert captured.values["memory.extract.parse.exhausted"] == 0
