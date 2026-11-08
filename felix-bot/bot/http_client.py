"""Async HTTP client abstraction (stdlib only).

Same shape as the scanner bot's http_client.py, trimmed to what Felix
needs: JSON GET/POST plus plain-text GET (Pollinations answers in text,
Fragment answers in HTML). Transport failures raise HttpError; HTTP
error statuses are returned, not raised, so callers can degrade
gracefully (a failed price source must never crash the bot).
"""
import asyncio
import json
import urllib.error
import urllib.request


class HttpError(Exception):
    def __init__(self, status, body=""):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {str(body)[:200]}")


class HttpClient:
    async def get_json(self, url, headers=None, timeout=15):
        raise NotImplementedError

    async def get_text(self, url, headers=None, timeout=15):
        raise NotImplementedError

    async def post_json(self, url, payload, headers=None, timeout=15):
        raise NotImplementedError

    async def post_text(self, url, payload, headers=None, timeout=15):
        """POST a JSON payload, return (status, raw text). Needed for
        SSE endpoints (Gemini streaming), which are not JSON."""
        raise NotImplementedError


def _parse_body(raw):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"_raw": raw.decode("utf-8", "replace")}


def _do_request(method, url, data, headers, timeout):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # DNS, timeout, connection reset, ...
        raise HttpError(0, str(exc))


class UrllibHttpClient(HttpClient):
    """Stdlib client for local dev / the VM poller (blocking call in a thread)."""

    async def get_json(self, url, headers=None, timeout=15):
        status, raw = await asyncio.to_thread(
            _do_request, "GET", url, None, headers, timeout)
        if status >= 400:
            return status, _parse_body(raw)
        return status, _parse_body(raw)

    async def get_text(self, url, headers=None, timeout=15):
        status, raw = await asyncio.to_thread(
            _do_request, "GET", url, None, headers, timeout)
        return status, raw.decode("utf-8", "replace")

    async def post_json(self, url, payload, headers=None, timeout=15):
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        status, raw = await asyncio.to_thread(
            _do_request, "POST", url, json.dumps(payload).encode(), hdrs, timeout)
        return status, _parse_body(raw)

    async def post_text(self, url, payload, headers=None, timeout=15):
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        status, raw = await asyncio.to_thread(
            _do_request, "POST", url, json.dumps(payload).encode(), hdrs, timeout)
        return status, raw.decode("utf-8", "replace")
