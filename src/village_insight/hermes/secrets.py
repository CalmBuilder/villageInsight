from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(RuntimeError):
    pass


class SecretCipher:
    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path

    def _load_or_create_key(self) -> bytes:
        try:
            return self.key_path.read_bytes().strip()
        except FileNotFoundError:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            try:
                descriptor = os.open(
                    self.key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                return self.key_path.read_bytes().strip()
            with os.fdopen(descriptor, "wb") as writer:
                writer.write(key)
            return key

    def encrypt(self, value: str) -> str:
        return Fernet(self._load_or_create_key()).encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return Fernet(self._load_or_create_key()).decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise SecretDecryptionError(
                "stored API key cannot be decrypted with the current settings key"
            ) from exc
