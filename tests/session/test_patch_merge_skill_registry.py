# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session-skill patch-merge must resolve schemas from the skill registry (issue #4368).

`Compressor._get_session_skill_trainer` builds a `PatchMergePolicyOptimizer`
with the trainer-level ``memory_type="skills"``. Gradients from
``_skill_operations_to_gradients`` carry the same value, but the schema
describing skills only exists (under the key ``session_skills``) in the
dedicated ``skill_extract`` registry — the general ``MemoryTypeRegistry``
never contains it, so the default provider raised
``Memory schema not found or disabled: skills`` on every commit that reached
skill extraction.
"""

from __future__ import annotations

import pytest

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.patch_merge_context_provider import (
    PatchMergeContextProvider,
    PatchMergePatch,
)
from openviking.session.skill.session_skill_context_provider import (
    SESSION_SKILL_MEMORY_TYPE,
    load_skill_extract_registry,
)
from openviking.session.train.components.policy_optimizer import (
    PatchMergePolicyOptimizer,
)


def _skill_patch() -> PatchMergePatch:
    return PatchMergePatch(
        before_file=None,
        after_file=MemoryFile(
            uri="viking://memories/default/user/skills/pdf-merge/SKILL.md",
            content="Merge PDFs with a single command.",
            memory_type="skills",
            extra_fields={"memory_type": "skills", "skill_name": "pdf-merge"},
        ),
    )


def test_skill_provider_resolves_schema_from_skill_registry() -> None:
    """The skill path must query the skill registry under 'session_skills'."""
    provider = PatchMergeContextProvider(
        memory_type="skills",
        patches=[_skill_patch()],
        registry_factory=load_skill_extract_registry,
        schema_memory_type=SESSION_SKILL_MEMORY_TYPE,
    )
    schemas = provider.get_memory_schemas(None)
    assert len(schemas) == 1
    assert schemas[0].memory_type == SESSION_SKILL_MEMORY_TYPE
    assert schemas[0].enabled


def test_skill_provider_default_registry_still_raises() -> None:
    """Guard: without injection the general registry has no 'skills' entry.

    If this ever starts passing because 'skills' became a first-class general
    schema, the injection in ``_get_session_skill_trainer`` can be dropped.
    """
    provider = PatchMergeContextProvider(
        memory_type="skills",
        patches=[_skill_patch()],
    )
    with pytest.raises(ValueError, match="skills"):
        provider.get_memory_schemas(None)


def test_default_experiences_path_is_unchanged() -> None:
    """No injection → general registry + memory_type key, exactly as before."""
    provider = PatchMergeContextProvider(
        memory_type="experiences",
        patches=[],
    )
    schemas = provider.get_memory_schemas(None)
    assert len(schemas) == 1
    assert schemas[0].memory_type == "experiences"
    assert schemas[0].enabled


def test_optimizer_defaults_preserve_legacy_construction() -> None:
    """Existing call sites (experiences) construct the optimizer unchanged."""
    optimizer = PatchMergePolicyOptimizer(viking_fs=None, memory_type="experiences")
    assert optimizer.registry_factory is None
    assert optimizer.schema_memory_type is None


def test_optimizer_passes_registry_config_to_provider() -> None:
    """The optimizer forwards the registry override into the provider it builds."""
    optimizer = PatchMergePolicyOptimizer(
        viking_fs=None,
        memory_type="skills",
        registry_factory=load_skill_extract_registry,
        schema_memory_type=SESSION_SKILL_MEMORY_TYPE,
    )
    provider = PatchMergeContextProvider(
        memory_type=optimizer.memory_type,
        patches=[],
        registry_factory=optimizer.registry_factory,
        schema_memory_type=optimizer.schema_memory_type,
    )
    # Same wiring _run_merge_extract_loop uses; must resolve, not raise.
    schemas = provider.get_memory_schemas(None)
    assert schemas[0].memory_type == SESSION_SKILL_MEMORY_TYPE
