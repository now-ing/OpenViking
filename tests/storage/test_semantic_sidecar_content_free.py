# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Content-free abstracts must not be persisted as .abstract.md sidecars (#4336)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.queuefs.semantic_sidecar import (
    has_retrievable_abstract_text,
    write_semantic_sidecars,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("---", False),
        ("***", False),
        ("___", False),
        ("- - -", False),
        ("", False),
        ("   \n", False),
        ("# Heading only\n---\n", False),
        ("This directory contains documents.", True),
        ("---\nSecond line is real text.", True),
        ("# Heading\nFollowed by a real body line.", True),
    ],
)
def test_has_retrievable_abstract_text(text, expected):
    assert has_retrievable_abstract_text(text) is expected


@pytest.mark.asyncio
async def test_write_semantic_sidecars_skips_content_free_abstract():
    viking_fs = MagicMock()
    viking_fs._uri_to_path = MagicMock(return_value="/tmp/ignored")
    viking_fs._async_agfs.pathlock_acquire_exact_batch = AsyncMock(return_value={"lease": 1})
    viking_fs._async_agfs.pathlock_release = AsyncMock()
    viking_fs.write_file = AsyncMock()

    wrote = await write_semantic_sidecars(
        viking_fs=viking_fs,
        dir_uri="viking://resources/x",
        overview="# Overview\n\nBody",
        abstract="---",
        ctx=None,
        is_stale=lambda: False,
    )

    assert wrote is True
    written_paths = [call.args[0] for call in viking_fs.write_file.await_args_list]
    assert "viking://resources/x/.overview.md" in written_paths
    assert "viking://resources/x/.abstract.md" not in written_paths


@pytest.mark.asyncio
async def test_write_semantic_sidecars_writes_real_abstract():
    viking_fs = MagicMock()
    viking_fs._uri_to_path = MagicMock(return_value="/tmp/ignored")
    viking_fs._async_agfs.pathlock_acquire_exact_batch = AsyncMock(return_value={"lease": 1})
    viking_fs._async_agfs.pathlock_release = AsyncMock()
    viking_fs.write_file = AsyncMock()

    wrote = await write_semantic_sidecars(
        viking_fs=viking_fs,
        dir_uri="viking://resources/x",
        overview="# Overview\n\nBody",
        abstract="Real abstract text.",
        ctx=None,
        is_stale=lambda: False,
    )

    assert wrote is True
    written_paths = [call.args[0] for call in viking_fs.write_file.await_args_list]
    assert "viking://resources/x/.abstract.md" in written_paths
