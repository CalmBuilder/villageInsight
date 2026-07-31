from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

from village_insight.db.models import SemanticFieldVariant


def normalized_field_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def build_field_variant(values: dict[str, Any]) -> SemanticFieldVariant:
    header_path = [
        " ".join(str(part).split())
        for part in values.get("header_path", [])
        if str(part).strip()
    ]
    alias = str(values["alias"]).strip() if values.get("alias") else None
    role = str(values["role"]).strip() if values.get("role") else None
    normalized_value = normalized_field_label(
        alias or " / ".join(header_path) or role or ""
    )
    if not normalized_value:
        raise ValueError("field variant requires an alias, header path, or role")
    identity = {
        "kind": str(values["kind"]),
        "normalized_value": normalized_value,
        "header_path": header_path,
        "role": role,
        "domain": values.get("domain"),
        "record_type": values.get("record_type"),
        "observed_data_type": values.get("observed_data_type"),
        "unit_dimension": values.get("unit_dimension"),
    }
    variant_key = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return SemanticFieldVariant(
        variant_key=variant_key,
        kind=str(values["kind"]),
        normalized_value=normalized_value,
        alias=alias,
        header_path=header_path,
        parent_path=header_path[:-1],
        role=role,
        domain=(
            str(values["domain"]).strip()
            if values.get("domain")
            else None
        ),
        record_type=(
            str(values["record_type"]).strip()
            if values.get("record_type")
            else None
        ),
        observed_data_type=values.get("observed_data_type"),
        unit_dimension=values.get("unit_dimension"),
        source=str(values.get("source") or "manual"),
        confidence_basis_points=int(values.get("confidence_basis_points", 10_000)),
        evidence=dict(values.get("evidence") or {}),
    )
