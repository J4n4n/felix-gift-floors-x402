"""Telegram NFT gift prices — multi-source, attribute-aware, fuzzy.

Sources verified live 2026-11-08 (see README for the per-source table):
- Tonnel pageGifts API (POST gifts2.tonnel.network/api/pageGifts, NO
  auth, 200): the primary source. Full attribute data (model/backdrop
  + rarity), gift_num lookup, price_asc sort. Works for every
  gift including ones Fragment does not list yet.
- Fragment public pages (HTML, no key): /gifts index (123 collections),
  /gifts/<slug> sale pages with ?attr[Model]/attr[Backdrop] filters,
  /gift/<slug>-<num> item pages (attributes + list price), and
  ?filter=sold pages (sold items with price + date — the last-sale
  source; NOTE: Fragment sorts sold pages by price, never by date,
  so "last sale" = the max sale date among the fetched items).
- floorprice.is (HTML, no key): 124-collection index (the ONLY index
  that has Myth Mask) + per-gift pages with per-marketplace floors
  (Tonnel / MRKT / Portals / Telegram market) + cheapest gift number,
  plus per-instance pages (floorprice.is/<slug>-<num>) with a
  "Listed on" table (the exact-instance price, with venue) and a
  "What similar gifts are listed for" table (per-model and
  per-model+backdrop floors).
  CAUTION: floorprice.is's per-instance "Not listed" does NOT cover
  the Telegram in-app market — verified 2026-11-08: MythMask-85511
  IS listed there, yet floorprice.is says "Not listed". Portals
  used to be in the same blind spot, but since 2026-11-08 the bot
  checks Portals directly via its own keyless read API (see below)
  — only the Telegram in-app market remains uncheckable from
  outside, and the bot's not-listed line must say so, never claim
  it was checked.
- Portals mini-app public read API (JSON, no key, no auth header —
  verified live 2026-11-08, see
  ~/workspace/research_notes/portals-api-investigation-20261108.md):
  GET {PORTALS_API}/collections (short_name -> collection UUID,
  floors) + GET {PORTALS_API}/nfts/search?collection_ids=<uuid>
  &external_collection_number=<num>&status=listed (exact-instance
  listing: price string in GRAM, status, tg_id, attributes).
  Quirk: the search response's total_count is always 0 — use
  len(results). Only gifts in Portals' custody appear. Buy orders
  and recent sales are 401 without auth on this host — those need
  the Partners API (see below); the keyless read API covers
  exact-instance listings only, never buy orders or sales.
- Portals Partners API (JSON, key in PORTALS_PARTNERS_KEY env —
  verified live 2026-11-08, free plan, 1 req/s): GET
  /partners/nfts/search with collection_id + filter_by_models /
  filter_by_backdrops (scoped floors: cheapest listing with a given
  model / backdrop / model+backdrop — the PREFERRED source for the
  item report's scoped floor lines) + GET
  /partners/market/actions with collection_id + the same filters
  (recent sales of one model+backdrop, newest first). Buy orders
  come from GET /partners/collection-offers/{id}/all — but that
  endpoint has NO trait filter and NO pagination (verified live:
  filter_by_models / offset are ignored; the page is always the
  top 100 trait offers by amount), so a combo whose offers sit
  below the page floor is invisible there and the buy-orders line
  is omitted rather than guessed.
- Thermos gift aggregator (swap.coffee) public proxy API (JSON, no
  key — verified live 2026-11-08): POST {THERMOS_API}/gifts
  (search by collection NAME / number / model / backdrop, prices in
  nanotons as strings; response {items, count}) + GET
  {THERMOS_API}/collections (126 collections, name + floor). Cross-
  market: GetGems, MRKT, Portals (marketplace is UPPERCASE in the
  response). TWO caveats, both verified live: (1) search returns
  CACHED data — the docs say to validate via the Get gift endpoint,
  and #47747 showed 18.9 on Thermos while Portals lists it at 18.0
  — so Thermos is a FALLBACK exact source (after floorprice.is and
  Portals), never the first. (2) the models/backdrops filters are
  EXACT (models=["Horn"] and backdrops=["Mint"] return 0 items) —
  the code still checks each returned item's attributes match the
  requested ones exactly, as cheap insurance.
- t.me/nft/<slug>-<num> pages (HTML, no key): exact attributes
  (Model/Backdrop) for one specific gift instance — the key
  to answering gift links. (Symbol is deliberately never used:
  the owner says it does not matter.)

Why Myth Mask was not found (2026-11-08): it is a NEW gift (released
~Nov 5, 2026; Tonnel shows can_transfer_since 2026-11-05) and
Fragment's index does not list it yet — it is the only one of
floorprice.is's 124 collections missing from Fragment's 123. The old
code only knew Fragment, so it returned "not found". Tonnel and
floorprice.is both have it (floor 11.2 TON).

BLOCKED sources (do NOT use — need login/key, verified 2026-11-08):
- Tonnel saleHistory API: "Invalid auth data" without authData.
- Portals Partners buy orders for LOW-value combos: the /all
  endpoint returns only the top 100 trait offers by amount with no
  trait filter and no pagination, so a combo whose offers sit below
  the page floor (Myth Mask: page floor 25 TON; Broken Time + Navy
  Blue offers ~11 TON) cannot be seen — the buy-orders line is
  omitted for those, never guessed. The market/actions sales above
  ARE wired; that is the only Partners sales source.
- MRKT (@mrkt): Telegram bot only, no public API or web market —
  its listings surface only via Thermos's aggregator (see above).
- Gift Satellite (@gift_satellite_app): Telegram mini-app only,
  no public API. (floorprice.is aggregates some of the same markets'
  floors, but its per-instance coverage is incomplete — see the
  floorprice.is CAUTION above.)
- Telegram's in-app marketplace: needs a Telegram login; no public
  endpoint. floorprice.is reports a "Telegram" row in its
  per-marketplace floors, but its per-instance "Not listed" does
  NOT cover this market (verified: MythMask-85511).
- GetGems GraphQL: 403 CloudFront from this VM (re-tested).

Rules: never guess a price. If a source fails, the report says which
one and gives what IS available. Last-sale data comes only from
Fragment's sold pages; Tonnel sale history needs a login, so when
Fragment has no recorded sale for a combination the bot says so.
"""
import asyncio
import difflib
import json
import re
import time
import urllib.parse

from . import config

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FelixBot/1.0)"}
_TONNEL_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://market.tonnel.network",
    "Referer": "https://market.tonnel.network/",
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
}

# Well-known gifts — fast path for exact names; anything else is matched
# against the live merged index (Fragment + floorprice.is).
GIFT_SLUGS = {
    "plush pepe": "plushpepe",
    "plushpepes": "plushpepe",
    "durov's cap": "durovscap",
    "durovs cap": "durovscap",
    "durov cap": "durovscap",
    "precious peach": "preciouspeach",
    "preciouspeaches": "preciouspeach",
    "myth mask": "mythmask",
    "myth masks": "mythmask",
}

_index_cache = {"ts": 0.0, "entries": []}  # [{slug, name, source}]
_floorprice_cache = {"ts": 0.0, "entries": []}  # [{slug, name, floor}]
_portals_collections_cache = {"ts": 0.0, "by_slug": {}}  # slug -> UUID
_thermos_collections_cache = {"ts": 0.0, "by_slug": {}}  # slug -> name


def clear_caches():
    _index_cache["ts"] = 0.0
    _index_cache["entries"] = []
    _floorprice_cache["ts"] = 0.0
    _floorprice_cache["entries"] = []
    _portals_collections_cache["ts"] = 0.0
    _portals_collections_cache["by_slug"] = {}
    _thermos_collections_cache["ts"] = 0.0
    _thermos_collections_cache["by_slug"] = {}


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _singular(normed):
    """plushpepes -> plushpepe; mythmasks -> mythmask."""
    if normed.endswith("s") and not normed.endswith("ss"):
        return normed[:-1]
    return normed


def _title(text):
    """plush pepes -> Plush Pepe (Tonnel wants title-cased names)."""
    return " ".join(w[:1].upper() + w[1:] for w in (text or "").split())


def _strip_rarity(value):
    """'Red Oni (1.9%)' -> 'Red Oni'."""
    return re.sub(r"\s*\([\d.]+%\)\s*$", "", value or "").strip()


# --- Index: Fragment + floorprice.is (merged) ------------------------------
async def _fetch_fragment_index(http):
    """Gift collections from Fragment's /gifts page."""
    try:
        status, html = await http.get_text(
            f"{config.FRAGMENT_BASE}/gifts", headers=_HEADERS,
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return []
    if status != 200 or not html:
        return []
    entries = []
    for m in re.finditer(
            r'href="/gifts/([a-z0-9]+)"[^>]*data-keywords="([^"]*)"'
            r'[^>]*data-value="([^"]*)".*?'
            r'tm-main-filters-name">([^<]+)</div>',
            html, re.S):
        slug, keywords, _value, name = m.groups()
        entries.append({"slug": slug, "name": name.strip(),
                        "keywords": keywords, "source": "fragment"})
    return entries


async def _fetch_floorprice_index(http):
    """Gift collections from floorprice.is (has gifts Fragment lacks,
    e.g. Myth Mask). Cards carry data-n (name) + data-f (floor TON)."""
    now = time.time()
    if (_floorprice_cache["entries"]
            and now - _floorprice_cache["ts"]
            < config.GIFT_INDEX_CACHE_TTL_S):
        return _floorprice_cache["entries"]
    try:
        status, html = await http.get_text(
            f"{config.FLOORPRICE_BASE}/", headers=_HEADERS,
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return _floorprice_cache["entries"]
    if status != 200 or not html:
        return _floorprice_cache["entries"]
    entries = []
    for m in re.finditer(
            r'<a class="card" href="/([a-z0-9]+)" data-n="([^"]+)"'
            r' data-f="([\d.]+)"',
            html):
        slug, name, floor = m.groups()
        try:
            floor_f = float(floor)
        except ValueError:
            floor_f = None
        entries.append({"slug": slug, "name": name.strip(),
                        "floor": floor_f, "source": "floorprice"})
    if entries:
        _floorprice_cache["ts"] = now
        _floorprice_cache["entries"] = entries
    return _floorprice_cache["entries"]


async def fetch_index(http):
    """Merged gift index (Fragment first, floorprice.is fills the gaps).
    Cached 10 min. Each entry: {slug, name, keywords?, source}."""
    now = time.time()
    if (_index_cache["entries"]
            and now - _index_cache["ts"] < config.GIFT_INDEX_CACHE_TTL_S):
        return _index_cache["entries"]
    frag, fp = await asyncio.gather(
        _fetch_fragment_index(http), _fetch_floorprice_index(http))
    seen = set()
    entries = []
    for entry in frag + fp:
        if entry["slug"] in seen:
            continue
        seen.add(entry["slug"])
        entries.append(entry)
    if entries:
        _index_cache["ts"] = now
        _index_cache["entries"] = entries
    return _index_cache["entries"]


# --- Name resolution (exact / closest / ask) -------------------------------
def _score(query_norm, entry):
    """Similarity of a normalized query to one index entry (0..1)."""
    best = 0.0
    for field in (entry.get("name"), entry.get("keywords"),
                  entry.get("slug")):
        if not field:
            continue
        target = _singular(_norm(field))
        ratio = difflib.SequenceMatcher(None, query_norm, target).ratio()
        if query_norm and query_norm in target:
            ratio = max(ratio, 0.9)  # substring match counts high
        best = max(best, ratio)
    return best


async def resolve_gift(http, query, fuzzy=True):
    """Resolve a user's gift name.

    Returns one of:
    - {"status": "ok", "entry": {...}} — exact match.
    - {"status": "closest", "entry": {...}} — no exact match, but one
      gift is clearly the closest (e.g. "myth masks" -> "myth mask").
    - {"status": "ask", "candidates": [...]} — several gifts match
      similarly; the caller must ask the user which one.
    - {"status": "none"} — nothing similar at all.
    With fuzzy=False only exact matches count (used for bare gift
    names in free-form chat, so ordinary words never trigger a price).
    """
    q = _singular(_norm(query))
    if not q:
        return {"status": "none"}
    for name, slug in GIFT_SLUGS.items():
        if _singular(_norm(name)) == q:
            return {"status": "ok",
                    "entry": {"slug": slug, "name": name,
                              "source": "known"}}
    entries = await fetch_index(http)
    for entry in entries:
        for field in (entry.get("name"), entry.get("keywords"),
                      entry.get("slug")):
            if field and _singular(_norm(field)) == q:
                return {"status": "ok", "entry": entry}
    if not fuzzy:
        return {"status": "none"}
    scored = sorted(
        ((e, _score(q, e)) for e in entries),
        key=lambda pair: pair[1], reverse=True)
    good = [(e, s) for e, s in scored if s >= 0.6]
    if not good:
        return {"status": "none"}
    if len(good) == 1 or good[0][1] - good[1][1] >= 0.2:
        return {"status": "closest", "entry": good[0][0]}
    return {"status": "ask",
            "candidates": [e for e, _s in good[:5]]}


async def find_gift_slug(http, query):
    """Back-compat wrapper: slug for an exact/closest match, else None."""
    result = await resolve_gift(http, query)
    if result["status"] in ("ok", "closest"):
        return result["entry"]["slug"]
    return None


async def gift_display_name(http, slug):
    """Pretty name for a slug (from the index), falling back to the slug."""
    for entry in await fetch_index(http):
        if entry["slug"] == slug:
            return entry["name"]
    return slug


def tonnel_name(entry):
    """Index name -> Tonnel's gift_name ('Plush Pepes' -> 'Plush Pepe',
    "Durov's Caps" -> "Durov's Cap")."""
    display = (entry.get("name") or entry.get("slug") or "").strip()
    if not display:
        return ""
    words = display.split()
    if words and len(words[-1]) > 1 and words[-1].lower().endswith("s"):
        words[-1] = words[-1][:-1]
    return _title(" ".join(words))


# --- Gift links ------------------------------------------------------------
_LINK_PATTERNS = [
    # t.me/nft/<slug>-<num> or t.me/nft/<slug>
    (re.compile(r"(?:t\.me|telegram\.me)/nft/([a-z0-9]+)(?:-(\d+))?",
                re.I), "item"),
    # fragment.com/gift/<slug>-<num>
    (re.compile(r"fragment\.com/gift/([a-z0-9]+)-(\d+)", re.I), "item"),
    # fragment.com/gifts/<slug>
    (re.compile(r"fragment\.com/gifts/([a-z0-9]+)", re.I), "collection"),
    # getgems.io/nft/<address> — address-based, cannot resolve keylessly
    (re.compile(r"getgems\.io/nft/([A-Za-z0-9_\-]+)", re.I), "unsupported"),
]


def parse_gift_link(text):
    """Parse a gift link out of a message. Returns None when the text
    holds no gift link, else {"kind", "slug", "num", "raw"}:
    kind "item" (specific gift, has num), "collection" (a gift type),
    or "unsupported" (a marketplace link we cannot resolve keylessly)."""
    if not text:
        return None
    for pattern, kind in _LINK_PATTERNS:
        m = pattern.search(text)
        if m:
            slug = m.group(1).lower()
            num = None
            if kind == "item" and len(m.groups()) > 1 and m.group(2):
                try:
                    num = int(m.group(2))
                except ValueError:
                    num = None
            return {"kind": kind, "slug": slug, "num": num,
                    "raw": m.group(0)}
    if re.search(r"(mrkt|portals|tonnel)[^\s]*", text or "", re.I) \
            and re.search(r"https?://\S+", text or ""):
        return {"kind": "unsupported", "slug": "", "num": None, "raw": text}
    return None


async def fetch_item_attrs(http, slug, num):
    """Exact attributes of one gift instance (model/backdrop)
    + its Fragment list price when it is for sale.

    Tries the t.me/nft page first (og:description carries the
    attributes), then the Fragment item page's attribute table and
    bid-info block. Returns {name, num, model, backdrop,
    list_price} or None. Symbol is never read: the owner says it
    does not matter."""
    if num is None:
        return None
    attrs = {}
    name = slug
    # 1. t.me/nft/<slug>-<num>
    try:
        status, html = await http.get_text(
            f"https://t.me/nft/{slug}-{num}", headers=_HEADERS,
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        status, html = 0, ""
    if status == 200 and html:
        title_m = re.search(
            r'<meta property="og:title" content="([^"]+)"', html)
        desc_m = re.search(
            r'<meta property="og:description" content="([^"]*)"', html)
        if desc_m:
            for line in desc_m.group(1).splitlines():
                key, _, value = line.partition(":")
                key = key.strip().lower()
                if key in ("model", "backdrop"):
                    attrs[key] = value.strip()
        if title_m:
            name = title_m.group(1).rsplit(" #", 1)[0]
    # 2. Fragment /gift/<slug>-<num> attribute table + list price
    list_price = None
    try:
        status, html = await http.get_text(
            f"{config.FRAGMENT_BASE}/gift/{slug}-{num}", headers=_HEADERS,
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        status, html = 0, ""
    if status == 200 and html:
        for key in ("Model", "Backdrop"):
            if key.lower() not in attrs:
                m = re.search(
                    r'table-cell">' + key + r"</div>.*?"
                    r'class="table-cell-value-link">([^<]+)</a>',
                    html, re.S)
                if m:
                    attrs[key.lower()] = m.group(1).strip()
        price_m = re.search(
            r'tm-section-bid-info.*?tm-value icon-before icon-ton">'
            r"([\d,\.]+)</div>",
            html, re.S)
        if price_m:
            try:
                list_price = float(price_m.group(1).replace(",", ""))
            except ValueError:
                list_price = None
        name_m = re.search(r'<span class="item-name">([^<]+)</span>', html)
        if name_m:
            name = name_m.group(1).strip()
    if not attrs and list_price is None:
        return None
    return {"name": name, "num": num, "model": attrs.get("model"),
            "backdrop": attrs.get("backdrop"),
            "list_price": list_price}


# --- Tonnel (primary source, no auth) --------------------------------------
def _tonnel_filter(gift_name=None, model=None, backdrop=None,
                   gift_num=None):
    filt = {"price": {"$exists": True}, "buyer": {"$exists": False},
            "asset": "TON", "refunded": {"$ne": True},
            "export_at": {"$exists": True}}
    if gift_name:
        filt["gift_name"] = gift_name
    if model:
        filt["model"] = {"$regex": f"^{_title(model)} \\("}
    if backdrop:
        filt["backdrop"] = {"$regex": f"^{_title(backdrop)} \\("}
    if gift_num is not None:
        filt["gift_num"] = str(gift_num)
    return filt


async def tonnel_search(http, gift_name=None, model=None, backdrop=None,
                        gift_num=None, limit=30,
                        sort="price_asc"):
    """Search Tonnel's public gift listings (no auth needed).

    Returns a list of {num, name, model, backdrop, price}
    sorted by price ascending, or None when the API is unreachable /
    blocked (callers degrade to the other sources). Up to 3 attempts:
    Cloudflare sometimes challenges this VM's shared IP (403) and the
    API rate-limits (429) — the next attempt usually passes (verified
    live 2026-11-08: 1st attempt failed, 2nd/3rd returned 200).
    Symbol is never filtered on: the owner says it does not matter."""
    sorts = {
        "price_asc": '{"price":1,"gift_id":-1}',
        "price_desc": '{"price":-1,"gift_id":-1}',
        "latest": '{"message_post_time":-1,"gift_id":-1}',
    }
    payload = {
        "filter": json.dumps(_tonnel_filter(
            gift_name, model, backdrop, gift_num)),
        "limit": limit, "page": 1,
        "sort": sorts.get(sort, sorts["price_asc"]),
        "ref": 0, "user_auth": "", "price_range": 0,
    }
    for _attempt in range(3):
        try:
            status, data = await http.post_json(
                config.TONNEL_API, payload, headers=_TONNEL_HEADERS,
                timeout=config.PRICE_TIMEOUT_S)
        except Exception:  # transport error — retry
            await asyncio.sleep(1)
            continue
        if status != 200 or not isinstance(data, list):
            # Cloudflare challenge (403) / rate limit (429) / 5xx:
            # retry, it usually passes on the next attempt.
            await asyncio.sleep(1)
            continue
        out = []
        for g in data:
            try:
                out.append({
                    "num": int(g.get("gift_num")),
                    "name": g.get("name") or gift_name or "",
                    "model": _strip_rarity(g.get("model")),
                    "backdrop": _strip_rarity(g.get("backdrop")),
                    "price": float(g.get("price")),
                })
            except (TypeError, ValueError, KeyError):
                continue
        return out
    return None


def group_floors(items):
    """Cheapest listings grouped by model and by model+backdrop.

    Returns (model_floors, combo_floors), each a list of
    (label, min_price, count) sorted by price ascending."""
    by_model, by_combo = {}, {}
    for item in items or []:
        price = item["price"]
        model = item.get("model") or "—"
        combo = (model, item.get("backdrop") or "—")
        for key, store in ((model, by_model), (combo, by_combo)):
            cur = store.get(key)
            if cur is None or price < cur[0]:
                store[key] = (price, (cur[1] if cur else 0) + 1)
            else:
                store[key] = (cur[0], cur[1] + 1)
    model_floors = [(m, p, n) for m, (p, n) in
                    sorted(by_model.items(), key=lambda kv: kv[1][0])]
    combo_floors = [(f"{m} + {b}", p, n) for (m, b), (p, n) in
                    sorted(by_combo.items(), key=lambda kv: kv[1][0])]
    return model_floors, combo_floors


# --- Fragment (secondary source) -------------------------------------------
def _fragment_listings_url(slug, model=None, backdrop=None, sold=False):
    params = {"sort": "price", "filter": "sold" if sold else "sale"}
    if model:
        params["attr[Model]"] = json.dumps([model])
    if backdrop:
        params["attr[Backdrop]"] = json.dumps([backdrop])
    query = urllib.parse.urlencode(params)
    return f"{config.FRAGMENT_BASE}/gifts/{slug}?{query}"


def _parse_fragment_items(html):
    """Parse one Fragment gift grid page into listings.

    Each <a class="tm-grid-item"> block holds: number (#203), price
    (icon-ton value), status (avail/sold) and, on sold pages, a
    <time datetime>. Returns [{num, price, date|None, for_sale}]."""
    items = []
    for m in re.finditer(
            r'<a href="/gift/[a-z0-9]+-\d+[^>]*>.*?</a>', html, re.S):
        block = m.group(0)
        num_m = re.search(r"-(\d+)", block)
        price_m = re.search(
            r'tm-grid-item-value[^>]*>([\d,\.]+)</div>', block)
        date_m = re.search(r'<time datetime="([^"]+)"', block)
        if not num_m or not price_m:
            continue
        try:
            price = float(price_m.group(1).replace(",", ""))
        except ValueError:
            continue
        items.append({
            "num": int(num_m.group(1)),
            "price": price,
            "date": date_m.group(1) if date_m else None,
            "for_sale": "tm-status-avail" in block,
        })
    return items


async def get_floor_ton(http, slug, model=None, backdrop=None):
    """Cheapest currently-for-sale listing (TON) for a gift slug,
    optionally filtered to one model / model+backdrop.
    Returns float TON, or None when the page fails / nothing listed."""
    url = _fragment_listings_url(slug, model, backdrop, sold=False)
    try:
        status, html = await http.get_text(
            url, headers=_HEADERS, timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not html:
        return None
    for item in _parse_fragment_items(html):
        if item["for_sale"]:
            return item["price"]
    return None


async def get_sold_items(http, slug, model=None, backdrop=None):
    """Sold listings for a gift (first page), optionally filtered to
    one model / model+backdrop. Returns [{num, price, date}] or []."""
    url = _fragment_listings_url(slug, model, backdrop, sold=True)
    try:
        status, html = await http.get_text(
            url, headers=_HEADERS, timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return []
    if status != 200 or not html:
        return []
    return [{"num": i["num"], "price": i["price"], "date": i["date"]}
            for i in _parse_fragment_items(html) if i["date"]]


def last_sale(sold_items):
    """Most recent sale from a sold-items list, or None.

    Fragment never sorts sold pages by date, so this is the max sale
    date among the fetched items — best effort, honestly labeled."""
    dated = [i for i in (sold_items or []) if i.get("date")]
    if not dated:
        return None
    return max(dated, key=lambda i: i["date"])


def last_sales(sold_items, n=3):
    """The n most recent sales, newest first, or [].

    Same best-effort date ordering as last_sale."""
    dated = [i for i in (sold_items or []) if i.get("date")]
    dated.sort(key=lambda i: i["date"], reverse=True)
    return dated[:n]


# --- floorprice.is (index gap-filler + multi-venue floors) ------------------
async def get_floorprice_markets(http, slug):
    """Per-marketplace floors for one gift from floorprice.is.

    Returns {floor, markets: [{venue, floor, usd}], cheapest_num,
    change24} or None when the page fails / has no table."""
    try:
        status, html = await http.get_text(
            f"{config.FLOORPRICE_BASE}/{urllib.parse.quote(slug)}",
            headers=_HEADERS, timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not html:
        return None
    markets = []
    for m in re.finditer(
            r"<tr><td>([^<]+)</td><td[^>]*><span[^>]*>([\d,\.]+)</span>"
            r"</td><td[^>]*>([^<]*)</td>",
            html):
        venue, floor, usd = m.groups()
        try:
            markets.append({"venue": venue.strip(),
                            "floor": float(floor.replace(",", "")),
                            "usd": usd.strip()})
        except ValueError:
            continue
    if not markets:
        return None
    cheapest_m = re.search(r"Cheapest gift:\s*([A-Za-z0-9]+-\d+)", html)
    change_m = re.search(r"▲\s*([\d.]+)%", html)
    return {
        "floor": markets[0]["floor"] if markets else None,
        "markets": markets,
        "cheapest_num": cheapest_m.group(1) if cheapest_m else None,
        "change24": change_m.group(1) if change_m else None,
    }


# --- Full gift report -------------------------------------------------------
async def get_floorprice_exact(http, slug, num):
    """Exact-instance listing price from floorprice.is.

    GET {FLOORPRICE_BASE}/{slug}-{num}; returns {"price", "venue"}
    from the first row of the page's "Listed on" table, or None when
    the instance is not listed there. The table is parsed
    section-scoped: only a row inside the "Listed on" table counts,
    so the page's "Estimated value" box (explicitly "Not a listing
    or an offer") can never leak in as a price.
    CAUTION: "Not listed" here covers only floorprice.is's own
    tracked listings — it does NOT cover Portals or the Telegram
    in-app market (verified 2026-11-08: MythMask-85511 is listed on
    both, yet floorprice.is says "Not listed"); both venues are
    mini-app/in-app only and cannot be checked from outside. The
    caller must say so in the report."""
    url = f"{config.FLOORPRICE_BASE}/{slug}-{num}"
    try:
        status, html = await http.get_text(
            url, headers=_HEADERS, timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not html or "Not listed" in html:
        return None
    return _parse_fp_listed(html)


def _parse_fp_listed(html):
    """First row of the floorprice.is "Listed on" table, or None."""
    head = html.find("<th>Listed on</th>")
    if head < 0:
        return None
    m = re.search(
        r'<tr><td>([^<]+)</td><td class="r"><span class="ton">'
        r'([\d,]+(?:\.\d+)?)</span>',
        html[head:])
    if not m:
        return None
    try:
        return {"price": float(m.group(2).replace(",", "")),
                "venue": m.group(1).strip()}
    except ValueError:
        return None


def parse_fp_similar(html):
    """Model/combo floors from the floorprice.is "What similar gifts
    are listed for" table: [(label, price)] with labels like
    "Horny floor", "Mexican Pink floor", "Horny + Mexican Pink".
    Section-scoped to that table, so the "Listed on" table (and the
    estimate box) can never leak in. [] when the table is absent —
    listed pages have no similar table."""
    head = html.find("<th>Cheapest with the same</th>")
    if head < 0:
        return []
    end = html.find("</table>", head)
    section = html[head:end if end > 0 else len(html)]
    out = []
    for m in re.finditer(
            r'<tr><td>([^<]+)</td><td class="r"><span class="ton">'
            r'([\d,]+(?:\.\d+)?)</span>', section):
        try:
            out.append((m.group(1).strip(),
                        float(m.group(2).replace(",", ""))))
        except ValueError:
            continue
    return out


def parse_fp_similar_detail(html):
    """Model/combo floors from the floorprice.is "What similar gifts
    are listed for" table, with the venue + instance number of each
    row's cheapest listing: [{label, price, venue, num}]. Labels are
    the same as parse_fp_similar's ("Horny floor", "Horny + Mexican
    Pink"); venue/num come from the table's "Where" cell
    ("Portals #47747"). Section-scoped to that table, so the
    "Listed on" table (and the estimate box) can never leak in.
    [] when the table is absent — listed pages have no similar table.
    A row with no Where cell still yields its price (venue/num None)
    rather than being dropped."""
    head = html.find("<th>Cheapest with the same</th>")
    if head < 0:
        return []
    end = html.find("</table>", head)
    section = html[head:end if end > 0 else len(html)]
    out = []
    for m in re.finditer(
            r'<tr><td>([^<]+)</td><td class="r"><span class="ton">'
            r'([\d,]+(?:\.\d+)?)</span></td>'
            r'(?:<td[^>]*>[^<]*</td>)?<td>(.*?)</td>', section):
        label, price_s, where = m.groups()
        try:
            price = float(price_s.replace(",", ""))
        except ValueError:
            continue
        venue = None
        num = None
        venue_m = re.search(r"^\s*([^<\s][^<]*?)\s*(?:<a|$)", where)
        if venue_m:
            venue = venue_m.group(1).strip() or None
        num_m = re.search(r"#(\d+)", where)
        if num_m:
            try:
                num = int(num_m.group(1))
            except ValueError:
                num = None
        out.append({"label": label.strip(), "price": price,
                    "venue": venue, "num": num})
    return out


# --- Portals (keyless mini-app read API) ---------------------------------
# Collection UUIDs seeded from GET /api/collections (verified live
# 2026-11-08). Unknown slugs fall back to a live collections fetch.
PORTALS_COLLECTIONS = {
    "mythmask": "6d216503-7294-4204-aece-ac11534640a4",
    "plushpepe": "8d21b568-63bb-437a-9093-8684c5b63485",
}


async def _portals_collection_map(http):
    """slug -> collection UUID, from GET /api/collections (cached
    10 min). Keys are normalized the same way as index slugs."""
    now = time.time()
    if (_portals_collections_cache["by_slug"]
            and now - _portals_collections_cache["ts"]
            < config.GIFT_INDEX_CACHE_TTL_S):
        return _portals_collections_cache["by_slug"]
    try:
        status, data = await http.get_json(
            f"{config.PORTALS_API}/collections?limit=200",
            headers=_HEADERS, timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return _portals_collections_cache["by_slug"]
    if status != 200 or not isinstance(data, dict):
        return _portals_collections_cache["by_slug"]
    by_slug = {}
    for coll in data.get("collections", []) or []:
        short = coll.get("short_name")
        cid = coll.get("id")
        if short and cid:
            by_slug[_norm(short)] = cid
    if by_slug:
        _portals_collections_cache["ts"] = now
        _portals_collections_cache["by_slug"] = by_slug
    return _portals_collections_cache["by_slug"]


async def get_portals_exact(http, slug, num):
    """Exact-instance listing price from Portals' keyless read API.

    Resolves the collection UUID (seeded map, else a live
    /api/collections fetch), then GETs /api/nfts/search with
    collection_ids + external_collection_number + status=listed.
    Returns {"price", "venue": "Portals"} from the first matching
    result, or None when the collection is unknown, the request
    fails, or the instance is not listed there. The search
    response's total_count is always 0 even with results — it is
    ignored; only len(results) counts. Price is a GRAM string in
    the JSON. No auth header is sent (none is needed for these
    read endpoints); buy orders / sales are NOT available here
    (401) and are never claimed."""
    cid = PORTALS_COLLECTIONS.get(_norm(slug))
    if not cid:
        cid = (await _portals_collection_map(http)).get(_norm(slug))
    if not cid:
        return None
    url = (f"{config.PORTALS_API}/nfts/search?offset=0&limit=5"
           f"&sort_by=price+asc&collection_ids={cid}"
           f"&external_collection_number={num}&status=listed")
    try:
        status, data = await http.get_json(
            url, headers=_HEADERS, timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    for result in data.get("results", []) or []:
        try:
            if (int(result.get("external_collection_number")) == num
                    and result.get("status") == "listed"):
                return {"price": float(result["price"]),
                        "venue": "Portals"}
        except (TypeError, ValueError, KeyError):
            continue
    return None


# --- Portals Partners API (scoped floors) ------------------------------
# Free plan, self-issued key (verified live 2026-11-08). The key is
# read from config (env PORTALS_PARTNERS_KEY) — never hard-coded,
# never logged. Free plan: 1 request/second, enforced by
# _partners_throttle. Python urllib's default User-Agent gets
# Cloudflare 403 error 1010 on this host (verified live), so every
# Partners call carries a browser UA.
_PARTNERS_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_partners_last_call = 0.0
# Tests may set this to 0 to skip the wait; production uses the
# free-plan limit from config.
_partners_min_interval_s = config.PARTNERS_MIN_INTERVAL_S


def _partners_headers():
    return {"Authorization": f"partners {config.PORTALS_PARTNERS_KEY}",
            "User-Agent": _PARTNERS_BROWSER_UA}


async def _partners_throttle():
    """Wait until the free plan's 1 req/s allows another call."""
    global _partners_last_call
    now = time.monotonic()
    wait = _partners_min_interval_s - (now - _partners_last_call)
    if wait > 0:
        await asyncio.sleep(wait)
    _partners_last_call = time.monotonic()


def _partners_attrs_match(result, model, backdrop):
    """Do the returned item's attributes match the requested ones?

    The Partners search filters server-side, but a mismatch must
    never be reported as a floor. Attributes are
    [{type: "model"|"backdrop"|"symbol", value: "..."}]; when they
    are absent or unparseable the server-side filter is trusted.
    Symbol is never checked: the owner says it does not matter."""
    attrs = result.get("attributes")
    if not isinstance(attrs, list) or not attrs:
        return True
    wanted = {}
    if model:
        wanted["model"] = _norm(model)
    if backdrop:
        wanted["backdrop"] = _norm(backdrop)
    if not wanted:
        return True
    have = {}
    for attr in attrs:
        if isinstance(attr, dict):
            have[_norm(attr.get("type"))] = _norm(attr.get("value"))
    return all(have.get(kind) == value
               for kind, value in wanted.items())


async def get_portals_scoped_floor(http, slug, model=None, backdrop=None):
    """Cheapest listed Portals gift with the given model and/or
    backdrop, via the Partners API (OpenAPI:
    https://docs.portals.tg/api/partners.en.json, GET
    /partners/nfts/search).

    GET {PARTNERS}/nfts/search?collection_id=<uuid>
    &filter_by_models=<model>&filter_by_backdrops=<backdrop>
    &status=listed&sort_by=price asc&limit=1. Returns
    {"price", "venue": "Portals", "num"} (price is a GRAM string
    in the JSON) or None when the key is missing, the collection
    is unknown, the request fails (401/403/5xx — fail-open), or
    nothing matches. The returned item's attributes are checked
    against the requested ones. Throttled to the free plan's
    1 req/s."""
    if not config.PORTALS_PARTNERS_KEY:
        return None
    cid = PORTALS_COLLECTIONS.get(_norm(slug))
    if not cid:
        cid = (await _portals_collection_map(http)).get(_norm(slug))
    if not cid:
        return None
    params = {"collection_id": cid, "status": "listed",
              "sort_by": "price asc", "limit": 1}
    if model:
        params["filter_by_models"] = model
    if backdrop:
        params["filter_by_backdrops"] = backdrop
    url = (f"{config.PORTALS_PARTNERS_BASE}/nfts/search?"
           + urllib.parse.urlencode(params))
    await _partners_throttle()
    try:
        status, data = await http.get_json(
            url, headers=_partners_headers(),
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    for result in data.get("results", []) or []:
        try:
            if result.get("status") != "listed":
                continue
            if not _partners_attrs_match(result, model, backdrop):
                continue
            return {"price": float(result["price"]),
                    "venue": "Portals",
                    "num": int(result["external_collection_number"])}
        except (TypeError, ValueError, KeyError):
            continue
    return None


async def _partners_collection_id(http, slug):
    """Collection UUID for a Partners call (seeded map, else a live
    /api/collections fetch). None when the key is missing or the
    collection is unknown — the caller fails open."""
    if not config.PORTALS_PARTNERS_KEY:
        return None
    cid = PORTALS_COLLECTIONS.get(_norm(slug))
    if not cid:
        cid = (await _portals_collection_map(http)).get(_norm(slug))
    return cid or None


def _partners_scope_matches(scope, model, backdrop):
    """Does a collection offer's scope cover this gift? An empty
    list means "any" (per the OpenAPI), otherwise the gift's value
    must be listed. Symbol is never checked: the owner says it
    does not matter."""
    scope = scope or {}
    for kind, value in (("models", model), ("backdrops", backdrop)):
        allowed = scope.get(kind)
        if allowed and _norm(value) not in [_norm(v) for v in allowed]:
            return False
    return True


async def get_portals_buy_orders(http, slug, model, backdrop):
    """Top buy orders for one model+backdrop, via the Partners API.

    GET {PARTNERS}/collection-offers/{collection_id}/all?scope=trait
    &limit=100. The endpoint has NO trait filter and NO pagination
    (verified live 2026-11-08: filter_by_models / filter_by_backdrops
    / offset are all ignored — the page is always the top 100 trait
    offers by amount, and /top returns only the single highest trait
    offer, which is why it is not used here). Matching is therefore
    client-side, with _partners_scope_matches. Returns {"count",
    "count_exact", "top"} (top = highest matching amount in TON)
    or None when the key is missing, the collection is unknown, the
    request fails, or no offer in the page matches. count_exact is
    False when total_count exceeds the page (more offers exist below
    the page floor, so the count is a lower bound — rendered with a
    "+"). A combo whose offers sit below the page floor (Myth Mask:
    page floor 25 TON; Broken Time + Navy Blue offers ~11 TON)
    yields None — the buy-orders line is omitted, never guessed."""
    cid = await _partners_collection_id(http, slug)
    if not cid:
        return None
    url = (f"{config.PORTALS_PARTNERS_BASE}/collection-offers/{cid}/all"
           f"?scope=trait&limit=100")
    await _partners_throttle()
    try:
        status, data = await http.get_json(
            url, headers=_partners_headers(),
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    offers = data.get("offers") or []
    matches = []
    for offer in offers:
        try:
            if offer.get("status") != "pending":
                continue
            if not _partners_scope_matches(offer.get("scope"),
                                           model, backdrop):
                continue
            matches.append(float(offer["amount"]))
        except (TypeError, ValueError, KeyError):
            continue
    if not matches:
        return None
    total = data.get("total_count")
    if isinstance(total, int):
        count_exact = total <= len(offers)
    else:
        count_exact = len(offers) < 100
    return {"count": len(matches), "count_exact": count_exact,
            "top": max(matches)}


async def get_portals_recent_sales(http, slug, model, backdrop, n=3):
    """Last n completed sales of one model+backdrop, via the
    Partners API.

    GET {PARTNERS}/market/actions/?collection_id=<uuid>
    &filter_by_models=<model>&filter_by_backdrops=<backdrop>
    &action_types=sell&limit=20 (newest first). The query's
    action_types=sell returns purchase-type actions (verified live
    2026-11-08: action_types=buy returns the identical set, so
    sell alone is enough). Each action's nft.attributes are checked
    client-side against the requested model/backdrop, and only
    type == "purchase" counts as a sale. Returns [{"num", "price",
    "at"}] (newest first, "at" = the RFC 3339 created_at string)
    or None when the key is missing, the collection is unknown,
    the request fails, or no sale matches."""
    cid = await _partners_collection_id(http, slug)
    if not cid:
        return None
    params = {"collection_id": cid, "filter_by_models": model,
              "filter_by_backdrops": backdrop, "action_types": "sell",
              "limit": 20}
    url = (f"{config.PORTALS_PARTNERS_BASE}/market/actions/?"
           + urllib.parse.urlencode(params))
    await _partners_throttle()
    try:
        status, data = await http.get_json(
            url, headers=_partners_headers(),
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    sales = []
    for action in data.get("actions") or []:
        try:
            if action.get("type") != "purchase":
                continue
            nft = action.get("nft") or {}
            if not _partners_attrs_match(nft, model, backdrop):
                continue
            sales.append({
                "num": int(nft["external_collection_number"]),
                "price": float(action["amount"]),
                "at": action.get("created_at") or "",
            })
        except (TypeError, ValueError, KeyError):
            continue
        if len(sales) >= n:
            break
    return sales or None


# --- Thermos (keyless cross-market aggregator) --------------------------
# Collection display names seeded from GET /api/v1/collections
# (verified live 2026-11-08). Unknown slugs fall back to a live
# collections fetch (case-insensitive name match).
THERMOS_COLLECTIONS = {
    "mythmask": "Myth Mask",
    "plushpepe": "Plush Pepe",
}

# Thermos returns marketplace names in UPPERCASE; the report shows
# them the way users write them.
_THERMOS_MARKET_NAMES = {
    "GETGEMS": "GetGems",
    "MRKT": "MRKT",
    "PORTALS": "Portals",
    "TONNEL": "Tonnel",
    "FRAGMENT": "Fragment",
    "TELEGRAM": "Telegram",
}

_NANOTONS_PER_TON = 1_000_000_000


def _thermos_market(marketplace):
    return _THERMOS_MARKET_NAMES.get(
        (marketplace or "").upper(),
        (marketplace or "").title() or "—")


async def _thermos_collection_name(http, slug):
    """Display name for a slug ("mythmask" -> "Myth Mask"), from the
    seeded map, else a live /api/v1/collections fetch (cached 10
    min). The Thermos search takes collection NAMES, not slugs.
    Case-insensitive on the fallback path."""
    seeded = THERMOS_COLLECTIONS.get(_norm(slug))
    if seeded:
        return seeded
    now = time.time()
    if (_thermos_collections_cache["by_slug"]
            and now - _thermos_collections_cache["ts"]
            < config.GIFT_INDEX_CACHE_TTL_S):
        return _thermos_collections_cache["by_slug"].get(_norm(slug))
    try:
        status, data = await http.get_json(
            f"{config.THERMOS_API}/collections",
            headers=_HEADERS, timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not isinstance(data, list):
        return None
    by_slug = {}
    for coll in data:
        name = coll.get("name")
        if name:
            by_slug[_norm(name)] = name
    if by_slug:
        _thermos_collections_cache["ts"] = now
        _thermos_collections_cache["by_slug"] = by_slug
    return by_slug.get(_norm(slug))


async def get_thermos_exact(http, slug, num):
    """Exact-instance listing price from the Thermos aggregator.

    POST {THERMOS_API}/gifts with {collections: [name], number: num,
    per_page: 5}; returns {"price", "venue": "Thermos · <Market>"}
    from the first item whose number matches, or None when the
    collection is unknown, the request fails, or the instance is not
    listed anywhere Thermos tracks. Price is a nanotons STRING in
    the JSON (1 TON = 1e9 nanotons). No auth header is sent. The
    search endpoint returns CACHED data (per the docs — and verified
    live: #47747 showed 18.9 while Portals lists it at 18.0), so
    this is a fallback source only, never the first exact lookup;
    no "validated live" claim is ever made about its price."""
    name = await _thermos_collection_name(http, slug)
    if not name:
        return None
    payload = {"collections": [name], "number": num, "per_page": 5}
    try:
        status, data = await http.post_json(
            f"{config.THERMOS_API}/gifts", payload,
            headers={"Content-Type": "application/json", **_HEADERS},
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    for item in data.get("items", []) or []:
        try:
            if int(item.get("number")) == num:
                return {"price": float(item["price"])
                        / _NANOTONS_PER_TON,
                        "venue": "Thermos · "
                        + _thermos_market(item.get("marketplace"))}
        except (TypeError, ValueError, KeyError):
            continue
    return None


async def get_thermos_floor(http, slug, model=None, backdrop=None):
    """Cheapest Thermos listing (TON) for one model / model+backdrop.

    POST {THERMOS_API}/gifts with {collections: [name],
    models: [model], backdrops: [backdrop], ordering: "PRICE_ASC",
    per_page: 5}. Returns the price, or None when nothing matches.
    The models/backdrops filters are exact (verified live:
    models=["Horn"] and backdrops=["Mint"] return 0 items), but the
    code still checks each returned item's attributes match the
    requested ones exactly — case-insensitive — as cheap insurance;
    the cheapest such item wins. Same cached-data caveat as
    get_thermos_exact: a fallback floor, never the primary one."""
    if model is None and backdrop is None:
        return None
    name = await _thermos_collection_name(http, slug)
    if not name:
        return None
    payload = {"collections": [name], "ordering": "PRICE_ASC",
               "per_page": 5}
    if model:
        payload["models"] = [model]
    if backdrop:
        payload["backdrops"] = [backdrop]
    try:
        status, data = await http.post_json(
            f"{config.THERMOS_API}/gifts", payload,
            headers={"Content-Type": "application/json", **_HEADERS},
            timeout=config.PRICE_TIMEOUT_S)
    except Exception:
        return None
    if status != 200 or not isinstance(data, dict):
        return None
    for item in data.get("items", []) or []:
        try:
            item_model = (item.get("model") or {}).get("name") or ""
            if model and _norm(item_model) != _norm(model):
                continue
            if backdrop:
                item_backdrop = (item.get("backdrop") or {}).get("name") \
                    or ""
                if _norm(item_backdrop) != _norm(backdrop):
                    continue
            return float(item["price"]) / _NANOTONS_PER_TON
        except (TypeError, ValueError, KeyError):
            continue
    return None


async def fetch_gift_data(http, entry, model=None, backdrop=None, num=None):
    """Fetch everything the /gift reply needs for one resolved gift.

    entry: index entry {slug, name}. model/backdrop: known attributes
    (from a gift link) or None. num: the instance number; when Tonnel
    is down and num is given, the model/combo floors are filled from
    floorprice.is's per-instance page instead. Returns a dict; any
    source that fails is listed in "failed" and its fields are None —
    never guessed."""
    slug = entry["slug"]
    name = tonnel_name(entry)
    frag_index = await fetch_index(http)
    on_fragment = any(e["slug"] == slug and e.get("source") == "fragment"
                      for e in frag_index)

    # Scoped floors (item reports only — the model/backdrop are the
    # gift's own). The Portals Partners API is the PREFERRED source:
    # it searches Portals' own listings by model/backdrop, which no
    # keyless source can do. Three sequential throttled calls (free
    # plan: 1 req/s); the Tonnel / floorprice.is / Thermos data
    # below fills whatever Partners misses. A collection query has
    # no model/backdrop, so it never calls Partners at all.
    model_floor = backdrop_floor = combo_floor = None
    if model is not None or backdrop is not None:
        if model is not None:
            model_floor = await get_portals_scoped_floor(
                http, slug, model=model)
        if backdrop is not None:
            backdrop_floor = await get_portals_scoped_floor(
                http, slug, backdrop=backdrop)
        if model is not None and backdrop is not None:
            combo_floor = await get_portals_scoped_floor(
                http, slug, model=model, backdrop=backdrop)

    # Portals Partners buy orders + recent sales (item reports only —
    # the scope is the gift's own model+backdrop). Two more throttled
    # calls, each failing open to None (their lines are then omitted,
    # and the rest of the report renders as before).
    buy_orders = partners_sales = None
    if model is not None and backdrop is not None:
        buy_orders = await get_portals_buy_orders(
            http, slug, model, backdrop)
        partners_sales = await get_portals_recent_sales(
            http, slug, model, backdrop)

    tonnel_items, frag_floor, fp_markets = await asyncio.gather(
        tonnel_search(http, name, limit=30),
        get_floor_ton(http, slug) if on_fragment else asyncio.sleep(0),
        get_floorprice_markets(http, slug),
    )
    if not isinstance(frag_floor, (int, float)):
        frag_floor = None

    floors = [i["price"] for i in (tonnel_items or [])]
    if frag_floor is not None:
        floors.append(frag_floor)
    if fp_markets and fp_markets.get("floor") is not None:
        floors.append(fp_markets["floor"])
    floor = min(floors) if floors else None

    model_floors, combo_floors = group_floors(tonnel_items or [])
    # Tonnel fallback for whatever Partners missed: the cheapest
    # Tonnel listing with the gift's own model / backdrop / combo.
    # {"price", "venue", "num"} or None — never a guess.
    if tonnel_items:
        if model is not None and model_floor is None:
            matches = [i for i in tonnel_items
                       if (i.get("model") or "") == model]
            if matches:
                best = min(matches, key=lambda i: i["price"])
                model_floor = {"price": best["price"], "venue": "Tonnel",
                               "num": best.get("num")}
        if backdrop is not None and backdrop_floor is None:
            matches = [i for i in tonnel_items
                       if (i.get("backdrop") or "") == backdrop]
            if matches:
                best = min(matches, key=lambda i: i["price"])
                backdrop_floor = {"price": best["price"],
                                  "venue": "Tonnel",
                                  "num": best.get("num")}
        if (model is not None and backdrop is not None
                and combo_floor is None):
            matches = [i for i in tonnel_items
                       if (i.get("model") or "") == model
                       and (i.get("backdrop") or "") == backdrop]
            if matches:
                best = min(matches, key=lambda i: i["price"])
                combo_floor = {"price": best["price"], "venue": "Tonnel",
                               "num": best.get("num")}
    if tonnel_items is None and num is not None:
        # Tonnel is down (its Cloudflare challenge blocks this VM's
        # egress IP intermittently, and the serverless egress always):
        # the floorprice.is per-instance page carries the same
        # model/combo floors in its "What similar gifts are listed
        # for" table. One extra ~0.5s GET, only on this path.
        try:
            fp_status, fp_html = await http.get_text(
                f"{config.FLOORPRICE_BASE}/{slug}-{num}",
                headers=_HEADERS, timeout=config.PRICE_TIMEOUT_S)
        except Exception:
            fp_status, fp_html = 0, ""
        if fp_status == 200 and fp_html:
            for row in parse_fp_similar_detail(fp_html):
                label, price = row["label"], row["price"]
                if " + " in label:
                    combo_floors.append((label, price, 1))
                elif label.endswith(" floor"):
                    name = label[:-len(" floor")]
                    if model is None or name == model:
                        model_floors.append((name, price, 1))
                # The same rows fill the singular scoped floors
                # (with venue + instance number from the "Where"
                # cell) when Partners missed them.
                if (model is not None and model_floor is None
                        and label == f"{model} floor"):
                    model_floor = {"price": price,
                                   "venue": row["venue"],
                                   "num": row["num"]}
                if (backdrop is not None and backdrop_floor is None
                        and label == f"{backdrop} floor"):
                    backdrop_floor = {"price": price,
                                      "venue": row["venue"],
                                      "num": row["num"]}
                if (model is not None and backdrop is not None
                        and combo_floor is None
                        and label == f"{model} + {backdrop}"):
                    combo_floor = {"price": price,
                                   "venue": row["venue"],
                                   "num": row["num"]}
            model_floors.sort(key=lambda t: t[1])
            combo_floors.sort(key=lambda t: t[1])
        # Third fallback: Thermos (cached cross-market search). Only
        # for the gift's OWN model/backdrop/combo (known from the
        # link) and only when the floorprice.is table left that
        # entry empty — listed fp pages have no similar table at
        # all, so without this the model floors would be empty
        # whenever Tonnel is down. One POST per missing entry, only
        # on this path. (Thermos is an aggregator search, not one
        # marketplace: the venue is Thermos itself and no instance
        # number is kept.)
        if (model is not None and model_floor is None and not any(
                m == model for m, _p, _n in model_floors)):
            thermos_model = await get_thermos_floor(http, slug,
                                                    model=model)
            if thermos_model is not None:
                model_floors.append((model, thermos_model, 1))
                model_floors.sort(key=lambda t: t[1])
                model_floor = {"price": thermos_model,
                               "venue": "Thermos", "num": None}
        if backdrop is not None and backdrop_floor is None:
            thermos_backdrop = await get_thermos_floor(
                http, slug, backdrop=backdrop)
            if thermos_backdrop is not None:
                backdrop_floor = {"price": thermos_backdrop,
                                  "venue": "Thermos", "num": None}
        if (model is not None and backdrop is not None
                and combo_floor is None and not any(
                    c == f"{model} + {backdrop}"
                    for c, _p, _n in combo_floors)):
            thermos_combo = await get_thermos_floor(
                http, slug, model=model, backdrop=backdrop)
            if thermos_combo is not None:
                combo_floors.append(
                    (f"{model} + {backdrop}", thermos_combo, 1))
                combo_floors.sort(key=lambda t: t[1])
                combo_floor = {"price": thermos_combo,
                               "venue": "Thermos", "num": None}

    # Last sales (Fragment sold pages; only meaningful with a model).
    # The sold-page fetches are independent — run them together,
    # including one unfiltered page for the overall last 3 sales.
    last_model, last_combo = None, None
    sale_model, sale_combo = None, None
    recent_sales = None
    if on_fragment:
        top_model = model or (model_floors[0][0] if model_floors else None)
        if top_model:
            sale_model = top_model
        top_combo = None
        if backdrop and model:
            top_combo = (model, backdrop)
        elif combo_floors:
            label = combo_floors[0][0]
            parts = label.split(" + ", 1)
            if len(parts) == 2:
                top_combo = (parts[0], parts[1])
        if top_combo:
            sale_combo = top_combo
        sold_model, sold_combo, sold_all = await asyncio.gather(
            get_sold_items(http, slug, model=top_model)
            if top_model else asyncio.sleep(0),
            get_sold_items(http, slug, model=top_combo[0],
                           backdrop=top_combo[1])
            if top_combo else asyncio.sleep(0),
            get_sold_items(http, slug),
        )
        if top_model:
            last_model = last_sale(sold_model)
        if top_combo:
            last_combo = last_sale(sold_combo)
        recent_sales = last_sales(sold_all)

    failed = []
    if tonnel_items is None:
        failed.append("Tonnel")
    if on_fragment and frag_floor is None:
        failed.append("Fragment")
    if fp_markets is None:
        failed.append("floorprice.is")

    return {
        "name": _title(entry.get("name") or slug),
        "slug": slug,
        "tonnel_name": name,
        "floor": floor,
        "tonnel_floor": (tonnel_items[0]["price"] if tonnel_items else None),
        "fragment_floor": frag_floor,
        "markets": fp_markets["markets"] if fp_markets else None,
        "cheapest_num": fp_markets.get("cheapest_num") if fp_markets else None,
        "model_floors": model_floors[:5],
        "combo_floors": combo_floors[:5],
        "model_floor": model_floor,
        "backdrop_floor": backdrop_floor,
        "combo_floor": combo_floor,
        "buy_orders": buy_orders,
        "partners_sales": partners_sales,
        "last_sale_model": last_model,
        "last_sale_combo": last_combo,
        "recent_sales": recent_sales,
        "sale_model": sale_model,
        "sale_combo": sale_combo,
        "model": model,
        "backdrop": backdrop,
        "on_fragment": on_fragment,
        "failed": failed,
    }
