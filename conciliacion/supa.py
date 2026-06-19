"""Cliente mínimo de Supabase (Auth + Storage) con stdlib — sin dependencias extra."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config


def _s():
    return config.supabase_settings()


def enabled() -> bool:
    return _s()["enabled"]


def verify_token(token: str) -> dict | None:
    """Valida el access_token del usuario contra la API de auth de Supabase."""
    s = _s()
    if not s["enabled"] or not token:
        return None
    req = urllib.request.Request(
        f"{s['url']}/auth/v1/user",
        headers={"apikey": s["public"], "Authorization": f"Bearer {token}"},
    )
    try:
        return json.load(urllib.request.urlopen(req, timeout=10))
    except Exception:
        return None


def upload(path: str, content: bytes, content_type: str = "application/octet-stream") -> str:
    """Sube (upsert) un archivo al bucket. Devuelve el path."""
    s = _s()
    req = urllib.request.Request(
        f"{s['url']}/storage/v1/object/{s['bucket']}/{path}",
        data=content, method="POST",
        headers={"apikey": s["secret"], "Authorization": f"Bearer {s['secret']}",
                 "Content-Type": content_type, "x-upsert": "true"},
    )
    urllib.request.urlopen(req, timeout=120)
    return path


def signed_url(path: str, expires: int = 3600) -> str | None:
    """URL firmada temporal para descargar un archivo privado."""
    s = _s()
    body = json.dumps({"expiresIn": expires}).encode()
    req = urllib.request.Request(
        f"{s['url']}/storage/v1/object/sign/{s['bucket']}/{path}",
        data=body, method="POST",
        headers={"apikey": s["secret"], "Authorization": f"Bearer {s['secret']}",
                 "Content-Type": "application/json"},
    )
    try:
        r = json.load(urllib.request.urlopen(req, timeout=15))
        return f"{s['url']}/storage/v1{r['signedURL']}"
    except Exception:
        return None
