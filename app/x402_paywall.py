"""x402 paywall for the gift API, built on the official coinbase/x402 SDK.

- Facilitator: PayAI (https://facilitator.payai.network) — no account,
  no API key. NEVER Coinbase CDP (KYT/OFAC; Iran is a prohibited
  region, so CDP signup would be blocked and settlements could be
  frozen).
- Scheme: exact, network eip155:8453 (Base), $0.05 USDC per call,
  payTo Janan's public EVM address.
- The SDK's x402HTTPResourceServer runs the 402 / verify / settle
  flow; this module adapts it to FastAPI/Starlette (HTTPAdapter +
  middleware helpers) and exposes the requirement builder so the
  /.well-known/x402 manifest serves exactly what the 402 returns.
"""
import logging
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from x402 import x402ResourceServer
from x402.http import (
    PAYMENT_SIGNATURE_HEADER,
    X_PAYMENT_HEADER,
    FacilitatorConfig,
    HTTPFacilitatorClient,
    HTTPProcessResult,
    HTTPRequestContext,
    PaymentOption,
    RouteConfig,
    x402HTTPResourceServer,
)
from x402.mechanisms.evm.exact import register_exact_evm_server
from x402.schemas import AssetAmount, ResourceConfig

from . import config

logger = logging.getLogger("x402-gift-api.paywall")

GIFT_ROUTE_PATTERN = "GET /gift/:collection/:item_id"
PRICE = AssetAmount(amount=config.PRICE_AMOUNT, asset=config.ASSET)


def gift_route_config() -> RouteConfig:
    """The one paid route: GET /gift/{collection}/{item_id}."""
    return RouteConfig(
        accepts=PaymentOption(
            scheme="exact",
            network=config.NETWORK,
            pay_to=config.PAY_TO,
            price=PRICE,
            max_timeout_seconds=config.MAX_TIMEOUT_SECONDS,
        ),
        description=config.SERVICE_DESCRIPTION,
        mime_type="application/json",
        service_name=config.SERVICE_NAME,
        tags=config.SERVICE_TAGS,
    )


def payment_header_value(request: Request) -> str | None:
    """The payment header for this request.

    x402 v2 uses PAYMENT-SIGNATURE; the legacy v1 name is X-PAYMENT.
    The SDK's server only reads PAYMENT-SIGNATURE, so accept both
    (v2 first) for compatibility with older clients.
    """
    return (request.headers.get(PAYMENT_SIGNATURE_HEADER)
            or request.headers.get(X_PAYMENT_HEADER))


class StarletteAdapter:
    """x402 HTTPAdapter over a Starlette Request."""

    def __init__(self, request: Request):
        self._request = request

    def get_header(self, name: str) -> str | None:
        value = self._request.headers.get(name)
        if value is None and name.lower() == \
                PAYMENT_SIGNATURE_HEADER.lower():
            # Compatibility: accept the legacy v1 header too.
            value = self._request.headers.get(X_PAYMENT_HEADER)
        return value

    def get_method(self) -> str:
        return self._request.method

    def get_path(self) -> str:
        return self._request.url.path

    def get_url(self) -> str:
        return str(self._request.url)

    def get_accept_header(self) -> str:
        return self._request.headers.get("accept", "")

    def get_user_agent(self) -> str:
        return self._request.headers.get("user-agent", "")

    def get_query_params(self) -> dict[str, str | list[str]] | None:
        return dict(self._request.query_params)

    def get_query_param(self, name: str) -> str | list[str] | None:
        return self._request.query_params.get(name)

    def get_body(self) -> Any:
        return None


@dataclass
class Paywall:
    """Wired x402 server + routes for the gift API."""

    http_server: x402HTTPResourceServer
    resource_server: x402ResourceServer

    def build_requirements(self, resource_url: str | None = None):
        """Payment requirements for the gift route, exactly as the
        402 response carries them (used by the manifest)."""
        cfg = ResourceConfig(
            scheme="exact",
            network=config.NETWORK,
            pay_to=config.PAY_TO,
            price=PRICE,
            max_timeout_seconds=config.MAX_TIMEOUT_SECONDS,
        )
        return self.resource_server.build_payment_requirements(cfg)

    async def process_request(self, request: Request) -> HTTPProcessResult:
        """Run the SDK's request processing (402 / verify / pass)."""
        context = HTTPRequestContext(
            adapter=StarletteAdapter(request),
            path=request.url.path,
            method=request.method,
            payment_header=payment_header_value(request),
            decoded_path=request.url.path,
        )
        return await self.http_server.process_http_request(context)

    async def settle_after_handler(self, result: HTTPProcessResult,
                                   request: Request):
        """Settle a verified payment after the handler served the data.

        Returns the SDK's ProcessSettleResult: on success its headers
        (X-PAYMENT-RESPONSE) are merged into the data response; on
        failure its response replaces the data response.
        """
        return await self.http_server.process_settlement(
            result.payment_payload,
            result.payment_requirements,
            context=HTTPRequestContext(
                adapter=StarletteAdapter(request),
                path=request.url.path,
                method=request.method,
                payment_header=payment_header_value(request),
                decoded_path=request.url.path,
            ),
            before_handler_settlement=result.before_handler_settlement,
            phase="after-handler",
        )


def _sanitize_no_proxy_for_httpx() -> None:
    """Drop bracketed IPv6 entries from NO_PROXY/no_proxy.

    This VM's NO_PROXY contains ``[::1]`` and ``[fd8b:...::1]``.
    httpx (used by the SDK's facilitator client) parses every
    NO_PROXY entry as a URL pattern and raises ``InvalidURL:
    Invalid port: ':1]'`` on the bracketed forms, which kills the
    service at startup. The entries are only needed by the proxy
    infrastructure itself; this service talks to localhost (kept)
    and to public HTTPS hosts (via the proxy). Values are only
    ever removed, never added.
    """
    import os

    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if not raw:
            continue
        kept = [e for e in raw.split(",")
                if e.strip() and not e.strip().startswith("[")]
        os.environ[key] = ",".join(kept)


def build_paywall(facilitator_client: Any | None = None) -> Paywall:
    """Build the paywall.

    ``facilitator_client`` is injectable for tests; by default a real
    HTTPFacilitatorClient pointed at PayAI is created and the server
    is initialized (a live GET /supported against PayAI).
    """
    _sanitize_no_proxy_for_httpx()
    if facilitator_client is None:
        facilitator_client = HTTPFacilitatorClient(
            FacilitatorConfig(url=config.FACILITATOR_URL))
    resource_server = x402ResourceServer(facilitator_client)
    register_exact_evm_server(resource_server, networks=[config.NETWORK])
    resource_server.initialize()
    http_server = x402HTTPResourceServer(
        resource_server,
        {GIFT_ROUTE_PATTERN: gift_route_config()},
    )
    return Paywall(http_server=http_server,
                   resource_server=resource_server)
