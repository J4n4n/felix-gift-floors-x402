"""Configuration for the x402 gift floor-price API.

Everything public lives here. The ONLY secret this service needs
(Portals Partners key) is read from ~/workspace/felix-bot/.env at
startup and is never logged, printed, or included in any response.

Money facts (verified 2026-11-08):
- Price: $0.05 USDC per call = 50000 atomic units (USDC, 6 decimals).
- Network: Base, eip155:8453.
- Asset: native USDC on Base, 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
  (verified against Circle's docs, the CDP SDK address map, and
  on-chain reads; the x402 SDK's own default-asset table agrees).
- payTo: Janan's public EVM address (from his own records).
- Facilitator: PayAI (https://facilitator.payai.network) — no account,
  no API key. NEVER Coinbase CDP: CDP runs KYT/OFAC checks and Iran
  is a prohibited region, so CDP signup would be blocked and
  settlements could be frozen.
"""
import os
from pathlib import Path

# --- Payment ---------------------------------------------------------------
# $0.05 USDC per call, on Base.
PRICE_DISPLAY = os.environ.get("X402_PRICE", "$0.05")
PRICE_AMOUNT = "50000"  # 0.05 USDC, 6 decimals
NETWORK = "eip155:8453"
ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # native USDC on Base
ASSET_SYMBOL = "USDC"
ASSET_DECIMALS = 6
# Janan's public EVM address (public, from his own records).
PAY_TO = os.environ.get(
    "X402_PAY_TO",
    "0x348614Ec71B97b7e3A309b29b4bc21d8f986D695",
)
# PayAI facilitator — no account, no key. NOT Coinbase CDP (see above).
FACILITATOR_URL = os.environ.get(
    "X402_FACILITATOR_URL", "https://facilitator.payai.network"
)
# How long a signed payment stays valid (seconds).
MAX_TIMEOUT_SECONDS = 300

# --- Service ----------------------------------------------------------------
SERVICE_NAME = "Felix Gift Floors"  # <= 32 chars (Bazaar cap)
SERVICE_DESCRIPTION = (
    "Telegram NFT gift floor prices: collection, collection+model, "
    "collection+backdrop and model+backdrop floors, plus buy orders "
    "and recent sales. Data aggregated live from Portals, Tonnel, "
    "Fragment, floorprice.is and Thermos."
)
SERVICE_TAGS = ["telegram-gifts", "nft", "floor-price", "ton"]
HOST = os.environ.get("X402_HOST", "127.0.0.1")
PORT = int(os.environ.get("X402_PORT", "8787"))

# --- Felix reuse -------------------------------------------------------------
FELIX_DIR = Path(os.environ.get(
    "FELIX_DIR", str(Path.home() / "workspace" / "felix-bot")))
FELIX_ENV_FILE = FELIX_DIR / ".env"

# --- Test mode ---------------------------------------------------------------
# X402_TEST_MODE=1 bypasses payment verification and serves the data
# for free. LOCAL TESTING ONLY — never deploy with this set. The
# service logs a loud warning at startup when it is on.
TEST_MODE = os.environ.get("X402_TEST_MODE", "0") == "1"
