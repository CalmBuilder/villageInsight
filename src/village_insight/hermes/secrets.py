from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(RuntimeError):
    pass


class SecretCipher:
    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path

    def _load_or_create_key(self) -> bytes:
        try:
            return self._load_existing_key()
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
            if self.key_path.parent.stat().st_mode & stat.S_ISGID:
                self.key_path.chmod(0o440)
            return key

    def _load_existing_key(self) -> bytes:
        key = self.key_path.read_bytes().strip()
        Fernet(key)
        return key

    def encrypt(self, value: str) -> str:
        return Fernet(self._load_or_create_key()).encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return Fernet(self._load_existing_key()).decrypt(value.encode()).decode()
        except (FileNotFoundError, InvalidToken, ValueError) as exc:
            raise SecretDecryptionError(
                "stored API key cannot be decrypted with the current settings key"
            ) from exc
