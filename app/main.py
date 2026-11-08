"""Felix Gift Floors — x402 paid API for Telegram gift floor prices.

GET /                              -> this landing page (free)
GET /.well-known/x402             -> discovery manifest (free)
GET /gift/{collection}/{item_id}  -> price data, $0.05 USDC on Base

The gift endpoint is protected by the x402 paywall (see
app/x402_paywall.py): without a valid X-PAYMENT header it answers
402 with the payment requirements; with a verified payment it serves
the data and settles afterwards. Set X402_TEST_MODE=1 to bypass
payment locally for testing (NEVER in production).
"""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import config
from .gift_service import GiftNotFound, get_gift_prices, load_gift_module
from .manifest import build_manifest
from .x402_paywall import PRICE, build_paywall, gift_route_config

logger = logging.getLogger("x402-gift-api")

LANDING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Felix Gift Floors — Telegram gift floor prices, $0.05/call</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:3em auto;
padding:0 1em;line-height:1.6;color:#1a1a1a}code{background:#f0f0f0;
padding:.1em .35em;border-radius:4px}pre{background:#111;color:#eee;
padding:1em;overflow:auto;border-radius:8px}h1{font-size:1.6em}</style>
</head><body>
<h1>🎭 Felix Gift Floors</h1>
<p>Live Telegram NFT gift floor prices, paid per request with
<a href="https://www.x402.org/">x402</a> — no account, no API key, no
subscription. One call costs <b>$0.05 USDC on Base</b>.</p>
<h2>Endpoint</h2>
<p><code>GET /gift/{collection}/{item_id}</code></p>
<p><code>collection</code> is a slug or a name (<code>mythmask</code> or
<code>Myth Mask</code>); <code>item_id</code> is the gift instance
number (e.g. <code>85451</code>).</p>
<h2>What you get</h2>
<p>Four scoped floors (collection, collection+model,
collection+backdrop, model+backdrop), per-marketplace floors, top buy
orders, recent sales, and which sources failed — aggregated live from
Portals, Tonnel, Fragment, floorprice.is and Thermos. Prices are
never guessed: a source that fails is reported, not filled in.</p>
<h2>Pay with x402</h2>
<pre>curl https://YOUR-HOST/gift/mythmask/85451
# → 402 Payment Required (price, asset, network, payTo)
# Pay with any x402 client, e.g. the official SDK:
#   x402-fetch / x402-axios wrap the request and settle automatically.
# The response carries an X-PAYMENT-RESPONSE header with the tx hash.</pre>
<h2>Discovery</h2>
<p>Machine-readable manifest: <a href="/.well-known/x402">/.well-known/x402</a></p>
<p>Facilitator: PayAI (no account, no key). Payments settle in USDC on
Base to the seller's wallet. Built on
<a href="https://github.com/coinbase/x402">coinbase/x402</a> v2.</p>
</body></html>
"""


def _test_mode_accepts() -> list[dict]:
    """Static accepts for the manifest when the paywall is off
    (test mode). Same values as the live 402, from config."""
    return [{
        "scheme": "exact",
        "network": config.NETWORK,
        "amount": PRICE.amount,
        "asset": PRICE.asset,
        "payTo": config.PAY_TO,
        "maxTimeoutSeconds": config.MAX_TIMEOUT_SECONDS,
        "description": config.SERVICE_DESCRIPTION,
        "mimeType": "application/json",
    }]


def create_app(facilitator_client=None, http_client_factory=None):
    """Build the FastAPI app.

    ``facilitator_client`` / ``http_client_factory`` are injectable
    for tests; production uses the real PayAI facilitator and Felix's
    UrllibHttpClient. The paywall is built at startup (lifespan), so
    importing this module never touches the network.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if config.TEST_MODE:
            logger.warning(
                "X402_TEST_MODE=1 — payment is BYPASSED, data is free. "
                "Never deploy with this set.")
            app.state.paywall = None
        else:
            app.state.paywall = build_paywall(facilitator_client)
            logger.info("x402 paywall ready (facilitator %s)",
                        config.FACILITATOR_URL)
        # Pre-load the Felix fetchers (and their .env) at startup so
        # a missing checkout or key is visible immediately, not on
        # the first paid request. Failure is non-fatal: the gift
        # route answers 503 until the module loads.
        try:
            load_gift_module()
            logger.info("Felix gift fetchers loaded")
        except Exception as exc:  # noqa: BLE001 - startup must not crash
            logger.warning("Felix fetchers unavailable: %s", exc)
        yield

    app = FastAPI(title="Felix Gift Floors",
                  description="Telegram gift floor prices via x402",
                  version="1.0.0",
                  lifespan=lifespan)
    app.state.paywall = None
    app.state.facilitator_client = facilitator_client
    app.state.http_client_factory = http_client_factory

    @app.middleware("http")
    async def paywall_middleware(request: Request, call_next):
        paywall = request.app.state.paywall
        if config.TEST_MODE:
            return await call_next(request)
        if paywall is None:
            # Startup has not built the paywall yet: fail closed.
            return JSONResponse(
                status_code=503,
                content={"error": "paywall_not_ready"})
        result = await paywall.process_request(request)
        if result.type == "payment-error":
            return _instructions_response(result.response)
        response = await call_next(request)
        if result.type == "payment-verified":
            settle = await paywall.settle_after_handler(result, request)
            if settle.success:
                for key, value in (settle.headers or {}).items():
                    response.headers[key] = value
            elif settle.response is not None:
                # Settlement failed after serving: replace the data
                # with the settlement error (nothing was paid).
                return _instructions_response(settle.response)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def landing():
        return LANDING_HTML

    @app.get("/.well-known/x402")
    async def manifest(request: Request):
        base = str(request.base_url).rstrip("/")
        paywall = request.app.state.paywall
        resource = f"{base}/gift/{{collection}}/{{item_id}}"
        if paywall is None:
            if config.TEST_MODE:
                accepts = _test_mode_accepts()
            else:
                return JSONResponse(
                    status_code=503,
                    content={"error": "paywall_not_ready"})
        else:
            accepts = None
        if accepts is None:
            return build_manifest(paywall, base)
        return {
            "x402Version": 2,
            "resources": [{
                "resource": resource,
                "description": config.SERVICE_DESCRIPTION,
                "mimeType": "application/json",
                "serviceName": config.SERVICE_NAME,
                "tags": config.SERVICE_TAGS,
                "accepts": accepts,
            }],
        }

    @app.get("/gift/{collection}/{item_id}")
    async def gift(collection: str, item_id: int, request: Request):
        factory = request.app.state.http_client_factory
        if factory is not None:
            http = factory()
        else:
            try:
                from bot.http_client import UrllibHttpClient
            except ImportError:
                return JSONResponse(
                    status_code=503,
                    content={"error": "gift_fetchers_unavailable"})
            http = UrllibHttpClient()
        try:
            data = await get_gift_prices(http, collection, item_id)
        except GiftNotFound as exc:
            return JSONResponse(
                status_code=404,
                content={"error": "collection_not_found",
                         "query": exc.query,
                         "candidates": exc.candidates},
            )
        return JSONResponse(content=data)

    return app


def _instructions_response(instructions) -> Response:
    """Turn an SDK HTTPResponseInstructions into a Starlette Response."""
    status = instructions.status
    headers = dict(instructions.headers or {})
    body = instructions.body
    if isinstance(body, dict):
        return JSONResponse(status_code=status, content=body,
                            headers=headers)
    if isinstance(body, str):
        try:
            return JSONResponse(status_code=status,
                                content=json.loads(body), headers=headers)
        except ValueError:
            return Response(status_code=status, content=body,
                            headers=headers,
                            media_type="application/json")
    if body is None:
        return Response(status_code=status, headers=headers)
    return JSONResponse(status_code=status, content={"body": str(body)},
                        headers=headers)


app = create_app()


def main():
    """Run with uvicorn (see run.sh)."""
    import uvicorn

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT,
                log_level="info")


if __name__ == "__main__":
    main()
