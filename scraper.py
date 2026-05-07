#!/usr/bin/env python3
"""
00981A ETF Holdings Scraper

Data sources (in priority order):
  1. TWSE OpenAPI  — openapi.twse.com.tw
  2. TWSE HTML scraper — www.twse.com.tw/fund/ETF_tf.html
  3. Fund company  — 中信投信 ctbcasset.com.tw
  4. Previous day's data (fallback — never overwrites with empty)

Usage:
  python scraper.py
"""

import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

ETF_CODE      = "00981A"
ETF_BASE_CODE = "00981"
DATA_DIR      = Path("data")
HOLDINGS_FILE = DATA_DIR / "holdings.json"
TIMEOUT       = 30
RETRIES       = 3
RETRY_DELAY   = 5  # seconds between retries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
    "Referer": "https://www.twse.com.tw/",
}

# ── Sector Lookup ─────────────────────────────────────────────────────────────

SECTOR_MAP: dict[str, str] = {
    # 半導體
    "2330": "半導體", "2454": "半導體", "2379": "半導體", "2303": "半導體",
    "3711": "半導體", "6770": "半導體", "2344": "半導體", "3034": "半導體",
    "2308": "半導體", "2449": "半導體", "3443": "半導體", "6415": "半導體",
    "8046": "半導體", "3044": "半導體", "2337": "半導體", "5347": "半導體",
    "2408": "半導體", "3036": "半導體", "3529": "半導體", "6462": "半導體",
    # 電子/電腦
    "2317": "電子", "2382": "電子", "2357": "電子", "3008": "電子",
    "2301": "電子", "2392": "電子", "2353": "電子", "2324": "電子",
    "6669": "電子", "2327": "電子", "2356": "電子", "2385": "電子",
    "2360": "電子", "3231": "電子", "4938": "電子",
    # 金融
    "2882": "金融", "2886": "金融", "2884": "金融", "2891": "金融",
    "2885": "金融", "2892": "金融", "2881": "金融", "2883": "金融",
    "2887": "金融", "5880": "金融", "2890": "金融",
    # 電信
    "2412": "電信", "3045": "電信", "4904": "電信",
    # 石化/材料
    "1301": "石化", "1303": "石化", "6505": "石化", "1326": "化工",
    # 鋼鐵
    "2002": "鋼鐵", "2006": "鋼鐵",
    # 消費/零售
    "2912": "消費", "9910": "消費", "2204": "消費",
    # 紡織
    "1402": "紡織",
    # 水泥
    "1101": "水泥",
}


def infer_sector(code: str) -> str:
    return SECTOR_MAP.get(code, "其他")


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_previous() -> Optional[dict]:
    if HOLDINGS_FILE.exists():
        try:
            data = json.loads(HOLDINGS_FILE.read_text(encoding="utf-8"))
            log.info("Loaded previous data (date: %s)", data.get("date", "?"))
            return data
        except Exception as e:
            log.error("Failed to read previous data: %s", e)
    return None


def save_data(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    HOLDINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Saved %d holdings to %s", len(data.get("holdings", [])), HOLDINGS_FILE)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def get(session: requests.Session, url: str, **kwargs) -> Optional[requests.Response]:
    for attempt in range(1, RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            log.warning("Attempt %d/%d failed for %s: %s", attempt, RETRIES, url, exc)
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * attempt)
    return None


# ── Data sources ──────────────────────────────────────────────────────────────

def _parse_holdings_table(soup: BeautifulSoup) -> Optional[list[dict]]:
    """
    Generic parser for TWSE-style HTML tables.
    Expected columns: 代號 | 名稱 | 持股比例(%)
    Adjust col indices if a specific site uses a different layout.
    """
    tables = soup.find_all("table")
    best: list[dict] = []

    for table in tables:
        rows = table.find_all("tr")
        parsed: list[dict] = []
        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cols) < 3:
                continue
            code = cols[0].strip()
            name = cols[1].strip()
            # Weight usually appears in the last numeric column
            weight_raw = next(
                (c.replace("%", "").replace(",", "") for c in reversed(cols) if c.replace(".", "").replace("%", "").replace(",", "").lstrip("-").isdigit()),
                None,
            )
            if not weight_raw or not code or not name:
                continue
            try:
                weight = float(weight_raw)
            except ValueError:
                continue
            # Filter noise: codes are 4-5 digits
            if not (4 <= len(code) <= 6 and code.isdigit()):
                continue
            parsed.append({
                "code": code,
                "name": name,
                "weight": round(weight, 4),
                "change": 0.0,
                "sector": infer_sector(code),
            })
        if len(parsed) > len(best):
            best = parsed

    return best if len(best) >= 3 else None


def fetch_openapi_twse() -> Optional[list[dict]]:
    """
    TWSE OpenAPI — check https://openapi.twse.com.tw for current endpoints.
    The ETF component endpoint may vary; try multiple candidates.
    """
    session = make_session()
    candidates = [
        f"https://openapi.twse.com.tw/v1/ETFdividend/ETFcomponent?etfcode={ETF_CODE}",
        "https://openapi.twse.com.tw/v1/ETFdividend/ETFcomponent",
    ]
    for url in candidates:
        log.info("Trying TWSE OpenAPI: %s", url)
        resp = get(session, url)
        if not resp:
            continue
        try:
            raw = resp.json()
            items = raw if isinstance(raw, list) else raw.get("data", [])
            holdings = []
            for item in items:
                # Filter to our ETF if the endpoint returns all ETFs
                etf_field = item.get("ETFcode", item.get("etfCode", item.get("ETFCode", "")))
                if etf_field and etf_field != ETF_CODE:
                    continue
                code   = str(item.get("Code",   item.get("code",   item.get("stockCode", "")))).strip()
                name   = str(item.get("Name",   item.get("name",   item.get("stockName", "")))).strip()
                weight = float(item.get("Ratio", item.get("ratio",  item.get("weight",    0))))
                if code and name and 4 <= len(code) <= 6:
                    holdings.append({
                        "code": code,
                        "name": name,
                        "weight": round(weight, 4),
                        "change": 0.0,
                        "sector": infer_sector(code),
                    })
            if len(holdings) >= 3:
                log.info("TWSE OpenAPI: %d holdings parsed", len(holdings))
                return holdings
        except Exception as exc:
            log.warning("OpenAPI parse error: %s", exc)
    return None


def fetch_twse_html() -> Optional[list[dict]]:
    """
    Scrape TWSE ETF component page.
    If the URL structure changes, inspect Network tab on
    https://www.twse.com.tw/fund/ETF_tf.html and update accordingly.
    """
    session = make_session()
    url = "https://www.twse.com.tw/fund/ETF_tf.html"
    log.info("Trying TWSE HTML scraper: %s", url)

    resp = get(session, url, params={"etfCode": ETF_CODE, "type": "html"})
    if not resp:
        return None
    resp.encoding = resp.apparent_encoding or "utf-8"
    try:
        soup = BeautifulSoup(resp.text, "lxml")
        holdings = _parse_holdings_table(soup)
        if holdings:
            log.info("TWSE HTML: %d holdings parsed", len(holdings))
        return holdings
    except Exception as exc:
        log.warning("TWSE HTML parse error: %s", exc)
        return None


def fetch_fund_company() -> Optional[list[dict]]:
    """
    Scrape 中信投信 (CTBC Asset Management) portfolio page.
    Update the URL if the website structure changes.
    Reference: https://www.ctbcasset.com.tw/
    """
    session = make_session()
    # Possible URL patterns — try both
    candidates = [
        f"https://www.ctbcasset.com.tw/fund/etf/{ETF_CODE}/portfolio",
        f"https://www.ctbcasset.com.tw/fund/detail?code={ETF_CODE}",
    ]
    for url in candidates:
        log.info("Trying fund company: %s", url)
        resp = get(session, url)
        if not resp:
            continue
        resp.encoding = resp.apparent_encoding or "utf-8"
        try:
            soup = BeautifulSoup(resp.text, "lxml")
            holdings = _parse_holdings_table(soup)
            if holdings:
                log.info("Fund company: %d holdings parsed", len(holdings))
                return holdings
        except Exception as exc:
            log.warning("Fund company parse error: %s", exc)
    return None


# ── Metrics ───────────────────────────────────────────────────────────────────

def fetch_metrics(holdings_count: int) -> dict:
    metrics: dict = {
        "scale": "N/A",
        "nav": "N/A",
        "return_ytd": "N/A",
        "return_1y": "N/A",
        "holdings_count": holdings_count,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    session = make_session()
    try:
        resp = get(session, "https://openapi.twse.com.tw/v1/ETFdividend/ETFcapital")
        if resp:
            for item in resp.json():
                if item.get("ETFcode") == ETF_CODE or item.get("code") == ETF_CODE:
                    metrics["scale"] = item.get("Scale", item.get("scale", "N/A"))
                    metrics["nav"]   = item.get("NAV",   item.get("nav",   "N/A"))
                    break
    except Exception as exc:
        log.debug("Metrics fetch error: %s", exc)
    return metrics


# ── Change detection ──────────────────────────────────────────────────────────

def detect_changes(current: list[dict], previous: Optional[dict]) -> dict:
    empty: dict = {"added": [], "removed": [], "increased": [], "decreased": []}
    if not previous or "holdings" not in previous:
        return empty

    prev = {h["code"]: h for h in previous["holdings"]}
    curr = {h["code"]: h for h in current}

    added   = [curr[c] for c in curr if c not in prev]
    removed = [prev[c] for c in prev if c not in curr]

    increased, decreased = [], []
    for code, h in curr.items():
        if code not in prev:
            continue
        delta = round(h["weight"] - prev[code]["weight"], 4)
        if abs(delta) >= 0.01:
            item = {**h, "change": delta}
            (increased if delta > 0 else decreased).append(item)

    return {
        "added":     added,
        "removed":   removed,
        "increased": sorted(increased, key=lambda x: x["change"], reverse=True)[:10],
        "decreased": sorted(decreased, key=lambda x: x["change"])[:10],
    }


# ── Sample data (first-run fallback) ─────────────────────────────────────────

def generate_sample() -> list[dict]:
    log.warning("Generating SAMPLE holdings — real data unavailable on first run.")
    rows = [
        ("2330", "台積電",     22.50, "半導體"),
        ("2454", "聯發科",      8.30, "半導體"),
        ("2382", "廣達",        5.20, "電子"),
        ("3711", "日月光投控",   4.80, "半導體"),
        ("2379", "瑞昱",        4.10, "半導體"),
        ("2303", "聯電",        3.90, "半導體"),
        ("6770", "力積電",      3.50, "半導體"),
        ("2344", "華邦電",      3.20, "半導體"),
        ("3034", "聯詠",        3.00, "半導體"),
        ("2308", "台達電",      2.80, "電子"),
        ("2449", "京元電子",    2.60, "半導體"),
        ("3443", "創意",        2.40, "半導體"),
        ("6415", "矽力-KY",     2.20, "半導體"),
        ("8046", "南電",        2.00, "半導體"),
        ("3044", "健鼎",        1.80, "電子"),
        ("2337", "旺宏",        1.70, "半導體"),
        ("5347", "世界先進",    1.60, "半導體"),
        ("2317", "鴻海",        1.50, "電子"),
        ("2357", "華碩",        1.40, "電子"),
        ("2392", "正崴",        1.30, "電子"),
    ]
    return [
        {"code": c, "name": n, "weight": w, "change": 0.0, "sector": s}
        for c, n, w, s in rows
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    today = date.today().strftime("%Y-%m-%d")
    log.info("=== 00981A ETF Scraper — %s ===", today)

    previous = load_previous()

    holdings = (
        fetch_openapi_twse()
        or fetch_twse_html()
        or fetch_fund_company()
    )

    if not holdings:
        if previous:
            log.warning("All sources failed — preserving previous data unchanged.")
            previous["fetch_attempted"] = datetime.now().isoformat()
            save_data(previous)
            return
        log.warning("No previous data. Using sample data for initial run.")
        holdings = generate_sample()

    # Attach per-holding change delta vs previous day
    if previous and "holdings" in previous:
        prev_map = {h["code"]: h for h in previous["holdings"]}
        for h in holdings:
            if h["code"] in prev_map:
                h["change"] = round(h["weight"] - prev_map[h["code"]]["weight"], 4)

    metrics = fetch_metrics(len(holdings))
    changes = detect_changes(holdings, previous)

    save_data({
        "date":       today,
        "fetched_at": datetime.now().isoformat(),
        "holdings":   sorted(holdings, key=lambda x: x["weight"], reverse=True),
        "metrics":    metrics,
        "changes":    changes,
    })
    log.info("Done.")


if __name__ == "__main__":
    main()
