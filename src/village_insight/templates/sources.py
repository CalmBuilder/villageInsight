from __future__ import annotations

from typing import Literal

VALIDATED_BASELINE_SOURCE = "validated_baseline"
VALIDATED_CORPUS_SOURCE = "validated_corpus"
AUTO_GOVERNANCE_SOURCE = "auto_governance"
MANUAL_GOVERNANCE_SOURCE = "manual_governance"

type TemplateSource = Literal[
    "validated_baseline",
    "validated_corpus",
    "auto_governance",
    "manual_governance",
    "manual",
    "codex",
    "hermes",
    "hermes_verified",
    "hermes_provisional",
    "governance",
    "bootstrap",
    "migration",
    "real_regression",
    "rule",
    "cache",
    "legacy",
    "test",
]


def source_metadata(
    *,
    source: str,
    metadata: dict[str, object] | None = None,
    legacy_source: str | None = None,
) -> dict[str, object]:
    values = dict(metadata or {})
    values["source_contract"] = "four-layer-template-source/v1"
    values["source"] = source
    if legacy_source and legacy_source != source:
        values.setdefault("legacy_source", legacy_source)
    return values
