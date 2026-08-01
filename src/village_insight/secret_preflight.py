from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from village_insight.config import get_settings
from village_insight.db.models import LLMConfiguration, LLMProviderCredential
from village_insight.db.session import get_session_factory


class SecretPreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecretPreflightResult:
    created: bool
    reentry_required: bool
    fingerprint: str | None
    path: Path


def _has_encrypted_credentials(database: Session) -> bool:
    bind = database.get_bind()
    inspector = inspect(bind)
    for model in (LLMConfiguration, LLMProviderCredential):
        if not inspector.has_table(model.__tablename__):
            continue
        encrypted = database.scalar(select(model.encrypted_api_key).limit(1))
        if encrypted:
            return True
    return False


def _read_valid_key(path: Path) -> bytes:
    key = path.read_bytes().strip()
    try:
        Fernet(key)
    except ValueError as exc:
        raise SecretPreflightError(f"settings.key 格式无效：{path}") from exc
    return key


def _create_key(path: Path) -> bytes:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read_valid_key(path)
    with os.fdopen(descriptor, "wb") as writer:
        writer.write(key)
    return key


def _prepare_shared_directory(path: Path, owner_uid: int, group_gid: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(path.parent, owner_uid, group_gid)
    path.parent.chmod(0o2770)


def _set_shared_read_permissions(path: Path, owner_uid: int, group_gid: int) -> None:
    _prepare_shared_directory(path, owner_uid, group_gid)
    os.chown(path, owner_uid, group_gid)
    path.chmod(0o440)


def prepare_settings_key(
    database: Session,
    path: Path,
    *,
    owner_uid: int | None = None,
    group_gid: int | None = None,
) -> SecretPreflightResult:
    resolved_path = path.expanduser().resolve()
    if (owner_uid is None) != (group_gid is None):
        raise SecretPreflightError("owner_uid 和 group_gid 必须同时提供")
    if owner_uid is not None and group_gid is not None:
        if owner_uid < 0 or group_gid < 0:
            raise SecretPreflightError("owner_uid 和 group_gid 不能为负数")

    created = False
    try:
        key = _read_valid_key(resolved_path)
    except FileNotFoundError:
        if _has_encrypted_credentials(database):
            if owner_uid is not None and group_gid is not None:
                _prepare_shared_directory(resolved_path, owner_uid, group_gid)
            else:
                resolved_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            return SecretPreflightResult(
                created=False,
                reentry_required=True,
                fingerprint=None,
                path=resolved_path,
            )
        key = _create_key(resolved_path)
        created = True

    if owner_uid is not None and group_gid is not None:
        _set_shared_read_permissions(resolved_path, owner_uid, group_gid)

    return SecretPreflightResult(
        created=created,
        reentry_required=False,
        fingerprint=hashlib.sha256(key).hexdigest()[:12],
        path=resolved_path,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="安全准备或校验用于模型凭据加密的 settings.key。"
    )
    parser.add_argument("--owner-uid", type=int)
    parser.add_argument("--group-gid", type=int)
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = get_settings()
    try:
        with get_session_factory()() as database:
            result = prepare_settings_key(
                database,
                settings.resolved_secret_key_path(),
                owner_uid=args.owner_uid,
                group_gid=args.group_gid,
            )
    except (OSError, SecretPreflightError) as exc:
        raise SystemExit(f"settings.key 启动预检失败：{exc}") from exc
    if result.reentry_required:
        print(
            "settings.key 缺失且数据库已有加密凭据：应用将以模型功能降级模式启动，"
            "请恢复原密钥或在设置页重新录入 API Key。"
        )
    else:
        action = "已创建" if result.created else "已复用"
        print(f"settings.key {action}：path={result.path}, fingerprint={result.fingerprint}")


if __name__ == "__main__":
    main()
