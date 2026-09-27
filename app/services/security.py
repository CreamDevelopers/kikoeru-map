from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets

from starlette.requests import HTTPConnection

from app.config import get_settings

DEV_IP = "127.0.0.1"


def client_ip(conn: HTTPConnection) -> str:
    # app のポートは公開していないので、CF-Connecting-IP が無ければ開発環境とみなす
    raw = conn.headers.get("cf-connecting-ip", "").strip()
    if raw:
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            return raw[:64]
    return DEV_IP


def hash_ip(ip: str) -> str:
    salt = get_settings().ip_hash_salt.encode()
    return hmac.new(salt, ip.encode(), hashlib.sha256).hexdigest()


def ip_hash_of(conn: HTTPConnection) -> str:
    cached = getattr(conn.state, "ip_hash", None)
    if cached:
        return str(cached)
    h = hash_ip(client_ip(conn))
    conn.state.ip_hash = h
    return h


def new_sound_id() -> str:
    return secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def token_matches(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(sha256_hex(token), token_hash)
