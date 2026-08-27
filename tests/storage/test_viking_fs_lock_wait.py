# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""rm/mv path-lock acquires must wait for a busy tree instead of failing at 0ms.

Regresssion guard for the CI flake seen on #4373: ``filesystem/test_fs_rm``
creates a directory and immediately removes it; the zero-wait lock acquire
returned CONFLICT ``path_busy`` while the mkdir's background semantic refresh
still held the tree lock. Mirrors the ingest-side wait added for #4337.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_fs._ops import FS_OP_LOCK_ACQUIRE_WAIT_SECS
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError


def _make_fs(monkeypatch, *, acquire_side_effect=None):
    fs = VikingFS(agfs=SimpleNamespace())
    acquire_tree = AsyncMock(return_value={"lease_ref": "tree"})
    if acquire_side_effect is not None:
        acquire_tree.side_effect = acquire_side_effect
    acquire_batch = AsyncMock(return_value={"lease_ref": "batch"})
    agfs = SimpleNamespace(
        stat=AsyncMock(return_value={"isDir": True}),
        pathlock_acquire_tree=acquire_tree,
        pathlock_acquire_exact=AsyncMock(return_value={"lease_ref": "exact"}),
        pathlock_acquire_batch=acquire_batch,
        pathlock_release=AsyncMock(),
        rm=AsyncMock(return_value={}),
        ls=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(fs, "_async_agfs", agfs)
    monkeypatch.setattr(fs, "_ensure_delete_access", lambda *_a, **_k: None)
    monkeypatch.setattr(fs, "_ensure_mutable_access", lambda *_a, **_k: None)
    monkeypatch.setattr(
        fs, "_uri_to_path", lambda uri, **_k: f"/agfs/{uri.split(':///', 1)[-1]}"
    )
    monkeypatch.setattr(fs, "_path_to_uri", lambda path, **_k: f"viking://{path}")
    monkeypatch.setattr(fs, "_ls_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(fs, "_get_vector_store", lambda: None)
    monkeypatch.setattr(fs, "_copy_for_mv", AsyncMock())
    monkeypatch.setattr(fs, "_update_vector_store_uris", AsyncMock())
    monkeypatch.setattr(fs, "_pathlock_fs_ctx", lambda _ctx, lease: {"lease": lease})
    return fs, agfs


@pytest.mark.asyncio
async def test_rm_waits_for_busy_tree_lock(monkeypatch):
    fs, agfs = _make_fs(monkeypatch)

    await fs.rm("viking:///resources/docs", recursive=True, ctx=None)

    agfs.pathlock_acquire_tree.assert_awaited_once_with(
        "/agfs/resources/docs", timeout_secs=FS_OP_LOCK_ACQUIRE_WAIT_SECS
    )
    agfs.pathlock_release.assert_awaited_once()


@pytest.mark.asyncio
async def test_rm_file_waits_for_busy_exact_lock(monkeypatch):
    fs, agfs = _make_fs(monkeypatch)
    agfs.stat = AsyncMock(return_value={"isDir": False})

    await fs.rm("viking:///resources/docs/file.md", ctx=None)

    agfs.pathlock_acquire_exact.assert_awaited_once_with(
        "/agfs/resources/docs/file.md", timeout_secs=FS_OP_LOCK_ACQUIRE_WAIT_SECS
    )


@pytest.mark.asyncio
async def test_rm_still_maps_persistent_busy_to_resource_busy(monkeypatch):
    fs, _agfs = _make_fs(
        monkeypatch, acquire_side_effect=LockAcquisitionError("still busy")
    )

    with pytest.raises(ResourceBusyError):
        await fs.rm("viking:///resources/docs", recursive=True, ctx=None)


@pytest.mark.asyncio
async def test_mv_waits_for_busy_batch_lock(monkeypatch):
    fs, agfs = _make_fs(monkeypatch)

    await fs.mv("viking:///resources/a", "viking:///resources/b", ctx=None)

    assert agfs.pathlock_acquire_batch.await_args.kwargs.get(
        "timeout_secs"
    ) == FS_OP_LOCK_ACQUIRE_WAIT_SECS
    requests = agfs.pathlock_acquire_batch.await_args.args[0]
    assert requests == [
        {"path": "/agfs/resources/a", "kind": "tree"},
        {"path": "/agfs/resources/b", "kind": "exact"},
    ]
