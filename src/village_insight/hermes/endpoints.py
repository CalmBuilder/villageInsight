from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

TRANSPARENT_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _reject_non_public_address(
    value: str,
    *,
    allow_transparent_proxy: bool = False,
) -> None:
    address = ipaddress.ip_address(value)
    if allow_transparent_proxy and address in TRANSPARENT_PROXY_NETWORK:
        return
    if not address.is_global:
        raise ValueError("Base URL 不能指向本机、私网或保留地址")


def validate_endpoint_url(value: str, *, resolve: bool) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme != "https":
        raise ValueError("Base URL 必须使用 HTTPS")
    if not parsed.hostname:
        raise ValueError("Base URL 缺少有效主机名")
    if parsed.username or parsed.password:
        raise ValueError("Base URL 不能包含用户名或密码")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Base URL 不能指向本机")
    try:
        _reject_non_public_address(hostname)
    except ValueError as exc:
        if "does not appear to be" not in str(exc):
            raise
    if resolve:
        try:
            addresses = {
                str(result[4][0])
                for result in socket.getaddrinfo(
                    hostname,
                    parsed.port or 443,
                    type=socket.SOCK_STREAM,
                )
            }
        except OSError as exc:
            raise ValueError("Base URL 域名无法解析") from exc
        if not addresses:
            raise ValueError("Base URL 域名没有可用地址")
        for address in addresses:
            _reject_non_public_address(address, allow_transparent_proxy=True)
    return normalized
