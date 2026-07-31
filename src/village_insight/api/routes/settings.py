from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException

from village_insight.api.dependencies import Database, require_governor
from village_insight.config import get_settings
from village_insight.db.schema import (
    LLMConfigurationRead,
    LLMConfigurationUpdate,
    LLMConnectionTestResult,
    LLMModelDiscoveryResult,
    LLMProviderPresetRead,
)
from village_insight.hermes.configuration import (
    configuration_read,
    draft_connection,
    provider_presets_read,
    resolve_configuration,
    save_configuration,
)
from village_insight.hermes.discovery import discover_models
from village_insight.hermes.endpoints import validate_endpoint_url
from village_insight.hermes.live_check import LiveCheckResult
from village_insight.hermes.runtime import (
    EmbeddedHermesRuntime,
    HermesCallPolicy,
    HermesInvalidResponseError,
    HermesUnavailableError,
)

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_governor)],
)


@router.get("/llm", response_model=LLMConfigurationRead)
def get_llm_configuration(database: Database) -> LLMConfigurationRead:
    return configuration_read(database, get_settings())


@router.put("/llm", response_model=LLMConfigurationRead)
def update_llm_configuration(
    payload: LLMConfigurationUpdate,
    database: Database,
) -> LLMConfigurationRead:
    try:
        return save_configuration(database, get_settings(), payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/llm/presets", response_model=list[LLMProviderPresetRead])
def get_llm_provider_presets(database: Database) -> list[LLMProviderPresetRead]:
    return provider_presets_read(database, get_settings())


@router.post("/llm/models", response_model=LLMModelDiscoveryResult)
def get_llm_models(
    payload: LLMConfigurationUpdate,
    database: Database,
) -> LLMModelDiscoveryResult:
    try:
        connection = draft_connection(database, get_settings(), payload)
        return discover_models(connection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/llm/test", response_model=LLMConnectionTestResult)
async def test_llm_configuration(
    database: Database,
    payload: LLMConfigurationUpdate | None = None,
) -> LLMConnectionTestResult:
    settings = get_settings()
    try:
        connection = (
            draft_connection(database, settings, payload)
            if payload is not None
            else resolve_configuration(database, settings).connection
        )
        validate_endpoint_url(connection.base_url, resolve=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    started = perf_counter()
    try:
        await EmbeddedHermesRuntime(settings, connection).run_json(
            system_prompt="Return the requested connectivity JSON only. Do not use tools.",
            user_prompt='Return {"status":"ok","component":"hermes-provider"}.',
            output_model=LiveCheckResult,
            policy=HermesCallPolicy(
                thinking_enabled=False,
                max_tokens=128,
                enabled_toolsets=(),
                repair_attempts=0,
            ),
            task_id="settings-connection-test",
        )
    except (HermesUnavailableError, HermesInvalidResponseError) as exc:
        message = str(exc)
        normalized = message.lower()
        if any(marker in normalized for marker in ("401", "403", "api key", "unauthorized")):
            detail = "API Key 无效或没有调用该模型的权限"
        elif any(marker in normalized for marker in ("404", "model not found")):
            detail = "模型不存在，或当前账户没有该模型权限"
        elif "429" in normalized or "rate limit" in normalized:
            detail = "供应商正在限流，请稍后重试"
        elif "timed out" in normalized or "timeout" in normalized:
            detail = "模型调用超时"
        elif isinstance(exc, HermesInvalidResponseError):
            detail = "连接成功，但模型未返回要求的结构化 JSON"
        else:
            detail = "供应商连接失败，请检查协议、Base URL 和模型名称"
        raise HTTPException(status_code=422, detail=detail) from exc
    return LLMConnectionTestResult(
        status="ok",
        provider=connection.provider,
        model=connection.fast_model or connection.model,
        api_mode=connection.api_mode,
        latency_ms=round((perf_counter() - started) * 1000),
        stages=["endpoint", "structured_json"],
    )
