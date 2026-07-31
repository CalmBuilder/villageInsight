from __future__ import annotations

import asyncio
import importlib
import json
import multiprocessing
import os
import threading
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from multiprocessing.process import BaseProcess
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from village_insight.config import Settings

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class HermesConnection:
    provider: str
    model: str
    base_url: str
    api_key: str
    api_mode: Literal["openai_chat", "anthropic_messages"] = "openai_chat"
    thinking_protocol: Literal["none", "deepseek"] = "none"
    fast_model: str | None = None
    reasoning_model: str | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class HermesCallPolicy:
    thinking_enabled: bool = False
    reasoning_effort: Literal["high", "max"] = "high"
    max_tokens: int | None = None
    json_mode: bool = True
    enabled_toolsets: tuple[str, ...] = ()
    repair_attempts: int = 1
    timeout_seconds: int | None = None
    max_iterations: int | None = None

    def __post_init__(self) -> None:
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if self.repair_attempts not in {0, 1}:
            raise ValueError("repair_attempts must be 0 or 1")
        if self.timeout_seconds is not None and self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if self.max_iterations is not None and self.max_iterations < 1:
            raise ValueError("max_iterations must be positive")


@dataclass(frozen=True)
class HermesRunTrace:
    session_id: str
    provider: str
    model: str
    thinking_enabled: bool
    reasoning_effort: str | None
    max_tokens: int | None
    enabled_toolsets: tuple[str, ...]
    repair_attempted: bool
    message_count: int


@dataclass(frozen=True)
class HermesRunResult[ResultT: BaseModel]:
    value: ResultT
    trace: HermesRunTrace


@dataclass(frozen=True)
class HermesChatEvent:
    event: str
    data: dict[str, Any]


@dataclass(frozen=True)
class HermesChatResult:
    content: str
    tool_results: list[dict[str, Any]]
    trace: HermesRunTrace


class HermesUnavailableError(RuntimeError):
    pass


class HermesInvalidResponseError(RuntimeError):
    pass


class HermesRuntime(Protocol):
    async def run_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_model: type[ModelT],
        policy: HermesCallPolicy | None = None,
        task_id: str | None = None,
    ) -> ModelT: ...


_active_chat_processes: dict[str, BaseProcess] = {}
_active_chat_processes_lock = threading.Lock()


def _thinking_request_overrides(
    *,
    protocol: str,
    policy: HermesCallPolicy,
) -> dict[str, object]:
    if protocol != "deepseek":
        return {}
    overrides: dict[str, object] = {
        "extra_body": {
            "thinking": {
                "type": "enabled" if policy.thinking_enabled else "disabled"
            }
        }
    }
    if policy.thinking_enabled:
        overrides["reasoning_effort"] = policy.reasoning_effort
    return overrides


def stop_chat_run(run_id: str) -> bool:
    with _active_chat_processes_lock:
        process = _active_chat_processes.get(run_id)
    if process is None or not process.is_alive():
        return False
    process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    return True


class EmbeddedHermesRuntime:
    """Restricted boundary around the pinned hermes-agent distribution."""

    def __init__(
        self,
        settings: Settings,
        connection: HermesConnection | None = None,
    ) -> None:
        self.settings = settings
        self.connection = connection

    def _agent_class(self) -> type[Any]:
        if not self.settings.hermes_enabled:
            raise HermesUnavailableError("Hermes is disabled")
        try:
            module = importlib.import_module("run_agent")
            return module.AIAgent  # type: ignore[no-any-return]
        except (ImportError, AttributeError) as exc:
            raise HermesUnavailableError(
                "Pinned Hermes wheel is not installed or lacks run_agent.AIAgent"
            ) from exc

    def _default_policy(self) -> HermesCallPolicy:
        effort = self.settings.hermes_reasoning_effort.strip().lower()
        if effort not in {"high", "max"}:
            raise ValueError("HERMES_REASONING_EFFORT must be high or max")
        return HermesCallPolicy(
            thinking_enabled=self.settings.hermes_thinking_enabled,
            reasoning_effort=effort,  # type: ignore[arg-type]
            max_tokens=self.settings.hermes_max_tokens,
            enabled_toolsets=tuple(self.settings.hermes_enabled_toolsets),
        )

    def _settings_api_key(self, provider: str) -> str | None:
        normalized = provider.strip().lower()
        if normalized == "deepseek":
            return self.settings.deepseek_api_key
        if normalized == "siliconflow":
            return self.settings.llm_multimodal_api_key
        return self.settings.hermes_api_key

    async def run_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_model: type[ModelT],
        policy: HermesCallPolicy | None = None,
        task_id: str | None = None,
    ) -> ModelT:
        result = await self.run_json_with_trace(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_model=output_model,
            policy=policy,
            task_id=task_id,
        )
        return result.value

    async def run_json_with_trace(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_model: type[ModelT],
        policy: HermesCallPolicy | None = None,
        task_id: str | None = None,
    ) -> HermesRunResult[ModelT]:
        agent_class = self._agent_class()
        call_policy = policy or self._default_policy()
        configured_max_tokens = (
            self.connection.max_tokens
            if self.connection is not None
            else self.settings.hermes_max_tokens
        )
        effective_max_tokens = (
            call_policy.max_tokens if call_policy.max_tokens is not None else configured_max_tokens
        )
        effective_timeout = (
            call_policy.timeout_seconds
            if call_policy.timeout_seconds is not None
            else self.settings.hermes_timeout_seconds
        )
        session_id = task_id or uuid.uuid4().hex

        def invoke() -> HermesRunResult[ModelT]:
            connection = self.connection
            provider = (
                connection.provider if connection is not None else self.settings.hermes_provider
            ) or ""
            model = (
                connection.model if connection is not None else self.settings.hermes_model
            ) or ""
            fast_model = (
                connection.fast_model if connection is not None else self.settings.hermes_fast_model
            )
            reasoning_model = (
                connection.reasoning_model
                if connection is not None
                else self.settings.hermes_reasoning_model
            )
            if call_policy.thinking_enabled:
                model = reasoning_model or model
            else:
                model = fast_model or model
            base_url = (
                connection.base_url if connection is not None else self.settings.hermes_base_url
            )
            api_key = (
                connection.api_key if connection is not None else self._settings_api_key(provider)
            )
            reasoning_config: dict[str, object] = {
                "enabled": call_policy.thinking_enabled,
            }
            if call_policy.thinking_enabled:
                reasoning_config["effort"] = call_policy.reasoning_effort
            request_overrides: dict[str, object] = {}
            api_mode = (
                connection.api_mode
                if connection is not None
                else "openai_chat"
            )
            if call_policy.json_mode and api_mode == "openai_chat":
                request_overrides["response_format"] = {"type": "json_object"}
            request_overrides.update(
                _thinking_request_overrides(
                    protocol=(
                        connection.thinking_protocol
                        if connection is not None
                        else self.settings.hermes_thinking_protocol
                    ),
                    policy=call_policy,
                )
            )

            agent_options: dict[str, object] = {
                "base_url": base_url or None,
                "api_key": api_key or None,
                "provider": provider or None,
                "api_mode": (
                    "chat_completions"
                    if api_mode == "openai_chat"
                    else api_mode
                ),
                "model": model,
                "quiet_mode": True,
                "skip_context_files": True,
                "skip_memory": True,
                "max_iterations": (
                    call_policy.max_iterations
                    or self.settings.hermes_max_iterations
                ),
                "reasoning_config": reasoning_config,
                "request_overrides": request_overrides,
                "enabled_toolsets": list(call_policy.enabled_toolsets),
                "load_soul_identity": False,
                "checkpoints_enabled": False,
                "session_id": session_id,
                "ephemeral_system_prompt": (
                    f"{system_prompt}\n\n"
                    "Return JSON only, matching this JSON Schema. "
                    "Do not add markdown fences or commentary.\n"
                    f"{json.dumps(output_model.model_json_schema(), ensure_ascii=False)}"
                ),
            }
            if effective_max_tokens is not None:
                agent_options["max_tokens"] = effective_max_tokens

            agent = agent_class(
                **agent_options,
            )

            repair_attempted = False
            message_count = 0
            raw = ""
            for attempt in range(call_policy.repair_attempts + 1):
                prompt = user_prompt
                if attempt:
                    repair_attempted = True
                    prompt = (
                        "The previous response was not valid JSON for the required schema. "
                        "Return a corrected JSON object only. Previous response:\n"
                        f"{raw[:4000]}"
                    )
                conversation = agent.run_conversation(
                    user_message=prompt,
                    task_id=session_id,
                )
                raw = str(conversation.get("final_response") or "")
                messages = conversation.get("messages")
                if isinstance(messages, list):
                    message_count += len(messages)
                if raw.lstrip().startswith("HTTP "):
                    raise HermesUnavailableError(
                        f"Hermes provider request failed: {raw.strip()[:500]}"
                    )
                try:
                    value = output_model.model_validate_json(raw)
                    return HermesRunResult(
                        value=value,
                        trace=HermesRunTrace(
                            session_id=session_id,
                            provider=provider,
                            model=model,
                            thinking_enabled=call_policy.thinking_enabled,
                            reasoning_effort=(
                                call_policy.reasoning_effort
                                if call_policy.thinking_enabled
                                else None
                            ),
                            max_tokens=effective_max_tokens,
                            enabled_toolsets=call_policy.enabled_toolsets,
                            repair_attempted=repair_attempted,
                            message_count=message_count,
                        ),
                    )
                except ValidationError:
                    if attempt == call_policy.repair_attempts:
                        break
            raise HermesInvalidResponseError(
                "Hermes returned a response that does not match the required JSON schema"
            )

        if "_agent_class" in self.__dict__:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(invoke),
                    timeout=effective_timeout,
                )
            except TimeoutError:
                raise HermesUnavailableError("Hermes execution timed out") from None

        context = multiprocessing.get_context("fork")
        receiver, sender = context.Pipe(duplex=False)

        def run_isolated() -> None:
            try:
                sender.send(("ok", invoke()))
            except Exception as exc:
                sender.send(("error", type(exc).__name__, str(exc)))
            finally:
                sender.close()

        process = context.Process(target=run_isolated, daemon=True)
        process.start()
        sender.close()
        try:
            ready = await asyncio.to_thread(
                receiver.poll,
                effective_timeout,
            )
        except BaseException:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            receiver.close()
            raise
        if not ready:
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            receiver.close()
            raise HermesUnavailableError("Hermes execution timed out")
        message = receiver.recv()
        receiver.close()
        process.join(timeout=5)
        if message[0] == "ok":
            return message[1]  # type: ignore[no-any-return]
        error_type, error_message = message[1], message[2]
        if error_type == "HermesInvalidResponseError":
            raise HermesInvalidResponseError(error_message)
        raise HermesUnavailableError(error_message)

    async def stream_chat(
        self,
        *,
        system_prompt: str,
        user_message: str,
        conversation_history: list[dict[str, Any]],
        database_url: str,
        tenant_id: uuid.UUID,
        administrative_unit_ids: tuple[uuid.UUID, ...],
        run_id: uuid.UUID,
        source_item_ids: tuple[uuid.UUID, ...] = (),
        source_scope_enforced: bool = False,
        record_created_before: datetime | None = None,
        catalog_snapshot: dict[str, Any] | None = None,
        policy: HermesCallPolicy | None = None,
    ) -> AsyncIterator[HermesChatEvent]:
        """Run one isolated embedded Agent turn and stream safe UI events."""

        agent_class = self._agent_class()
        call_policy = policy or HermesCallPolicy(
            thinking_enabled=False,
            max_tokens=self.settings.hermes_max_tokens,
            json_mode=False,
            enabled_toolsets=("village_query", "clarify", "code_execution"),
            repair_attempts=0,
            timeout_seconds=self.settings.hermes_timeout_seconds,
        )
        effective_timeout = (
            call_policy.timeout_seconds
            if call_policy.timeout_seconds is not None
            else self.settings.hermes_timeout_seconds
        )
        configured_max_tokens = (
            self.connection.max_tokens
            if self.connection is not None
            else self.settings.hermes_max_tokens
        )
        effective_max_tokens = (
            call_policy.max_tokens
            if call_policy.max_tokens is not None
            else configured_max_tokens
        )
        run_key = str(run_id)
        from village_insight.hermes.read_only_database import (
            ensure_hermes_readonly_database_url,
        )

        readonly_database_url = ensure_hermes_readonly_database_url(
            database_url
        )
        context = multiprocessing.get_context("fork")
        receiver, sender = context.Pipe(duplex=False)

        def run_isolated() -> None:
            send_lock = threading.Lock()
            tool_started_at: dict[str, float] = {}

            def send(event: str, data: dict[str, Any]) -> None:
                with send_lock:
                    sender.send(("event", event, data))

            def tool_started(call_id: str, name: str, args: object) -> None:
                normalized_call_id = str(call_id)
                tool_started_at[normalized_call_id] = time.perf_counter()
                send(
                    "tool.started",
                    {
                        "tool_call_id": normalized_call_id,
                        "tool_name": str(name),
                        "arguments": args if isinstance(args, dict) else {},
                    },
                )

            def tool_completed(
                call_id: str,
                name: str,
                _args: object,
                result: object,
            ) -> None:
                output: object = result
                if isinstance(result, str):
                    try:
                        output = json.loads(result)
                    except json.JSONDecodeError:
                        output = {"message": result[:2_000]}
                normalized_call_id = str(call_id)
                started_at = tool_started_at.pop(normalized_call_id, None)
                duration_ms = (
                    max(0, round((time.perf_counter() - started_at) * 1_000))
                    if started_at is not None
                    else None
                )
                event_name = (
                    "tool.failed"
                    if isinstance(output, dict) and output.get("status") == "error"
                    else "tool.completed"
                )
                send(
                    event_name,
                    {
                        "tool_call_id": normalized_call_id,
                        "tool_name": str(name),
                        "output": output,
                        "duration_ms": duration_ms,
                    },
                )

            def stream_delta(delta: str) -> None:
                if delta:
                    send("assistant.delta", {"delta": str(delta)})

            def reasoning_delta(delta: str) -> None:
                if delta:
                    send("reasoning.delta", {"text": str(delta)})

            def clarify(question: str, choices: object = None) -> str:
                normalized_choices = (
                    [str(choice) for choice in choices]
                    if isinstance(choices, list)
                    else []
                )
                send(
                    "clarify.requested",
                    {
                        "question": str(question),
                        "choices": normalized_choices,
                    },
                )
                return (
                    "The user will answer this clarification in the next turn. "
                    "End this turn by asking the same question without inventing an answer."
                )

            try:
                readonly_environment = {
                    "VILLAGE_INSIGHT_QUERY_DATABASE_URL": readonly_database_url,
                    "VILLAGE_INSIGHT_QUERY_TENANT_ID": str(tenant_id),
                    "VILLAGE_INSIGHT_QUERY_UNIT_IDS": ",".join(
                        str(value) for value in administrative_unit_ids
                    ),
                    "VILLAGE_INSIGHT_QUERY_SOURCE_ITEM_IDS": ",".join(
                        str(value) for value in source_item_ids
                    ),
                    "VILLAGE_INSIGHT_QUERY_SOURCE_SCOPE_ENFORCED": (
                        "1" if source_scope_enforced else "0"
                    ),
                    "VILLAGE_INSIGHT_QUERY_RECORD_CREATED_BEFORE": (
                        record_created_before.isoformat()
                        if record_created_before is not None
                        else ""
                    ),
                }
                os.environ.update(readonly_environment)
                from tools.approval import approve_session  # type: ignore[import-untyped]
                from tools.env_passthrough import (  # type: ignore[import-untyped]
                    register_env_passthrough,
                )

                register_env_passthrough(readonly_environment)
                approve_session(run_key, "execute_code")

                from village_insight.hermes.question_tools import (
                    QuestionToolContext,
                    activate_question_tools,
                    current_tool_results,
                )

                activate_question_tools(
                    QuestionToolContext(
                        database_url=database_url,
                        tenant_id=tenant_id,
                        administrative_unit_ids=administrative_unit_ids,
                        run_id=run_id,
                        source_item_ids=source_item_ids,
                        source_scope_enforced=source_scope_enforced,
                        record_created_before=record_created_before,
                        catalog_snapshot=catalog_snapshot or {},
                    )
                )
                connection = self.connection
                provider = (
                    connection.provider
                    if connection is not None
                    else self.settings.hermes_provider
                ) or ""
                model = (
                    connection.model
                    if connection is not None
                    else self.settings.hermes_model
                ) or ""
                fast_model = (
                    connection.fast_model
                    if connection is not None
                    else self.settings.hermes_fast_model
                )
                reasoning_model = (
                    connection.reasoning_model
                    if connection is not None
                    else self.settings.hermes_reasoning_model
                )
                if call_policy.thinking_enabled:
                    model = reasoning_model or model
                else:
                    model = fast_model or model
                base_url = (
                    connection.base_url
                    if connection is not None
                    else self.settings.hermes_base_url
                )
                api_key = (
                    connection.api_key
                    if connection is not None
                    else self._settings_api_key(provider)
                )
                request_overrides = _thinking_request_overrides(
                    protocol=(
                        connection.thinking_protocol
                        if connection is not None
                        else self.settings.hermes_thinking_protocol
                    ),
                    policy=call_policy,
                )
                api_mode = (
                    connection.api_mode
                    if connection is not None
                    else "openai_chat"
                )
                agent_options: dict[str, object] = {
                    "base_url": base_url or None,
                    "api_key": api_key or None,
                    "provider": provider or None,
                    "api_mode": (
                        "chat_completions"
                        if api_mode == "openai_chat"
                        else api_mode
                    ),
                    "model": model,
                    "quiet_mode": True,
                    "skip_context_files": True,
                    "skip_memory": True,
                    "max_iterations": (
                        call_policy.max_iterations
                        or self.settings.hermes_max_iterations
                    ),
                    "reasoning_config": {
                        "enabled": call_policy.thinking_enabled,
                        **(
                            {"effort": call_policy.reasoning_effort}
                            if call_policy.thinking_enabled
                            else {}
                        ),
                    },
                    "request_overrides": request_overrides,
                    "enabled_toolsets": list(call_policy.enabled_toolsets),
                    "disabled_toolsets": [
                        "terminal",
                        "file",
                        "web",
                        "browser",
                        "memory",
                    ],
                    "load_soul_identity": False,
                    "checkpoints_enabled": False,
                    "session_id": run_key,
                    "ephemeral_system_prompt": system_prompt,
                    "tool_start_callback": tool_started,
                    "tool_complete_callback": tool_completed,
                    "stream_delta_callback": stream_delta,
                    "reasoning_callback": reasoning_delta,
                    "clarify_callback": clarify,
                }
                if effective_max_tokens is not None:
                    agent_options["max_tokens"] = effective_max_tokens
                agent = agent_class(**agent_options)
                conversation = agent.run_conversation(
                    user_message=user_message,
                    conversation_history=conversation_history,
                    task_id=run_key,
                )
                content = str(conversation.get("final_response") or "")
                messages = conversation.get("messages")
                trace = HermesRunTrace(
                    session_id=run_key,
                    provider=provider,
                    model=model,
                    thinking_enabled=call_policy.thinking_enabled,
                    reasoning_effort=(
                        call_policy.reasoning_effort
                        if call_policy.thinking_enabled
                        else None
                    ),
                    max_tokens=effective_max_tokens,
                    enabled_toolsets=call_policy.enabled_toolsets,
                    repair_attempted=False,
                    message_count=len(messages) if isinstance(messages, list) else 0,
                )
                sender.send(
                    (
                        "result",
                        HermesChatResult(
                            content=content,
                            tool_results=current_tool_results(),
                            trace=trace,
                        ),
                    )
                )
            except Exception as exc:
                sender.send(("error", type(exc).__name__, str(exc)))
            finally:
                sender.close()

        process = context.Process(target=run_isolated, daemon=True)
        process.start()
        sender.close()
        with _active_chat_processes_lock:
            _active_chat_processes[run_key] = process
        loop = asyncio.get_running_loop()
        deadline = loop.time() + effective_timeout
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    stop_chat_run(run_key)
                    raise HermesUnavailableError("Hermes execution timed out")
                ready = await asyncio.to_thread(
                    receiver.poll,
                    min(0.25, remaining),
                )
                if not ready:
                    if not process.is_alive():
                        raise HermesUnavailableError(
                            "Hermes process stopped before returning a result"
                        )
                    continue
                message = receiver.recv()
                if message[0] == "event":
                    yield HermesChatEvent(
                        event=str(message[1]),
                        data=dict(message[2]),
                    )
                    continue
                if message[0] == "result":
                    result: HermesChatResult = message[1]
                    yield HermesChatEvent(
                        event="answer.completed",
                        data={
                            "content": result.content,
                            "tool_results": result.tool_results,
                            "trace": {
                                "session_id": result.trace.session_id,
                                "provider": result.trace.provider,
                                "model": result.trace.model,
                                "message_count": result.trace.message_count,
                            },
                        },
                    )
                    break
                error_type, error_message = message[1], message[2]
                raise HermesUnavailableError(
                    f"{error_type}: {error_message}"
                )
        finally:
            receiver.close()
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            with _active_chat_processes_lock:
                _active_chat_processes.pop(run_key, None)
