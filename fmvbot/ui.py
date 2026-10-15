"""Recognising buttons in popups from their name, label text and textures."""

import re

POSITIVE = re.compile(
    r"\b(collect|claim|continue|ok|okay|got it|accept|yes|great|awesome|thanks|thank you|next|done|"
    r"open|play|start|get|free|receive|reward|cool|nice|let'?s go|go)\b",
    re.I,
)
NEGATIVE = re.compile(r"\b(cancel|no thanks|not now|later|skip|close|back)\b", re.I)
CLOSE_TEX = re.compile(r"(close|btn_x\b|_x_|cross|exit)", re.I)
# buttons that leave the game or tie the account to something: never press them
AVOID = re.compile(r"(sign ?in|log ?in|login|invite|share|connect|link account|subscribe|rate us|download|install)", re.I)
MONEY_NAME = re.compile(r"^(purchaseIAP|purchaseInventory|purchasePremium|iap)", re.I)
PRICE_TEX = re.compile(r"(gem|diamond|ruby|premium|price|purchase|buy|cart|ad_|video|watch)", re.I)


def label(b: dict) -> str:
    return " ".join([b.get("name") or "", b.get("text") or "", " ".join(b.get("tex") or [])]).strip()


def is_close(b: dict) -> bool:
    name = (b.get("name") or "").lower()
    text = (b.get("text") or "").strip()
    return "close" in name or text in ("X", "x", "✕", "×") or any(CLOSE_TEX.search(t) for t in b.get("tex") or [])


AD_TEXT = re.compile(r"\b(watch|ad|ads|video)\b", re.I)
# generic popups: any buy/purchase wording means it isn't a free claim
BUY_TEXT = re.compile(r"\b(buy|purchase)\b", re.I)
PREMIUM_TEX = re.compile(r"(gem|diamond|ruby|premium|video|_ad_|ads_|cash|dollar)", re.I)


def spends_premium(b: dict) -> bool:
    """Costs gems or real money, or plays an ad. Energy and coin costs don't count."""
    tex = " ".join(b.get("tex") or [])
    text = b.get("text") or ""
    name = b.get("name") or ""
    return bool(
        b.get("priced")
        or MONEY_NAME.search(name)
        or PREMIUM_TEX.search(tex)
        or AD_TEXT.search(text)
        or "$" in text
        or "€" in text
        or "£" in text
    )


def costs_something(b: dict) -> bool:
    """Buttons that show a price, a gem, or start an ad."""
    tex = " ".join(b.get("tex") or [])
    text = b.get("text") or ""
    name = b.get("name") or ""
    if spends_premium(b) or BUY_TEXT.search(text):
        return True
    if PRICE_TEX.search(tex) or PRICE_TEX.search(name):
        return True
    return bool(re.search(r"\d", text)) and not POSITIVE.search(text)


def is_free(b: dict) -> bool:
    """A button that offers something for free (e.g. the free skip on short timers)."""
    text = (b.get("text") or "").lower()
    tex = " ".join(b.get("tex") or [])
    return "free" in text and not re.search(r"\d", text) and "video" not in tex and not AD_TEXT.search(text)


def is_positive(b: dict) -> bool:
    if costs_something(b) or is_close(b) or AVOID.search(b.get("text") or "") or AVOID.search(b.get("name") or ""):
        return False
    text = b.get("text") or ""
    name = b.get("name") or ""
    tex = " ".join(b.get("tex") or [])
    return bool(POSITIVE.search(text) or POSITIVE.search(name) or re.search(r"btn_(green|confirm|ok|collect|claim)", tex, re.I))


def is_negative(b: dict) -> bool:
    return bool(NEGATIVE.search(b.get("text") or "")) or is_close(b)


def biggest(buttons: list[dict]) -> dict | None:
    return max(buttons, key=lambda b: b["w"] * b["h"], default=None)


def find(buttons: list[dict], pattern: str) -> dict | None:
    rx = re.compile(pattern, re.I)
    hits = [b for b in buttons if rx.search(label(b))]
    return biggest(hits)
