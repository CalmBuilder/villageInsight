from __future__ import annotations

from village_insight.config import get_settings
from village_insight.db.session import get_session_factory
from village_insight.identity import ensure_bootstrap_identity


def main() -> None:
    settings = get_settings()
    with get_session_factory()() as database:
        result = ensure_bootstrap_identity(database, settings)
    print(
        "初始化账号已就绪："
        f"users={','.join(result.usernames)}, newly_created={result.created_users}"
    )


if __name__ == "__main__":
    main()
