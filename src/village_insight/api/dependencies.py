from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from village_insight.config import get_settings
from village_insight.db.session import get_db
from village_insight.identity import Principal, resolve_principal

Database = Annotated[Session, Depends(get_db)]


def get_current_principal(request: Request, database: Database) -> Principal:
    raw_token = request.cookies.get(get_settings().session_cookie_name)
    principal = resolve_principal(database, raw_token) if raw_token else None
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_governor(principal: CurrentPrincipal) -> Principal:
    if not any(permission.startswith("governance.") for permission in principal.permissions):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="没有治理权限")
    return principal


GovernorPrincipal = Annotated[Principal, Depends(require_governor)]
