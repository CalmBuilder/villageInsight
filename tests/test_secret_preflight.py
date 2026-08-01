from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import LLMProviderCredential
from village_insight.hermes.secrets import SecretCipher, SecretDecryptionError
from village_insight.secret_preflight import SecretPreflightError, prepare_settings_key


def _database() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_preflight_creates_and_reuses_key_for_empty_database(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "settings.key"
    with _database() as database:
        first = prepare_settings_key(database, path)
        first_key = path.read_bytes()
        second = prepare_settings_key(database, path)

    assert first.created is True
    assert first.reentry_required is False
    assert second.created is False
    assert second.reentry_required is False
    assert second.fingerprint == first.fingerprint
    assert path.read_bytes() == first_key
    assert path.stat().st_mode & 0o777 == 0o600


def test_preflight_degrades_without_new_key_when_database_has_ciphertext(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.key"
    with _database() as database:
        database.add(
            LLMProviderCredential(
                preset_id="deepseek",
                provider="deepseek",
                api_mode="openai_chat",
                base_url="https://api.deepseek.com",
                encrypted_api_key="existing-ciphertext",
            )
        )
        database.commit()

        result = prepare_settings_key(database, path)

    assert result.reentry_required is True
    assert result.fingerprint is None
    assert not path.exists()


def test_preflight_never_overwrites_invalid_existing_key(tmp_path: Path) -> None:
    path = tmp_path / "settings.key"
    path.write_bytes(b"invalid")
    with _database() as database:
        with pytest.raises(SecretPreflightError, match="格式无效"):
            prepare_settings_key(database, path)
    assert path.read_bytes() == b"invalid"


def test_decrypt_does_not_generate_missing_key(tmp_path: Path) -> None:
    path = tmp_path / "settings.key"
    token = Fernet(Fernet.generate_key()).encrypt(b"secret").decode()

    with pytest.raises(SecretDecryptionError):
        SecretCipher(path).decrypt(token)

    assert not path.exists()
