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
COMPARE_DIR   = DATA_DIR / "compare"
OVERLAP_FILE  = DATA_DIR / "overlap.json"
TIMEOUT       = 30
RETRIES       = 3
RETRY_DELAY   = 5  # seconds between retries

# ETFs to compare against 00981A for overlap analysis
COMPARISON_ETFS: dict[str, str] = {
    "00891":  "中信關鍵半導體",
    "00892":  "富邦台灣半導體",
    "00982A": "富邦半導體正2",
    "0050":   "元大台灣50",
    "0056":   "元大高股息",
    "00919":  "群益精選高息",
}

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
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    HOLDINGS_FILE.write_text(payload, encoding="utf-8")
    log.info("Saved %d holdings to %s", len(data.get("holdings", [])), HOLDINGS_FILE)
    # Also write a dated snapshot so the date-picker in the UI can load history
    dated = DATA_DIR / f"holdings-{data.get('date', date.today().isoformat())}.json"
    dated.write_text(payload, encoding="utf-8")
    log.info("Dated snapshot: %s", dated)


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

def _parse_numeric_cols(cols: list[str]) -> tuple[Optional[float], Optional[int]]:
    """
    Return (weight_pct, shares) parsed from a table row's columns.
    weight: small float 0-100 (last such column)
    shares: large integer > 1000 (largest such column, converted to shares if given in 張)
    """
    weight: Optional[float] = None
    shares: Optional[int]   = None

    for raw in reversed(cols):
        clean = raw.replace("%", "").replace(",", "").strip()
        if not clean.lstrip("-").replace(".", "", 1).isdigit():
            continue
        try:
            val = float(clean)
        except ValueError:
            continue
        if 0 < val <= 100 and weight is None:
            weight = val
        elif val > 1000 and shares is None:
            # Might be in 張 (lots) or in 股 (shares); keep as raw integer
            shares = int(val)

    return weight, shares


def _parse_holdings_table(soup: BeautifulSoup) -> Optional[list[dict]]:
    """
    Generic parser for TWSE-style HTML tables.
    Extracts: 代號 | 名稱 | 持股比例(%) | 持有股數/張數 (if present)
    """
    tables = soup.find_all("table")
    best: list[dict] = []

    for table in tables:
        rows   = table.find_all("tr")
        parsed: list[dict] = []

        # Detect header to guess if shares column is in 張 or 股
        header_text = " ".join(
            td.get_text(strip=True) for td in (rows[0].find_all(["td", "th"]) if rows else [])
        )
        shares_in_zhang = "張" in header_text  # True → multiply by 1000 to get shares

        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cols) < 3:
                continue

            code = cols[0].strip()
            name = cols[1].strip()
            if not (4 <= len(code) <= 6 and code.isdigit()):
                # Try second and third columns
                code = cols[1].strip() if len(cols) > 1 else ""
                name = cols[2].strip() if len(cols) > 2 else ""
                if not (4 <= len(code) <= 6 and code.isdigit()):
                    continue

            weight, shares_raw = _parse_numeric_cols(cols)
            if weight is None:
                continue

            shares: Optional[int] = None
            if shares_raw is not None:
                shares = shares_raw * 1000 if shares_in_zhang else shares_raw

            parsed.append({
                "code":   code,
                "name":   name,
                "weight": round(weight, 4),
                "shares": shares,
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
                    shares_raw = item.get("Shares", item.get("shares",
                                 item.get("SharesHeld", item.get("sharesHeld",
                                 item.get("Volume", item.get("volume", None))))))
                    shares: Optional[int] = None
                    if shares_raw is not None:
                        try:
                            shares = int(float(str(shares_raw).replace(",", "")))
                        except (ValueError, TypeError):
                            pass
                    holdings.append({
                        "code":   code,
                        "name":   name,
                        "weight": round(weight, 4),
                        "shares": shares,
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

    # Source 1: ETFcapital → scale + NAV
    try:
        resp = get(session, "https://openapi.twse.com.tw/v1/ETFdividend/ETFcapital")
        if resp:
            for item in resp.json():
                if item.get("ETFcode") == ETF_CODE or item.get("code") == ETF_CODE:
                    metrics["scale"] = item.get("Scale", item.get("scale", "N/A"))
                    metrics["nav"]   = item.get("NAV",   item.get("nav",   "N/A"))
                    break
    except Exception as exc:
        log.debug("ETFcapital fetch error: %s", exc)

    # Source 2: ETFperformance → YTD / 1Y returns
    try:
        resp = get(session, "https://openapi.twse.com.tw/v1/ETFdividend/ETFperformance")
        if resp:
            for item in resp.json():
                if item.get("ETFcode") == ETF_CODE or item.get("code") == ETF_CODE:
                    metrics["return_ytd"] = item.get(
                        "ReturnYTD", item.get("returnYTD", item.get("YTDReturn", "N/A")))
                    metrics["return_1y"]  = item.get(
                        "Return1Y",  item.get("return1Y",  item.get("OneYearReturn", "N/A")))
                    break
    except Exception as exc:
        log.debug("ETFperformance fetch error: %s", exc)

    # Source 3: TWSE daily ETF info (TWT38U) → NAV fallback
    if metrics["nav"] == "N/A":
        try:
            resp = get(session, "https://www.twse.com.tw/fund/TWT38U",
                       params={"response": "json", "stockNo": ETF_CODE})
            if resp:
                rows = resp.json().get("data", [])
                if rows:
                    last = rows[-1]
                    # Columns: 日期, 受益人數, 淨資產總值, 每單位淨資產價值, ...
                    if len(last) >= 4:
                        metrics["nav"]   = last[3].replace(",", "") if last[3] != "--" else "N/A"
                    if len(last) >= 3 and metrics["scale"] == "N/A":
                        metrics["scale"] = last[2].replace(",", "") if last[2] != "--" else "N/A"
        except Exception as exc:
            log.debug("TWT38U fetch error: %s", exc)

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
            # Calculate shares change in 張 (1張 = 1000股)
            curr_shares = h.get("shares")
            prev_shares = prev[code].get("shares")
            if curr_shares is not None and prev_shares is not None:
                item["shares_change"] = curr_shares - prev_shares  # in 股
            (increased if delta > 0 else decreased).append(item)

    return {
        "added":     added,
        "removed":   removed,
        "increased": sorted(increased, key=lambda x: x["change"], reverse=True)[:10],
        "decreased": sorted(decreased, key=lambda x: x["change"])[:10],
    }


# ── Comparison ETF fetching ───────────────────────────────────────────────────

def fetch_all_etf_components() -> Optional[list[dict]]:
    """Fetch the full TWSE ETF component table (all ETFs in one call)."""
    session = make_session()
    url = "https://openapi.twse.com.tw/v1/ETFdividend/ETFcomponent"
    log.info("Fetching TWSE all-ETF components: %s", url)
    resp = get(session, url)
    if not resp:
        return None
    try:
        return resp.json()
    except Exception as exc:
        log.warning("All-ETF parse error: %s", exc)
        return None


def parse_etf_from_all(all_rows: list[dict], etf_code: str) -> Optional[list[dict]]:
    """Filter and parse holdings for a specific ETF from the all-ETF table."""
    rows = [
        x for x in all_rows
        if (x.get("ETFcode") or x.get("etfCode") or x.get("ETFCode") or "") == etf_code
    ]
    if not rows:
        return None
    holdings = []
    for x in rows:
        code   = str(x.get("Code",  x.get("code",  ""))).strip()
        name   = str(x.get("Name",  x.get("name",  ""))).strip()
        weight = float(x.get("Ratio", x.get("ratio", x.get("Percent", 0))))
        if code and name and weight > 0:
            holdings.append({
                "code": code, "name": name,
                "weight": round(weight, 4),
                "sector": infer_sector(code),
            })
    return sorted(holdings, key=lambda x: x["weight"], reverse=True) if holdings else None


def save_compare(etf_code: str, etf_name: str, holdings: list[dict]) -> None:
    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "code": etf_code, "name": etf_name,
        "date": date.today().isoformat(),
        "fetched_at": datetime.now().isoformat(),
        "holdings": holdings,
    }, ensure_ascii=False, indent=2)
    (COMPARE_DIR / f"{etf_code}.json").write_text(payload, encoding="utf-8")
    log.info("Saved compare/%s.json (%d holdings)", etf_code, len(holdings))


def load_compare(etf_code: str) -> Optional[list[dict]]:
    path = COMPARE_DIR / f"{etf_code}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("holdings", [])
        except Exception:
            pass
    return None


# ── Overlap computation ───────────────────────────────────────────────────────

def compute_overlap(
    main_holdings: list[dict],
    compare_map: dict[str, list[dict]],
) -> dict[str, dict]:
    """
    Returns stocks that appear in 00981A AND at least one comparison ETF.
    Key = stock code, value = { code, name, etfs: { etf_code: weight } }
    """
    main_set = {h["code"]: h for h in main_holdings}
    overlap: dict[str, dict] = {}

    for etf_code, holdings in compare_map.items():
        for h in holdings:
            if h["code"] not in main_set:
                continue
            if h["code"] not in overlap:
                overlap[h["code"]] = {
                    "code":  h["code"],
                    "name":  h["name"],
                    "etfs":  {"00981A": round(main_set[h["code"]]["weight"], 4)},
                }
            overlap[h["code"]]["etfs"][etf_code] = round(h["weight"], 4)

    return overlap


def load_previous_overlap() -> Optional[dict]:
    if OVERLAP_FILE.exists():
        try:
            return json.loads(OVERLAP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def detect_overlap_changes(
    current: dict[str, dict],
    previous: Optional[dict],
) -> dict:
    """Diff current vs previous overlap; returns new / removed / changed entries."""
    empty: dict = {"new": [], "removed": [], "changed": []}
    if not previous or "overlap" not in previous:
        return empty

    prev = previous["overlap"]
    changes: dict = {"new": [], "removed": [], "changed": []}

    for code, entry in current.items():
        if code not in prev:
            changes["new"].append(entry)
        else:
            prev_etfs = set(prev[code]["etfs"])
            curr_etfs = set(entry["etfs"])
            added_etfs   = curr_etfs - prev_etfs
            removed_etfs = prev_etfs - curr_etfs
            if added_etfs or removed_etfs:
                changes["changed"].append({
                    **entry,
                    "added_etfs":   sorted(added_etfs),
                    "removed_etfs": sorted(removed_etfs),
                })

    for code, entry in prev.items():
        if code not in current:
            changes["removed"].append(entry)

    return changes


def save_overlap(overlap: dict, changes: dict) -> None:
    payload = json.dumps({
        "date": date.today().isoformat(),
        "overlap": overlap,
        "changes": changes,
    }, ensure_ascii=False, indent=2)
    OVERLAP_FILE.write_text(payload, encoding="utf-8")
    total_changes = len(changes["new"]) + len(changes["removed"]) + len(changes["changed"])
    log.info("Overlap saved: %d overlapping stocks, %d changes", len(overlap), total_changes)


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

    # ── Fetch comparison ETFs and compute overlap ──────────────────────────
    log.info("Fetching comparison ETF components...")
    all_rows = fetch_all_etf_components()

    compare_map: dict[str, list[dict]] = {}
    for etf_code, etf_name in COMPARISON_ETFS.items():
        fetched = parse_etf_from_all(all_rows, etf_code) if all_rows else None
        if fetched:
            save_compare(etf_code, etf_name, fetched)
            compare_map[etf_code] = fetched
        else:
            cached = load_compare(etf_code)
            if cached:
                compare_map[etf_code] = cached
                log.info("Using cached data for %s", etf_code)
            else:
                log.warning("No data available for %s", etf_code)

    prev_overlap = load_previous_overlap()
    overlap      = compute_overlap(holdings, compare_map)
    ov_changes   = detect_overlap_changes(overlap, prev_overlap)
    save_overlap(overlap, ov_changes)

    log.info("Done.")


if __name__ == "__main__":
    main()
