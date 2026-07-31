from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from village_insight.config import get_settings
from village_insight.db.session import get_session_factory
from village_insight.hermes.configuration import resolve_configuration
from village_insight.hermes.runtime import EmbeddedHermesRuntime, HermesUnavailableError


class LiveCheckResult(BaseModel):
    status: Literal["ok"]
    component: Literal["hermes-provider"]


async def check() -> LiveCheckResult:
    settings = get_settings()
    with get_session_factory()() as database:
        connection = resolve_configuration(database, settings).connection
    return await EmbeddedHermesRuntime(settings, connection).run_json(
        system_prompt=(
            "You are a connectivity test. Do not use tools. "
            "Return the requested JSON object and nothing else."
        ),
        user_prompt='Return {"status":"ok","component":"hermes-provider"}.',
        output_model=LiveCheckResult,
    )


def main() -> None:
    try:
        print(asyncio.run(check()).model_dump_json())
    except HermesUnavailableError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
