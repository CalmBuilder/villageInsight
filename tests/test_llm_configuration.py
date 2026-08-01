from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.config import Settings
from village_insight.db.base import Base
from village_insight.db.models import LLMConfiguration, LLMProviderCredential
from village_insight.db.schema import LLMConfigurationUpdate
from village_insight.hermes.configuration import (
    configuration_read,
    draft_connection,
    environment_connection,
    resolve_configuration,
    save_configuration,
)
from village_insight.hermes.endpoints import validate_endpoint_url


def test_configuration_moves_environment_secret_to_encrypted_storage(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        secret_key_path=tmp_path / "settings.key",
        hermes_provider="siliconflow",
        hermes_model="Qwen/Qwen3-8B",
        hermes_fast_model="Qwen/Qwen3-8B",
        hermes_reasoning_model="deepseek-ai/DeepSeek-V4-Pro",
        hermes_base_url="https://api.siliconflow.cn/v1",
        hermes_thinking_protocol="deepseek",
        llm_multimodal_api_key="not-a-real-siliconflow-key",
        hermes_max_tokens=4096,
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as database:
        initial = configuration_read(database, settings)
        assert initial.source == "environment"
        assert initial.preset_id == "siliconflow"
        assert initial.api_mode == "openai_chat"
        assert initial.api_key_hint == "••••••••-key"
        assert initial.fast_model == "Qwen/Qwen3-8B"
        assert initial.reasoning_model == "deepseek-ai/DeepSeek-V4-Pro"
        assert initial.thinking_protocol == "deepseek"
        assert initial.max_tokens == 4096

        saved = save_configuration(
            database,
            settings,
            LLMConfigurationUpdate(
                provider="siliconflow",
                preset_id="siliconflow",
                api_mode="openai_chat",
                model="Qwen/Qwen3-8B",
                fast_model="Qwen/Qwen3-8B",
                reasoning_model="deepseek-ai/DeepSeek-V4-Pro",
                base_url="https://api.siliconflow.cn/v1/",
                thinking_protocol="none",
                max_tokens=8192,
            ),
        )
        stored = database.get(LLMConfiguration, "default")
        credential = database.get(LLMProviderCredential, "siliconflow")
        assert stored is not None
        assert credential is not None
        assert "not-a-real-siliconflow-key" not in stored.encrypted_api_key
        assert "not-a-real-siliconflow-key" not in credential.encrypted_api_key
        assert saved.source == "database"
        assert saved.preset_id == "siliconflow"
        assert saved.api_mode == "openai_chat"
        assert saved.base_url == "https://api.siliconflow.cn/v1"
        assert saved.fast_model == "Qwen/Qwen3-8B"
        assert saved.reasoning_model == "deepseek-ai/DeepSeek-V4-Pro"
        assert saved.thinking_protocol == "none"
        assert saved.max_tokens == 8192
        assert stored.max_tokens == 8192
        assert resolve_configuration(database, settings).connection.api_key == (
            "not-a-real-siliconflow-key"
        )

    assert (tmp_path / "settings.key").stat().st_mode & 0o777 == 0o600


def test_environment_key_is_bound_to_selected_provider() -> None:
    settings = Settings(
        _env_file=None,
        hermes_provider="deepseek",
        hermes_model="deepseek-v4-flash",
        hermes_base_url="https://api.deepseek.com",
        hermes_api_key="generic-key",
        llm_multimodal_api_key="siliconflow-key",
        deepseek_api_key="deepseek-key",
    )

    assert environment_connection(settings).api_key == "deepseek-key"
    assert (
        environment_connection(
            settings.model_copy(update={"hermes_provider": "siliconflow"})
        ).api_key
        == "siliconflow-key"
    )


def test_draft_connection_does_not_reuse_key_for_another_provider(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        secret_key_path=tmp_path / "settings.key",
        hermes_provider="deepseek",
        hermes_model="deepseek-v4-flash",
        hermes_base_url="https://api.deepseek.com",
        deepseek_api_key="deepseek-key",
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    payload = LLMConfigurationUpdate(
        provider="dashscope",
        preset_id="qwen_anthropic",
        api_mode="anthropic_messages",
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/apps/anthropic",
    )

    with Session(engine) as database:
        with pytest.raises(ValueError, match="API Key"):
            draft_connection(database, settings, payload)
        connection = draft_connection(
            database,
            settings,
            payload.model_copy(update={"api_key": "new-dashscope-key"}),
        )

    assert connection.api_mode == "anthropic_messages"
    assert connection.api_key == "new-dashscope-key"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.4/v1",
        "https://198.18.0.8/v1",
        "https://user:password@example.com/v1",
    ],
)
def test_endpoint_validation_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_endpoint_url(url, resolve=False)
