# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared writeback for semantic sidecar files."""

import re
from typing import Any, Callable, Dict, Optional

from openviking.server.identity import RequestContext
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_THEMATIC_BREAK_RE = re.compile(r"^ {0,3}([-_*])(?:[ \t]*\1){2,}[ \t]*$")


def has_retrievable_abstract_text(text: str) -> bool:
    """True if the abstract contains at least one non-structural text line.

    A bare thematic break (``---``) or heading-only content would persist a
    structurally invalid sidecar: the reader treats a leading ``---`` as an
    opening frontmatter delimiter and fails with "frontmatter is not closed"
    (#4336). Such content must not be written at all.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _THEMATIC_BREAK_RE.match(stripped):
            continue
        if stripped.startswith("#"):
            continue
        return True
    return False


async def write_semantic_sidecars(
    *,
    viking_fs: Any,
    dir_uri: str,
    overview: str,
    abstract: str,
    ctx: Optional[RequestContext],
    is_stale: Callable[[], bool],
    lock: Optional[Dict[str, Any]] = None,
    log_prefix: str = "[Semantic]",
) -> bool:
    """Write .overview.md and .abstract.md sidecar files under dir_uri with exact-path locking."""
    if is_stale():
        logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
        return False

    lock_paths = [
        viking_fs._uri_to_path(f"{dir_uri}/.overview.md", ctx=ctx),
        viking_fs._uri_to_path(f"{dir_uri}/.abstract.md", ctx=ctx),
    ]
    sidecar_lease = lock
    owns_lease = sidecar_lease is None
    if sidecar_lease is None:
        sidecar_lease = await viking_fs._async_agfs.pathlock_acquire_exact_batch(lock_paths)
    try:
        if is_stale():
            logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
            return False
        await _write_sidecars(viking_fs, dir_uri, overview, abstract, ctx, sidecar_lease)
        return True
    finally:
        if owns_lease:
            await viking_fs._async_agfs.pathlock_release(sidecar_lease)


async def _write_sidecars(
    viking_fs: Any,
    dir_uri: str,
    overview: str,
    abstract: str,
    ctx: Optional[RequestContext],
    lease_ref: Optional[Dict[str, Any]] = None,
) -> None:
    """Write sidecar files to the target directory."""
    await viking_fs.write_file(
        f"{dir_uri}/.overview.md",
        overview,
        ctx=ctx,
        lease_ref=lease_ref,
    )
    if not has_retrievable_abstract_text(abstract):
        logger.warning(
            "[SemanticSidecar] Skipping content-free abstract for %s "
            "(no retrievable text; would break sidecar parsing)",
            dir_uri,
        )
        return
    await viking_fs.write_file(
        f"{dir_uri}/.abstract.md",
        abstract,
        ctx=ctx,
        lease_ref=lease_ref,
    )
