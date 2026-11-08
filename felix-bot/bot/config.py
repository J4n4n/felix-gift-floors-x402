"""Configuration for the Felix bot. Everything secret comes from env vars.

No bot token, API key or private key lives in this file or in git.
All data sources are free and keyless (see README for the live-verified
list). The TON address below is a PUBLIC wallet address, not a secret.
"""
import os


def _env(name, default=""):
    return os.environ.get(name, default)


# --- Telegram -------------------------------------------------------------
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
BOT_USERNAME = _env("BOT_USERNAME", "Felixagent")  # without @

# --- Free, keyless upstream APIs (all verified live 2026-11-08) -----------
# Brain: Gemini API (free AI Studio key) — see bot/felix.py. Pollinations
# was removed completely on the owner's order (2026-11-08).
# Prices: CoinGecko free simple-price + search (no key).
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
# Contract lookup fallback: DexScreener free API (no key).
DEXSCREENER_BASE = "https://api.dexscreener.com"
# Toman market rate: tgju.org public JSON (free-market rate, NOT 42,000).
TGJU_URL = (
    "https://api.tgju.org/v1/market/indicator/"
    "summary-table-data/price_dollar_rl"
)
# Telegram NFT gifts: Fragment public gift pages (HTML, no key).
FRAGMENT_BASE = "https://fragment.com"
# Tonnel marketplace public listings API (POST, no auth).
TONNEL_API = "https://gifts2.tonnel.network/api/pageGifts"
# floorprice.is gift-floor aggregator (HTML, no key).
FLOORPRICE_BASE = "https://floorprice.is"
# Portals marketplace mini-app public read API (JSON, no key, no auth
# header — verified live 2026-11-08): /api/collections (short_name ->
# UUID, floors) + /api/nfts/search (exact-instance listing price).
PORTALS_API = "https://portal-market.com/api"
# Portals Partners API (free plan, self-issued key — verified live
# 2026-11-08): GET /partners/nfts/search with collection_id +
# filter_by_models / filter_by_backdrops (scoped floors: cheapest
# listing with a given model / backdrop / model+backdrop) +
# /partners/collection-offers (buy orders) + /partners/market/actions
# (recent sales). Auth: "Authorization: partners <key>". Free plan:
# 1 request/second — the bot throttles to that. The key lives in
# PORTALS_PARTNERS_KEY (env) and is never logged or hard-coded.
PORTALS_PARTNERS_BASE = "https://portal-market.com/partners"
PORTALS_PARTNERS_KEY = _env("PORTALS_PARTNERS_KEY")
PARTNERS_MIN_INTERVAL_S = 1.0  # free-plan rate limit
# Thermos gift aggregator (swap.coffee) public proxy API (JSON, no
# key, no auth header — verified live 2026-11-08): POST /api/v1/gifts
# (search by collection name / number / model / backdrop, prices in
# nanotons) + GET /api/v1/collections. Cross-market (GetGems, MRKT,
# Portals...). NOTE: search returns CACHED data (per the docs, prices
# may be stale — verified: #47747 showed 18.9 while Portals lists it
# at 18.0) — so Thermos is a fallback exact source, never the first.
# The models/backdrops filters are EXACT (models=["Horn"] and
# backdrops=["Mint"] return 0 items) — the code still checks each
# returned item's attributes match exactly, as cheap insurance.
THERMOS_API = "https://proxy.thermos.gifts/api/v1"

# --- Caching / limits -----------------------------------------------------
PRICE_CACHE_TTL_S = 60        # CoinGecko simple-price cache
TOMAN_CACHE_TTL_S = 600       # tgju rate cache (10 min)
GIFT_INDEX_CACHE_TTL_S = 600  # Fragment /gifts index cache (10 min)
RATE_LIMIT_PER_MIN = 10       # max messages answered per user per minute
CHAT_TIMEOUT_S = 15           # Gemini chat request timeout
PRICE_TIMEOUT_S = 15
