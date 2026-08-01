from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from sqlalchemy.orm import Session

from village_insight.config import Settings
from village_insight.db.models import LLMConfiguration, LLMProviderCredential
from village_insight.db.schema import (
    LLMConfigurationRead,
    LLMConfigurationUpdate,
    LLMProviderPresetRead,
)
from village_insight.hermes.endpoints import validate_endpoint_url
from village_insight.hermes.runtime import HermesConnection
from village_insight.hermes.secrets import SecretCipher, SecretDecryptionError

CONFIGURATION_ID = "default"

PROVIDER_PRESETS: tuple[LLMProviderPresetRead, ...] = (
    LLMProviderPresetRead(
        id="deepseek",
        name="DeepSeek",
        provider="deepseek",
        api_mode="openai_chat",
        base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        fast_model="deepseek-v4-flash",
        reasoning_model="deepseek-v4-pro",
        supports_model_discovery=False,
        description="官方 OpenAI 兼容接口，快速与推理模型分开路由。",
    ),
    LLMProviderPresetRead(
        id="siliconflow",
        name="硅基流动",
        provider="siliconflow",
        api_mode="openai_chat",
        base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen3-8B",
        fast_model="Qwen/Qwen3-8B",
        reasoning_model="deepseek-ai/DeepSeek-V3.2",
        supports_model_discovery=True,
        description="通过模型发现选择当前账户可用的文本模型。",
    ),
    LLMProviderPresetRead(
        id="qwen_openai",
        name="阿里百炼 · OpenAI",
        provider="dashscope",
        api_mode="openai_chat",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        fast_model="qwen-flash",
        reasoning_model="qwen-plus",
        supports_model_discovery=True,
        description="百炼按量付费 OpenAI 兼容入口。",
    ),
    LLMProviderPresetRead(
        id="qwen_anthropic",
        name="阿里百炼 · Anthropic",
        provider="dashscope",
        api_mode="anthropic_messages",
        base_url="https://dashscope.aliyuncs.com/apps/anthropic",
        default_model="qwen-plus",
        fast_model="qwen-flash",
        reasoning_model="qwen-plus",
        supports_model_discovery=False,
        description="百炼按量付费 Anthropic Messages 兼容入口。",
    ),
    LLMProviderPresetRead(
        id="volcengine_openai",
        name="火山 Ark 套餐 · OpenAI",
        provider="volcengine",
        api_mode="openai_chat",
        base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        default_model="kimi-k3",
        fast_model="kimi-k3",
        reasoning_model="kimi-k3",
        supports_model_discovery=True,
        description="火山引擎套餐的 OpenAI/Responses 兼容入口。",
        billing_notice="保持套餐专用 /api/plan/v3 地址，避免切换到普通按量接口。",
    ),
    LLMProviderPresetRead(
        id="volcengine_anthropic",
        name="火山 Ark 套餐 · Anthropic",
        provider="volcengine",
        api_mode="anthropic_messages",
        base_url="https://ark.cn-beijing.volces.com/api/plan",
        default_model="kimi-k3",
        fast_model="kimi-k3",
        reasoning_model="kimi-k3",
        supports_model_discovery=False,
        description="火山引擎套餐的 Anthropic Messages 兼容入口。",
        billing_notice="保持套餐专用 /api/plan 地址，避免切换到普通按量接口。",
    ),
    LLMProviderPresetRead(
        id="custom_openai",
        name="自定义 OpenAI 兼容",
        provider="custom",
        api_mode="openai_chat",
        base_url="https://example.com/v1",
        default_model="custom-model",
        fast_model="custom-model",
        reasoning_model="custom-model",
        supports_model_discovery=True,
        description="连接实现 OpenAI Chat Completions 的 HTTPS 服务。",
    ),
    LLMProviderPresetRead(
        id="custom_anthropic",
        name="自定义 Anthropic 兼容",
        provider="custom",
        api_mode="anthropic_messages",
        base_url="https://example.com",
        default_model="custom-model",
        fast_model="custom-model",
        reasoning_model="custom-model",
        supports_model_discovery=False,
        description="连接实现 Anthropic Messages 的 HTTPS 服务。",
    ),
)


@dataclass(frozen=True)
class ResolvedLLMConfiguration:
    connection: HermesConnection
    source: str
    updated_at: datetime | None


def environment_connection(settings: Settings) -> HermesConnection:
    provider = settings.hermes_provider or ""
    return HermesConnection(
        provider=provider,
        model=settings.hermes_model or "",
        base_url=settings.hermes_base_url or "",
        api_key=environment_api_key(settings, provider),
        api_mode="openai_chat",
        thinking_protocol=settings.hermes_thinking_protocol,
        fast_model=settings.hermes_fast_model,
        reasoning_model=settings.hermes_reasoning_model,
        max_tokens=settings.hermes_max_tokens,
    )


def environment_api_key(settings: Settings, provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "deepseek":
        return settings.deepseek_api_key or ""
    if normalized == "siliconflow":
        return settings.llm_multimodal_api_key or ""
    return settings.hermes_api_key or ""


def resolve_configuration(
    database: Session,
    settings: Settings,
) -> ResolvedLLMConfiguration:
    stored = database.get(LLMConfiguration, CONFIGURATION_ID)
    if stored is None:
        return ResolvedLLMConfiguration(
            connection=environment_connection(settings),
            source="environment",
            updated_at=None,
        )
    if stored.thinking_protocol not in {"none", "deepseek"}:
        raise ValueError(f"Unsupported thinking protocol: {stored.thinking_protocol}")
    if stored.api_mode not in {"openai_chat", "anthropic_messages"}:
        raise ValueError(f"Unsupported API mode: {stored.api_mode}")
    api_key = SecretCipher(settings.resolved_secret_key_path()).decrypt(stored.encrypted_api_key)
    return ResolvedLLMConfiguration(
        connection=HermesConnection(
            provider=stored.provider,
            model=stored.model,
            base_url=stored.base_url,
            api_key=api_key,
            api_mode=cast(
                Literal["openai_chat", "anthropic_messages"],
                stored.api_mode,
            ),
            thinking_protocol=cast(
                Literal["none", "deepseek"],
                stored.thinking_protocol,
            ),
            fast_model=stored.fast_model,
            reasoning_model=stored.reasoning_model,
            max_tokens=stored.max_tokens,
        ),
        source="database",
        updated_at=stored.updated_at,
    )


def api_key_hint(api_key: str) -> str | None:
    if not api_key:
        return None
    return f"••••••••{api_key[-4:]}"


def inferred_preset_id(provider: str, api_mode: str) -> str:
    normalized = provider.strip().lower()
    for preset in PROVIDER_PRESETS:
        if preset.provider == normalized and preset.api_mode == api_mode:
            return preset.id
    return "custom_anthropic" if api_mode == "anthropic_messages" else "custom_openai"


def provider_presets_read(
    database: Session,
    settings: Settings,
) -> list[LLMProviderPresetRead]:
    cipher = SecretCipher(settings.resolved_secret_key_path())
    environment = environment_connection(settings)
    environment_preset_id = inferred_preset_id(
        environment.provider,
        environment.api_mode,
    )
    result: list[LLMProviderPresetRead] = []
    for preset in PROVIDER_PRESETS:
        stored = database.get(LLMProviderCredential, preset.id)
        reentry_required = False
        if stored is not None:
            try:
                key = cipher.decrypt(stored.encrypted_api_key)
            except SecretDecryptionError:
                key = ""
                reentry_required = True
        elif preset.id == environment_preset_id:
            key = environment.api_key
        else:
            key = ""
        result.append(
            preset.model_copy(
                update={
                    "api_key_configured": bool(key),
                    "api_key_hint": api_key_hint(key),
                    "api_key_reentry_required": reentry_required,
                }
            )
        )
    return result


def configuration_read(
    database: Session,
    settings: Settings,
) -> LLMConfigurationRead:
    stored = database.get(LLMConfiguration, CONFIGURATION_ID)
    try:
        resolved = resolve_configuration(database, settings)
    except SecretDecryptionError:
        if stored is None:
            raise
        return LLMConfigurationRead(
            provider=stored.provider,
            preset_id=stored.preset_id,
            api_mode=cast(
                Literal["openai_chat", "anthropic_messages"],
                stored.api_mode,
            ),
            model=stored.model,
            fast_model=stored.fast_model,
            reasoning_model=stored.reasoning_model,
            base_url=stored.base_url,
            thinking_protocol=cast(
                Literal["none", "deepseek"],
                stored.thinking_protocol,
            ),
            api_key_configured=False,
            api_key_hint=None,
            api_key_reentry_required=True,
            max_tokens=stored.max_tokens,
            source="database",
            updated_at=stored.updated_at,
        )
    connection = resolved.connection
    return LLMConfigurationRead(
        provider=connection.provider,
        preset_id=(
            stored.preset_id
            if stored is not None
            else inferred_preset_id(connection.provider, connection.api_mode)
        ),
        api_mode=connection.api_mode,
        model=connection.model,
        fast_model=connection.fast_model or connection.model,
        reasoning_model=connection.reasoning_model or connection.model,
        base_url=connection.base_url,
        thinking_protocol=connection.thinking_protocol,
        api_key_configured=bool(connection.api_key),
        api_key_hint=api_key_hint(connection.api_key),
        api_key_reentry_required=False,
        max_tokens=connection.max_tokens,
        source=resolved.source,
        updated_at=resolved.updated_at,
    )


def draft_connection(
    database: Session,
    settings: Settings,
    payload: LLMConfigurationUpdate,
) -> HermesConnection:
    base_url = validate_endpoint_url(payload.base_url, resolve=False)
    api_key = payload.api_key or ""
    decryption_failed = False
    if not api_key:
        cipher = SecretCipher(settings.resolved_secret_key_path())
        stored_credential = database.get(LLMProviderCredential, payload.preset_id)
        if (
            stored_credential is not None
            and stored_credential.provider == payload.provider
            and stored_credential.api_mode == payload.api_mode
            and stored_credential.base_url.rstrip("/") == base_url
        ):
            try:
                api_key = cipher.decrypt(stored_credential.encrypted_api_key)
            except SecretDecryptionError:
                decryption_failed = True
        if not api_key:
            try:
                current = resolve_configuration(database, settings).connection
            except SecretDecryptionError:
                decryption_failed = True
            else:
                can_reuse_current_key = (
                    current.provider == payload.provider
                    and current.base_url.rstrip("/") == base_url
                    and current.api_mode == payload.api_mode
                )
                if can_reuse_current_key:
                    api_key = current.api_key
    if not api_key:
        if decryption_failed:
            raise ValueError("已保存的 API Key 无法解密，请重新输入 API Key")
        raise ValueError("请为这个连接输入 API Key 后再测试")
    if payload.api_mode == "anthropic_messages" and payload.thinking_protocol != "none":
        raise ValueError("Anthropic Messages 连接不能使用 DeepSeek 思考参数")
    return HermesConnection(
        provider=payload.provider,
        model=payload.model,
        base_url=base_url,
        api_key=api_key,
        api_mode=payload.api_mode,
        thinking_protocol=payload.thinking_protocol,
        fast_model=payload.fast_model or payload.model,
        reasoning_model=payload.reasoning_model or payload.model,
        max_tokens=payload.max_tokens,
    )


def save_configuration(
    database: Session,
    settings: Settings,
    payload: LLMConfigurationUpdate,
) -> LLMConfigurationRead:
    connection = draft_connection(database, settings, payload)
    api_key = connection.api_key
    encrypted = SecretCipher(settings.resolved_secret_key_path()).encrypt(api_key)
    stored = database.get(LLMConfiguration, CONFIGURATION_ID)
    if stored is None:
        stored = LLMConfiguration(id=CONFIGURATION_ID)
        database.add(stored)
    stored.provider = payload.provider
    stored.preset_id = payload.preset_id
    stored.api_mode = payload.api_mode
    stored.model = payload.model
    stored.fast_model = payload.fast_model or payload.model
    stored.reasoning_model = payload.reasoning_model or payload.model
    stored.base_url = payload.base_url.rstrip("/")
    stored.thinking_protocol = payload.thinking_protocol
    stored.max_tokens = payload.max_tokens
    stored.encrypted_api_key = encrypted
    credential = database.get(LLMProviderCredential, payload.preset_id)
    if credential is None:
        credential = LLMProviderCredential(preset_id=payload.preset_id)
        database.add(credential)
    credential.provider = payload.provider
    credential.api_mode = payload.api_mode
    credential.base_url = payload.base_url.rstrip("/")
    credential.encrypted_api_key = encrypted
    database.commit()
    database.refresh(stored)
    return configuration_read(database, settings)
