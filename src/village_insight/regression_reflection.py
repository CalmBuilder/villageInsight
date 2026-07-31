from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RegressionReflection(BaseModel):
    """Machine-readable reflection required after every question regression."""

    model_config = ConfigDict(frozen=True)

    plan_alignment: str
    hermes_path_exercised: bool
    scope_enforced: bool
    deterministic_result_verified: bool
    observed_deviation: str | None = None
    next_action: str
