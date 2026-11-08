"""Gift price data for the x402 API, reusing Felix's fetchers.

The fetch logic lives in ~/workspace/felix-bot/bot/gifts.py (multi-source,
attribute-aware, never guesses a price) and is REUSED here, not
rewritten: this module only resolves the collection/item, calls
``fetch_gift_data``, and shapes the result into the API's JSON.

Attribution: gift fetching, scoped floors (Portals Partners), buy
orders, recent sales and all upstream sources are Felix's code
(see ~/workspace/felix-bot/bot/gifts.py and its README). This file
adds only the API shaping and the resolve/fetch orchestration.
"""
import sys
from datetime import datetime, timezone
from typing import Any

from . import config
from .felix_env import load_felix_env

_gifts = None  # felix-bot's bot.gifts module, loaded lazily


class GiftNotFound(Exception):
    """No collection matches the requested name/slug."""

    def __init__(self, query: str, candidates: list[str] | None = None):
        self.query = query
        self.candidates = candidates or []
        super().__init__(f"no gift collection matches {query!r}")


def load_gift_module():
    """Import felix-bot's bot.gifts (loading its .env first).

    Idempotent. Raises ImportError when the Felix checkout is missing.
    """
    global _gifts
    if _gifts is not None:
        return _gifts
    load_felix_env()
    felix_dir = str(config.FELIX_DIR)
    if felix_dir not in sys.path:
        sys.path.insert(0, felix_dir)
    from bot import gifts  # noqa: PLC0415  (import after sys.path setup)

    _gifts = gifts
    return _gifts


def _floor_entry(entry: dict | None) -> dict | None:
    """One Felix {"price","venue","num"} floor -> {"ton","venue","num"}."""
    if not entry:
        return None
    try:
        return {
            "ton": float(entry["price"]),
            "venue": entry.get("venue"),
            "num": entry.get("num"),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _collection_floor_venue(data: dict) -> str | None:
    """Best-effort venue for the overall collection floor.

    The overall floor is the min of the Tonnel items, the Fragment
    floor and the floorprice.is floor; the venue is whichever source
    produced that minimum. None when it cannot be determined — never
    guessed.
    """
    floor = data.get("floor")
    if floor is None:
        return None
    if data.get("tonnel_floor") == floor:
        return "Tonnel"
    if data.get("fragment_floor") == floor:
        return "Fragment"
    for market in data.get("markets") or []:
        if isinstance(market, dict) and market.get("floor") == floor:
            return market.get("venue")
    return None


def shape_gift_response(data: dict, slug: str, item_id: int | None,
                        exact_list_price: float | None = None) -> dict:
    """Shape one fetch_gift_data() result into the API's JSON.

    Pure function (no I/O) so it is unit-testable with canned data.
    Missing sources stay null — never guessed.
    """
    floor = data.get("floor")
    return {
        "collection": data.get("name") or slug,
        "slug": slug,
        "item_id": item_id,
        "model": data.get("model"),
        "backdrop": data.get("backdrop"),
        "exact_list_price": (
            {"ton": exact_list_price, "venue": "Fragment"}
            if exact_list_price is not None else None
        ),
        "floors": {
            # Overall collection floor (min across sources).
            "collection": (
                {"ton": floor, "venue": _collection_floor_venue(data)}
                if floor is not None else None
            ),
            # Cheapest listing with the gift's own model.
            "collection_model": _floor_entry(data.get("model_floor")),
            # Cheapest listing with the gift's own backdrop.
            "collection_backdrop": _floor_entry(
                data.get("backdrop_floor")),
            # Cheapest listing with the gift's own model+backdrop.
            "model_backdrop": _floor_entry(data.get("combo_floor")),
        },
        "model_floors": [
            {"label": label, "ton": price, "count": count}
            for label, price, count in (data.get("model_floors") or [])
        ],
        "combo_floors": [
            {"label": label, "ton": price, "count": count}
            for label, price, count in (data.get("combo_floors") or [])
        ],
        "markets": data.get("markets"),
        "cheapest_num": data.get("cheapest_num"),
        "buy_orders": (
            {
                "count": (data["buy_orders"] or {}).get("count"),
                "count_exact": (data["buy_orders"] or {}).get(
                    "count_exact"),
                "top_ton": (data["buy_orders"] or {}).get("top"),
            }
            if data.get("buy_orders") else None
        ),
        "partners_sales": [
            {"num": s["num"], "price_ton": s["price"], "at": s.get("at")}
            for s in (data.get("partners_sales") or [])
        ] or None,
        "last_sale_model": data.get("last_sale_model"),
        "last_sale_combo": data.get("last_sale_combo"),
        "recent_sales": [
            {"num": s["num"], "price_ton": s["price"], "at": s.get("at")}
            for s in (data.get("recent_sales") or [])
        ] or None,
        "on_fragment": data.get("on_fragment"),
        "failed_sources": data.get("failed") or [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_gift_prices(http: Any, collection: str,
                          item_id: int) -> dict:
    """Fetch and shape the price data for one gift instance.

    ``http`` is a felix-bot HttpClient (UrllibHttpClient in prod, a
    fake in tests). ``collection`` is a slug or a name
    ("mythmask" / "Myth Mask"); ``item_id`` is the instance number
    (e.g. 85451). Raises GiftNotFound when the collection cannot be
    resolved.
    """
    gifts = load_gift_module()
    resolved = await gifts.resolve_gift(http, collection, fuzzy=True)
    if resolved["status"] in ("ok", "closest"):
        entry = resolved["entry"]
    elif resolved["status"] == "ask":
        raise GiftNotFound(
            collection,
            [e.get("name") or e.get("slug")
             for e in resolved.get("candidates", [])],
        )
    else:
        raise GiftNotFound(collection)
    slug = entry["slug"]

    # The instance's own attributes (model/backdrop) drive the scoped
    # floors. When they cannot be read, the collection-level floors
    # are still returned (model/backdrop stay null).
    model = backdrop = None
    exact_list_price = None
    attrs = await gifts.fetch_item_attrs(http, slug, item_id)
    if attrs:
        model = attrs.get("model")
        backdrop = attrs.get("backdrop")
        exact_list_price = attrs.get("list_price")

    data = await gifts.fetch_gift_data(
        http, entry, model=model, backdrop=backdrop, num=item_id)
    return shape_gift_response(data, slug, item_id, exact_list_price)
