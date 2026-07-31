from __future__ import annotations

import json
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from village_insight.db.schema import LLMModelDiscoveryResult
from village_insight.hermes.endpoints import validate_endpoint_url
from village_insight.hermes.runtime import HermesConnection


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        validate_endpoint_url(str(newurl), resolve=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _models_url(connection: HermesConnection) -> str:
    suffix = "/v1/models" if connection.api_mode == "anthropic_messages" else "/models"
    return f"{connection.base_url.rstrip('/')}{suffix}"


def discover_models(connection: HermesConnection) -> LLMModelDiscoveryResult:
    validate_endpoint_url(connection.base_url, resolve=True)
    headers = (
        {
            "x-api-key": connection.api_key,
            "anthropic-version": "2023-06-01",
        }
        if connection.api_mode == "anthropic_messages"
        else {"Authorization": f"Bearer {connection.api_key}"}
    )
    request = Request(_models_url(connection), headers=headers)
    started = perf_counter()
    try:
        with build_opener(SafeRedirectHandler()).open(request, timeout=20) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("API Key 无效或没有模型列表权限") from exc
        if exc.code == 404:
            raise ValueError("该连接不提供模型发现，请手工填写模型名称") from exc
        if exc.code == 429:
            raise ValueError("供应商正在限流，请稍后重试") from exc
        raise ValueError(f"模型发现失败，上游返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError("模型发现请求超时或无法连接") from exc
    if not isinstance(payload, dict):
        raise ValueError("模型列表响应格式不兼容")
    items = payload.get("data")
    if not isinstance(items, list):
        items = payload.get("models")
    if not isinstance(items, list):
        raise ValueError("模型列表响应中没有 data 或 models")
    models = sorted(
        {
            str(item.get("id") or item.get("name"))
            for item in items
            if isinstance(item, dict) and (item.get("id") or item.get("name"))
        }
    )
    return LLMModelDiscoveryResult(
        status="ok",
        models=models,
        latency_ms=round((perf_counter() - started) * 1000),
    )
