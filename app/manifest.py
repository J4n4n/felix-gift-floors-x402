"""The x402 discovery manifest served at /.well-known/x402.

Directories and crawlers (x402scan, PayAI's index, nohumans.directory,
x402-list.com) find paid resources by reading this manifest. The
``accepts`` entries are built with the SAME requirement builder the
402 response uses, so the manifest can never drift from the live
price / asset / payTo.

Format follows the x402 discovery draft: x402Version + a resources
list, each entry carrying the resource URL template, service metadata
and the accepts array.
"""
from . import config
from .x402_paywall import Paywall


def build_manifest(paywall: Paywall, base_url: str) -> dict:
    """Build the manifest for this service instance.

    ``base_url`` is the request's own base (so the manifest works
    unchanged behind a tunnel or a custom domain).
    """
    requirements = paywall.build_requirements()
    accepts = [
        req.model_dump(by_alias=True, exclude_none=True)
        for req in requirements
    ]
    return {
        "x402Version": 2,
        "resources": [
            {
                "resource": (
                    f"{base_url.rstrip('/')}"
                    "/gift/{collection}/{item_id}"
                ),
                "description": config.SERVICE_DESCRIPTION,
                "mimeType": "application/json",
                "serviceName": config.SERVICE_NAME,
                "tags": config.SERVICE_TAGS,
                "accepts": accepts,
            }
        ],
    }
