import asyncio
import os
import sys
import time
import types
import uuid
from pathlib import Path

import pytest
from pydantic import BaseModel
from pytest import MonkeyPatch

from village_insight.config import Settings
from village_insight.hermes.runtime import (
    EmbeddedHermesRuntime,
    HermesCallPolicy,
    HermesConnection,
    HermesInvalidResponseError,
    HermesOperatorActionRequiredError,
    HermesUnavailableError,
)


class Answer(BaseModel):
    value: str


TIMEOUT_PID_PATH: Path | None = None


class SlowAgent:
    def __init__(self, **_: object) -> None:
        if TIMEOUT_PID_PATH is not None:
            TIMEOUT_PID_PATH.write_text(str(os.getpid()))

    def run_conversation(self, **_: object) -> dict[str, object]:
        time.sleep(30)
        return {"final_response": '{"value": "late"}', "messages": []}


class CrashingAgent:
    def __init__(self, **_: object) -> None:
        pass

    def run_conversation(self, **_: object) -> dict[str, object]:
        os._exit(3)


def test_runtime_creates_isolated_restricted_agent(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_conversation(self, **kwargs: object) -> dict[str, object]:
            captured["conversation"] = kwargs
            captured["logs_dir"] = self.logs_dir
            (self.logs_dir / "request_dump_test-task_fake.json").write_text(
                "sensitive request"
            )
            return {
                "final_response": '{"value": "ok"}',
                "messages": [{"role": "assistant", "content": '{"value": "ok"}'}],
            }

    settings = Settings(
        _env_file=None,
        hermes_enabled=True,
        hermes_model="test-model",
        hermes_api_key="not-a-real-key",
    )
    runtime = EmbeddedHermesRuntime(settings)
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    result = asyncio.run(
        runtime.run_json_with_trace(
            system_prompt="Return an answer.",
            user_prompt="test request",
            output_model=Answer,
            task_id="test-task",
        )
    )

    assert result.value == Answer(value="ok")
    assert result.trace.session_id == "test-task"
    assert result.trace.message_count == 1
    assert captured["enabled_toolsets"] == []
    assert captured["reasoning_config"] == {"enabled": False}
    assert captured["request_overrides"] == {"response_format": {"type": "json_object"}}
    assert "max_tokens" not in captured
    assert result.trace.max_tokens is None
    assert captured["skip_context_files"] is True
    assert captured["skip_memory"] is True
    assert captured["load_soul_identity"] is False
    assert captured["checkpoints_enabled"] is False
    assert captured["conversation"] == {
        "user_message": "test request",
        "task_id": "test-task",
    }
    assert not Path(captured["logs_dir"]).exists()  # type: ignore[arg-type]


def test_runtime_applies_explicit_thinking_policy(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_conversation(self, **_: object) -> dict[str, object]:
            return {"final_response": '{"value": "ok"}', "messages": []}

    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_provider="deepseek",
            hermes_model="deepseek-v4-flash",
            hermes_thinking_protocol="deepseek",
            deepseek_api_key="not-a-real-key",
        )
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    result = asyncio.run(
        runtime.run_json_with_trace(
            system_prompt="Map ambiguous fields.",
            user_prompt="test request",
            output_model=Answer,
            policy=HermesCallPolicy(
                thinking_enabled=True,
                reasoning_effort="max",
                max_tokens=4096,
                enabled_toolsets=("evidence",),
                repair_attempts=0,
            ),
        )
    )

    assert captured["api_key"] == "not-a-real-key"
    assert captured["reasoning_config"] == {"enabled": True, "effort": "max"}
    assert captured["enabled_toolsets"] == ["evidence"]
    assert captured["max_tokens"] == 4096
    assert result.trace.reasoning_effort == "max"


def test_runtime_applies_optional_connection_output_cap(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_conversation(self, **_: object) -> dict[str, object]:
            return {"final_response": '{"value": "ok"}', "messages": []}

    runtime = EmbeddedHermesRuntime(
        Settings(_env_file=None, hermes_enabled=True),
        HermesConnection(
            provider="siliconflow",
            model="test-model",
            base_url="https://example.invalid/v1",
            api_key="not-a-real-key",
            max_tokens=8192,
        ),
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    result = asyncio.run(
        runtime.run_json_with_trace(
            system_prompt="Return an answer.",
            user_prompt="test request",
            output_model=Answer,
            policy=HermesCallPolicy(thinking_enabled=False),
        )
    )

    assert captured["max_tokens"] == 8192
    assert result.trace.max_tokens == 8192


def test_runtime_uses_anthropic_messages_without_openai_json_mode(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run_conversation(self, **_: object) -> dict[str, object]:
            return {"final_response": '{"value": "ok"}', "messages": []}

    runtime = EmbeddedHermesRuntime(
        Settings(_env_file=None, hermes_enabled=True),
        HermesConnection(
            provider="dashscope",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/apps/anthropic",
            api_key="not-a-real-key",
            api_mode="anthropic_messages",
        ),
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    result = asyncio.run(
        runtime.run_json_with_trace(
            system_prompt="Return an answer.",
            user_prompt="test request",
            output_model=Answer,
        )
    )

    assert result.value == Answer(value="ok")
    assert captured["api_mode"] == "anthropic_messages"
    assert captured["request_overrides"] == {}


def test_runtime_routes_siliconflow_deepseek_models_by_thinking_policy(
    monkeypatch: MonkeyPatch,
) -> None:
    captured_calls: list[dict[str, object]] = []

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured_calls.append(kwargs)

        def run_conversation(self, **_: object) -> dict[str, object]:
            return {"final_response": '{"value": "ok"}', "messages": []}

    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_provider="siliconflow",
            hermes_model="deepseek-ai/DeepSeek-V4-Flash",
            hermes_fast_model="deepseek-ai/DeepSeek-V4-Flash",
            hermes_reasoning_model="deepseek-ai/DeepSeek-V4-Pro",
            hermes_base_url="https://api.siliconflow.cn/v1",
            hermes_thinking_protocol="deepseek",
            llm_multimodal_api_key="not-a-real-key",
        )
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    fast = asyncio.run(
        runtime.run_json_with_trace(
            system_prompt="Extract a fixed schema.",
            user_prompt="test request",
            output_model=Answer,
            policy=HermesCallPolicy(thinking_enabled=False),
        )
    )
    reasoning = asyncio.run(
        runtime.run_json_with_trace(
            system_prompt="Resolve an ambiguous field.",
            user_prompt="test request",
            output_model=Answer,
            policy=HermesCallPolicy(
                thinking_enabled=True,
                reasoning_effort="high",
            ),
        )
    )

    assert captured_calls[0]["model"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert captured_calls[0]["request_overrides"] == {
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    assert fast.trace.model == "deepseek-ai/DeepSeek-V4-Flash"
    assert captured_calls[1]["model"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert captured_calls[1]["request_overrides"] == {
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": "high",
    }
    assert reasoning.trace.model == "deepseek-ai/DeepSeek-V4-Pro"


def test_runtime_applies_official_deepseek_thinking_contract(
    monkeypatch: MonkeyPatch,
) -> None:
    captured_calls: list[dict[str, object]] = []

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured_calls.append(kwargs)

        def run_conversation(self, **_: object) -> dict[str, object]:
            return {"final_response": '{"value": "ok"}', "messages": []}

    runtime = EmbeddedHermesRuntime(
        Settings(_env_file=None, hermes_enabled=True),
        HermesConnection(
            provider="deepseek",
            model="deepseek-v4-flash",
            fast_model="deepseek-v4-flash",
            reasoning_model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key="not-a-real-key",
            thinking_protocol="deepseek",
        ),
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    asyncio.run(
        runtime.run_json(
            system_prompt="Extract a fixed schema.",
            user_prompt="test request",
            output_model=Answer,
            policy=HermesCallPolicy(thinking_enabled=False),
        )
    )
    asyncio.run(
        runtime.run_json(
            system_prompt="Resolve an ambiguous field.",
            user_prompt="test request",
            output_model=Answer,
            policy=HermesCallPolicy(
                thinking_enabled=True,
                reasoning_effort="max",
            ),
        )
    )

    assert captured_calls[0]["model"] == "deepseek-v4-flash"
    assert captured_calls[0]["request_overrides"] == {
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    assert captured_calls[1]["model"] == "deepseek-v4-pro"
    assert captured_calls[1]["request_overrides"] == {
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": "max",
    }


def test_runtime_surfaces_provider_http_error(monkeypatch: MonkeyPatch) -> None:
    class FakeAgent:
        def __init__(self, **_: object) -> None:
            pass

        def run_conversation(self, **_: object) -> dict[str, object]:
            return {"final_response": "HTTP 402: Insufficient Balance", "messages": []}

    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_model="test-model",
            hermes_api_key="not-a-real-key",
        )
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    try:
        asyncio.run(
            runtime.run_json(
                system_prompt="Return an answer.",
                user_prompt="test request",
                output_model=Answer,
            )
        )
    except HermesOperatorActionRequiredError as exc:
        assert exc.code == "HERMES_PROVIDER_ACTION_REQUIRED"
        assert "http_status=402" in str(exc)
    else:
        raise AssertionError("provider HTTP error was not surfaced")


def test_chat_stream_keeps_reasoning_separate_from_answer(
    monkeypatch: MonkeyPatch,
) -> None:
    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            self.reasoning_callback = kwargs["reasoning_callback"]
            self.stream_delta_callback = kwargs["stream_delta_callback"]

        def run_conversation(self, **_: object) -> dict[str, object]:
            self.reasoning_callback("internal analysis")
            self.stream_delta_callback("final answer")
            return {
                "final_response": "final answer",
                "messages": [{"role": "assistant", "content": "final answer"}],
            }

    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_model="test-model",
            hermes_api_key="not-a-real-key",
        )
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    async def collect_events() -> list[tuple[str, dict[str, object]]]:
        return [
            (event.event, event.data)
            async for event in runtime.stream_chat(
                system_prompt="Use tools.",
                user_message="question",
                conversation_history=[],
                database_url="sqlite+pysqlite:///:memory:",
                tenant_id=uuid.uuid4(),
                administrative_unit_ids=(uuid.uuid4(),),
                run_id=uuid.uuid4(),
            )
        ]

    events = asyncio.run(collect_events())

    assert events[0] == ("reasoning.delta", {"text": "internal analysis"})
    assert events[1] == ("assistant.delta", {"delta": "final answer"})
    assert events[2][0] == "answer.completed"
    assert events[2][1]["content"] == "final answer"


def test_chat_stream_enables_clarify_and_separates_failed_tools(
    monkeypatch: MonkeyPatch,
) -> None:
    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            self.enabled_toolsets = kwargs["enabled_toolsets"]
            self.tool_start_callback = kwargs["tool_start_callback"]
            self.tool_complete_callback = kwargs["tool_complete_callback"]

        def run_conversation(self, **_: object) -> dict[str, object]:
            self.tool_start_callback("call-1", "query_metric", {})
            self.tool_complete_callback(
                "call-1",
                "query_metric",
                {},
                '{"status":"error","error_code":"invalid_metric_query"}',
            )
            return {
                "final_response": str(self.enabled_toolsets),
                "messages": [],
            }

    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_model="test-model",
            hermes_api_key="not-a-real-key",
        )
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    async def collect_events() -> list[tuple[str, dict[str, object]]]:
        return [
            (event.event, event.data)
            async for event in runtime.stream_chat(
                system_prompt="Use tools.",
                user_message="question",
                conversation_history=[],
                database_url="sqlite+pysqlite:///:memory:",
                tenant_id=uuid.uuid4(),
                administrative_unit_ids=(uuid.uuid4(),),
                run_id=uuid.uuid4(),
            )
        ]

    events = asyncio.run(collect_events())

    assert [event for event, _ in events] == [
        "tool.started",
        "tool.failed",
        "answer.completed",
    ]
    assert events[1][1]["duration_ms"] is not None
    assert events[1][1]["output"] == {
        "status": "error",
        "error_code": "invalid_metric_query",
    }
    assert events[2][1]["content"] == (
        "['village_query', 'clarify', 'code_execution']"
    )


def test_runtime_repairs_invalid_json_once(monkeypatch: MonkeyPatch) -> None:
    prompts: list[str] = []

    class FakeAgent:
        def __init__(self, **_: object) -> None:
            pass

        def run_conversation(self, **kwargs: object) -> dict[str, object]:
            prompts.append(str(kwargs["user_message"]))
            response = "not json" if len(prompts) == 1 else '{"value": "fixed"}'
            return {"final_response": response, "messages": []}

    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_model="test-model",
            hermes_api_key="not-a-real-key",
        )
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    result = asyncio.run(
        runtime.run_json_with_trace(
            system_prompt="Return an answer.",
            user_prompt="test request",
            output_model=Answer,
        )
    )

    assert result.value == Answer(value="fixed")
    assert result.trace.repair_attempted is True
    assert len(prompts) == 2
    assert "Validation errors:" in prompts[1]
    assert "$:json_invalid" not in prompts[1]
    assert '"path": "$"' in prompts[1]
    assert '"type": "json_invalid"' in prompts[1]
    assert "Expected target: one JSON object matching the JSON Schema" in prompts[1]


def test_runtime_reports_schema_paths_without_raw_response(
    monkeypatch: MonkeyPatch,
) -> None:
    raw_response = '{"value": 123, "private": "do-not-log"}'

    class FakeAgent:
        def __init__(self, **_: object) -> None:
            pass

        def run_conversation(self, **_: object) -> dict[str, object]:
            return {"final_response": raw_response, "messages": []}

    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_model="test-model",
            hermes_api_key="not-a-real-key",
        )
    )
    monkeypatch.setattr(runtime, "_agent_class", lambda: FakeAgent)

    with pytest.raises(HermesInvalidResponseError) as caught:
        asyncio.run(
            runtime.run_json(
                system_prompt="Return an answer.",
                user_prompt="test request",
                output_model=Answer,
            )
        )

    message = str(caught.value)
    assert "value:string_type" in message
    assert f"response_length={len(raw_response)}" in message
    assert "do-not-log" not in message


def test_runtime_timeout_terminates_isolated_agent_process(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    global TIMEOUT_PID_PATH
    TIMEOUT_PID_PATH = tmp_path / "agent.pid"
    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=SlowAgent))
    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_model="test-model",
            hermes_api_key="not-a-real-key",
            hermes_timeout_seconds=1,
        )
    )
    try:
        asyncio.run(
            runtime.run_json(
                system_prompt="Return an answer.",
                user_prompt="wait",
                output_model=Answer,
            )
        )
    except HermesUnavailableError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("timeout was not surfaced")
    child_pid = int(TIMEOUT_PID_PATH.read_text())
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("timed-out Hermes child process is still alive")
    TIMEOUT_PID_PATH = None


def test_runtime_maps_child_exit_without_payload_to_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=CrashingAgent))
    runtime = EmbeddedHermesRuntime(
        Settings(
            _env_file=None,
            hermes_enabled=True,
            hermes_model="test-model",
            hermes_api_key="not-a-real-key",
        )
    )

    with pytest.raises(HermesUnavailableError, match="stopped before returning"):
        asyncio.run(
            runtime.run_json(
                system_prompt="Return an answer.",
                user_prompt="crash",
                output_model=Answer,
            )
        )
