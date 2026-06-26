from __future__ import annotations

import re


_FINANCE_SECURITY_RE = re.compile(
    r"\b(?:aktie|aktien|stock|share|shares|equity|securities?)\b",
    re.IGNORECASE,
)
_FINANCE_LISTING_RE = re.compile(
    r"\b(?:"
    r"börsennotiert|boersennotiert|(?:öffentlich|oeffentlich)\s+gelistet|"
    r"listed|listing|publicly\s+(?:traded|listed)|ipo|ticker|"
    r"stock\s+exchange|nasdaq|nyse|an\s+der\s+börse|an\s+der\s+boerse|"
    r"private\s+company|privately\s+held"
    r")\b",
    re.IGNORECASE,
)
_FINANCE_DERIVATIVE_EXCHANGE_RE = re.compile(
    r"\b(?:"
    r"derivat|derivative|tokeni[sz]ed|perpetual|pre[-\s]?market|bybit|usdt|[A-Z]{2,16}USDT"
    r")\b",
    re.IGNORECASE,
)
_FINANCE_EXPOSURE_RE = re.compile(
    r"\b(?:kaufen|buy|trade|traden|handeln|handelbar|investieren|invest|exposure|zugang)\b",
    re.IGNORECASE,
)
_STOCK_LISTING_STATUS_RE = re.compile(
    r"\b(?:"
    r"börsennotiert|boersennotiert|(?:öffentlich|oeffentlich)\s+gelistet|"
    r"listed|listing|publicly\s+(?:traded|listed)|ipo|ticker|"
    r"stock\s+exchange|nasdaq|nyse|an\s+der\s+börse|an\s+der\s+boerse"
    r")\b",
    re.IGNORECASE,
)

_WEATHER_RE = re.compile(r"\b(?:wetter|weather|temperatur|temperature|regen|rain|forecast|vorhersage)\b", re.IGNORECASE)
_CRYPTO_RE = re.compile(
    r"\b(?:"
    r"btc|bitcoin|eth|ethereum|sol|solana|xrp|ripple|ada|cardano|doge|dogecoin|"
    r"bnb|binance\s+coin|dot|polkadot|matic|polygon|avax|avalanche|ltc|litecoin|"
    r"link|chainlink|xlm|stellar|ton|toncoin|trx|tron|"
    r"crypto|krypto|kryptow(?:ä|ae)hrung|usdt|bybit"
    r")\b",
    re.IGNORECASE,
)
_COMMON_CRYPTO_NOUN_RE = re.compile(r"\b(?:coin|coins|token)\b", re.IGNORECASE)
_CRYPTO_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"btc|bitcoin|eth|ethereum|sol|solana|xrp|ripple|ada|cardano|doge|dogecoin|"
    r"crypto|krypto|kryptow(?:ä|ae)hrung|kurs|price|preis|market|exchange|blockchain|"
    r"wallet|dex|cex|usdt|bybit|tokeni[sz]ed|perpetual|derivat|derivative|trade|traden|handeln"
    r")\b",
    re.IGNORECASE,
)
_CRYPTO_ASSET_HINT_RE = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9-]{1,24}(?:coin|token)|[A-Z0-9]{2,20}USDT)\b",
    re.IGNORECASE,
)
_STOCK_RE = re.compile(
    r"\b(?:aktie|aktien|stock|share|shares|nasdaq|nyse|stock\s+exchange|dax|etf|börse|boerse|ticker|filing|filings|"
    r"börsennotiert|boersennotiert|(?:öffentlich|oeffentlich)\s+gelistet|"
    r"listed|publicly\s+(?:traded|listed)|ipo|listing|"
    r"fundamental|fundamentals|research|dividende|dividend|earnings|kgv)\b",
    re.IGNORECASE,
)
_NEWS_RE = re.compile(r"\b(?:news|nachrichten|neueste(?:n)?|latest|breaking|was\s+gibt\s+es\s+(?:heute\s+)?neues)\b", re.IGNORECASE)
_CURRENT_MARKET_RE = re.compile(
    r"\b(?:kurs|price|preis|jetzt|now|aktuell|current|macht|steht|börse|boerse|nasdaq|nyse|stock\s+exchange|"
    r"börsennotiert|boersennotiert|(?:öffentlich|oeffentlich)\s+gelistet|"
    r"listed|publicly\s+(?:traded|listed)|ipo|listing|derivat|derivative|"
    r"kaufen|buy|trade|traden|handeln|handelbar)\b",
    re.IGNORECASE,
)
_FINANCE_RESEARCH_SIGNAL_RE = re.compile(
    r"\b(?:fundamental|research|filing|filings|earnings|dividende|dividend|kgv)\b",
    re.IGNORECASE,
)
_SPORTS_SIGNAL_RE = re.compile(
    r"\b(?:"
    r"wm|fussball|fußball|fifa|world\s+cup|em|euro|bundesliga|champions\s+league|"
    r"nba|nfl|nhl|mlb|formel\s*1|formula\s*1|f1|tabelle|standings|spielplan|score|ergebnis"
    r")\b",
    re.IGNORECASE,
)


def is_finance_listing_query(text: str) -> bool:
    raw = text or ""
    if _FINANCE_LISTING_RE.search(raw):
        return True
    if _FINANCE_DERIVATIVE_EXCHANGE_RE.search(raw):
        return True
    return bool(_FINANCE_SECURITY_RE.search(raw) and _FINANCE_EXPOSURE_RE.search(raw))


def is_stock_listing_status_query(text: str) -> bool:
    raw = text or ""
    if is_derivative_exchange_query(raw):
        return False
    if _STOCK_LISTING_STATUS_RE.search(raw):
        return True
    return bool(_FINANCE_SECURITY_RE.search(raw) and _FINANCE_EXPOSURE_RE.search(raw))


def classify_evidence_domain(text: str) -> str:
    raw = text or ""
    if _WEATHER_RE.search(raw):
        return "weather"
    if is_derivative_exchange_query(raw):
        return "crypto"
    has_crypto_signal = bool(
        _CRYPTO_RE.search(raw)
        or _CRYPTO_ASSET_HINT_RE.search(raw)
        or (_COMMON_CRYPTO_NOUN_RE.search(raw) and _CRYPTO_CONTEXT_RE.search(raw))
    )
    if has_crypto_signal:
        if re.search(
            r"\b(?:aktie|stock|share|shares|börsennotiert|boersennotiert|"
            r"(?:öffentlich|oeffentlich)\s+gelistet|listed|publicly\s+(?:traded|listed)|"
            r"ipo|nasdaq|nyse|stock\s+exchange)\b",
            raw,
            re.IGNORECASE,
        ):
            return "stock"
        return "crypto"
    if is_finance_listing_query(raw):
        return "stock"
    if _STOCK_RE.search(raw) and (_CURRENT_MARKET_RE.search(raw) or _FINANCE_RESEARCH_SIGNAL_RE.search(raw)):
        return "stock"
    if _SPORTS_SIGNAL_RE.search(raw):
        return "sports"
    if _NEWS_RE.search(raw):
        return "news"
    return "generic"


def is_derivative_exchange_query(text: str) -> bool:
    return bool(
        re.search(r"\b(?:bybit|[A-Z]{2,16}USDT)\b", text or "", re.IGNORECASE)
        and re.search(
            r"\b(?:bybit|[A-Z]{2,16}USDT|usdt|tokeni[sz]ed|perpetual|derivat|derivative|exposure|pre[-\s]?market|trade|traden|handeln)\b",
            text or "",
            re.IGNORECASE,
        )
    )
