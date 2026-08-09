from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

SIGNATURE_HEADER = "X-GTOChess-Signature"
TIMESTAMP_HEADER = "X-GTOChess-Timestamp"
EVENT_HEADER = "X-GTOChess-Event"


def sign(payload: bytes, timestamp: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


def deliver(
    url: str,
    event: str,
    body: dict[str, Any],
    *,
    secret: str | None = None,
    timeout_s: float = 10.0,
    client: httpx.Client | None = None,
) -> bool:
    payload = json.dumps({"event": event, "data": body}, sort_keys=True).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: event,
        TIMESTAMP_HEADER: timestamp,
    }
    if secret:
        headers[SIGNATURE_HEADER] = sign(payload, timestamp, secret)

    owned = client is None
    http = client or httpx.Client(timeout=timeout_s)
    try:
        response = http.post(url, content=payload, headers=headers)
    except httpx.HTTPError:
        # A webhook that cannot be delivered must not fail the job that earned it.
        return False
    finally:
        if owned:
            http.close()
    return response.is_success
