"""Tiny JSON-over-HTTPS helper with retries (stdlib only, so the action needs no pip install)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")
        self.status = status
        self.body = body


def request(method: str, url: str, headers: dict, body=None, *, raw: bool = False,
            retries: int = 4, timeout: int = 600):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = dict(headers)
    if data is not None:
        hdrs.setdefault("content-type", "application/json")
    delay = 2.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                if raw:
                    return text
                return json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (408, 429, 500, 502, 503, 504, 529)
            if retryable and attempt < retries:
                wait = e.headers.get("retry-after")
                time.sleep(float(wait) if wait and wait.replace(".", "", 1).isdigit() else delay)
                delay *= 2
                continue
            raise HttpError(e.code, text, url) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise HttpError(0, str(e), url) from None
