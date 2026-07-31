from __future__ import annotations

import inspect
import json
from importlib.metadata import version
from typing import Any


def inspect_installation() -> dict[str, Any]:
    from run_agent import AIAgent

    return {
        "distribution": "hermes-agent",
        "version": version("hermes-agent"),
        "import": "run_agent.AIAgent",
        "chat_available": callable(getattr(AIAgent, "chat", None)),
        "constructor_parameters": list(inspect.signature(AIAgent).parameters),
    }


def main() -> None:
    print(json.dumps(inspect_installation(), ensure_ascii=False))


if __name__ == "__main__":
    main()
