#!/usr/bin/env python3
"""Update the TSMC dashboard data files.

This script is intentionally dependency-free so it can run in GitHub Actions.
It treats data/data.json as the canonical dataset and writes data/data.js as a
browser-friendly wrapper for static hosting and local file preview.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore


DEFAULT_TICKER = "2330.TW"
DEFAULT_TWSE_CODE = "2330"
TAIPEI_TZ = ZoneInfo("Asia/Taipei") if ZoneInfo else timezone.utc


def now_taipei() -> str:
    return datetime.now(TAIPEI_TZ).isoformat(timespec="seconds")


def http_get_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "tsmc-public-monitor/1.0 (+https://github.com/triathlan226/tsmc-public-monitor)"
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_js(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("window.TSMC_DASHBOARD_DATA = ")
        handle.write(payload)
        handle.write(";\n")


def normalize_key(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "item"


def stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(part.strip() for part in parts if part)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def csv_rows_from_url(url: str) -> list[dict[str, str]]:
    if not url:
        return []
    text = http_get_text(url)
    return [
        {normalize_key(key): (value or "").strip() for key, value in row.items()}
        for row in csv.DictReader(text.splitlines())
    ]


def add_source(data: dict[str, Any], name: str, url: str, source_type: str) -> str:
    if not url:
        return ""
    sources = data.setdefault("sources", [])
    for source in sources:
        if source.get("url") == url:
            return source.get("id", "")

    source_id = stable_id("SRC_SHEET", name, url)
    sources.append(
        {
            "id": source_id,
            "name": name or url,
            "type": source_type,
            "url": url,
            "retrieved_at": datetime.now(TAIPEI_TZ).date().isoformat(),
        }
    )
    return source_id


def upsert_source(data: dict[str, Any], source_id: str, name: str, url: str, source_type: str) -> str:
    sources = data.setdefault("sources", [])
    today = datetime.now(TAIPEI_TZ).date().isoformat()
    for source in sources:
        if source.get("id") == source_id:
            source.update({"name": name, "type": source_type, "url": url, "retrieved_at": today})
            return source_id

    sources.append(
        {
            "id": source_id,
            "name": name,
            "type": source_type,
            "url": url,
            "retrieved_at": today,
        }
    )
    return source_id


def parse_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def is_google_sheet_event(event: dict[str, Any], sheet_source_ids: set[str]) -> bool:
    event_id = str(event.get("id", ""))
    source_ids = event.get("source_ids", [])
    if event_id.startswith("SHEET_EVENT_"):
        return True
    if isinstance(source_ids, list) and sheet_source_ids.intersection(str(item) for item in source_ids):
        return True
    return False


def is_google_sheet_mops_event(event: dict[str, Any], sheet_source_ids: set[str]) -> bool:
    event_id = str(event.get("id", ""))
    source_ids = event.get("source_ids", [])
    if event_id.startswith("SHEET_MOPS_"):
        return True
    if isinstance(source_ids, list) and sheet_source_ids.intersection(str(item) for item in source_ids):
        return True
    return False


def merge_sheet_events(data: dict[str, Any], rows: list[dict[str, str]]) -> int:
    sheet_source_ids = {
        source.get("id", "")
        for source in data.setdefault("sources", [])
        if source.get("type") == "google_sheet_manual"
    }
    data["events"] = [
        event
        for event in data.setdefault("events", [])
        if not is_google_sheet_event(event, sheet_source_ids)
    ]
    data["sources"] = [
        source
        for source in data.setdefault("sources", [])
        if source.get("type") != "google_sheet_manual"
    ]

    events = data["events"]
    existing = {event.get("id"): event for event in events if event.get("id")}
    merged = 0

    for index, row in enumerate(rows, start=1):
        if row.get("enabled", "TRUE").strip().lower() in {"false", "0", "no", "n"}:
            continue

        title = row.get("title", "")
        date = row.get("date", "")
        if not title or not date:
            continue

        event_id = row.get("id") or stable_id("SHEET_EVENT", date, title, str(index))
        source_url = row.get("source_url", "")
        source_name = row.get("source_name", "") or "Google Sheet source"
        source_id = add_source(data, source_name, source_url, "google_sheet_manual") if source_url else ""
        source_ids = [source_id] if source_id else []

        event = {
            "id": event_id,
            "date": date,
            "category": row.get("category", "手動事件"),
            "title": title,
            "summary": row.get("summary", ""),
            "sentiment": row.get("sentiment", "neutral"),
            "importance": row.get("importance", "medium"),
            "dashboard_tag": row.get("dashboard_tag", row.get("tag", "Manual")),
            "analyst_take": row.get("analyst_take", row.get("take", "")),
        }
        if source_ids:
            event["source_ids"] = source_ids

        if event_id in existing:
            existing[event_id].update(event)
        else:
            events.insert(0, event)
        merged += 1

    events.sort(key=lambda item: item.get("date", ""), reverse=True)
    return merged


def merge_sheet_mops_events(data: dict[str, Any], rows: list[dict[str, str]]) -> int:
    sheet_source_ids = {
        source.get("id", "")
        for source in data.setdefault("sources", [])
        if source.get("type") == "google_sheet_mops"
    }
    data["events"] = [
        event
        for event in data.setdefault("events", [])
        if not is_google_sheet_mops_event(event, sheet_source_ids)
    ]
    data["sources"] = [
        source
        for source in data.setdefault("sources", [])
        if source.get("type") != "google_sheet_mops"
    ]

    events = data["events"]
    existing = {event.get("id"): event for event in events if event.get("id")}
    mops_items: list[dict[str, str]] = []
    merged = 0

    for index, row in enumerate(rows, start=1):
        if row.get("enabled", "TRUE").strip().lower() in {"false", "0", "no", "n"}:
            continue

        title = row.get("title", "")
        date = row.get("date", "")
        if not title or not date:
            continue

        source_url = row.get("source_url", "") or "https://mops.twse.com.tw/mops/web/t05st01"
        source_name = row.get("source_name", "") or "MOPS 人工登錄重大訊息"
        source_id = add_source(data, source_name, source_url, "google_sheet_mops")
        event_id = row.get("id") or stable_id("SHEET_MOPS", date, row.get("time", ""), title, str(index))

        item = {
            "date": date,
            "time": row.get("time", ""),
            "company": row.get("company", "2330 台積電"),
            "title": title,
            "summary": row.get("summary", ""),
            "source_id": source_id,
            "source_url": source_url,
        }
        mops_items.append(item)

        event = {
            "id": event_id,
            "date": date,
            "category": row.get("category", "MOPS 重大訊息"),
            "title": title,
            "summary": row.get("summary", ""),
            "sentiment": row.get("sentiment", "neutral"),
            "importance": row.get("importance", "medium"),
            "dashboard_tag": row.get("dashboard_tag", row.get("tag", "MOPS")),
            "analyst_take": row.get("analyst_take", row.get("take", "")),
            "source_ids": [source_id],
        }

        if event_id in existing:
            existing[event_id].update(event)
        else:
            events.insert(0, event)
        merged += 1

    if mops_items:
        mops_items.sort(key=lambda item: f"{item.get('date', '')} {item.get('time', '')}", reverse=True)
        data["mops_material_info"] = {
            "status": "ok",
            "stock_code": "2330",
            "items": mops_items[:12],
            "source_id": "SRC_MOPS_T05ST01",
            "source_url": "https://mops.twse.com.tw/mops/web/t05st01",
            "retrieved_at": now_taipei(),
            "entry_mode": "google_sheet_manual",
            "error": "",
        }

    events.sort(key=lambda item: item.get("date", ""), reverse=True)
    return merged


def replace_watchlist_from_sheet(data: dict[str, Any], rows: list[dict[str, str]]) -> int:
    items: list[dict[str, str]] = []
    for row in rows:
        if row.get("enabled", "TRUE").strip().lower() in {"false", "0", "no", "n"}:
            continue

        item = row.get("item", "")
        if not item:
            continue
        items.append(
            {
                "date": row.get("date", "rolling"),
                "item": item,
                "why_it_matters": row.get("why_it_matters", row.get("why", "")),
                "expected_source_id": row.get("expected_source_id", ""),
            }
        )

    if items:
        data["watchlist"] = items
    return len(items)


def yahoo_chart(symbol: str, chart_range: str, interval: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={chart_range}&interval={interval}"
    payload = json.loads(http_get_text(url))
    result = payload.get("chart", {}).get("result", [])
    if not result:
        raise RuntimeError(f"No Yahoo Finance chart result for {symbol}")

    chart = result[0]
    meta = chart.get("meta", {})
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    points = []
    for stamp, close, volume in zip(timestamps, closes, volumes):
        if close is None:
            continue
        points.append(
            {
                "datetime": datetime.fromtimestamp(stamp, TAIPEI_TZ).isoformat(timespec="seconds"),
                "close": round(float(close), 2),
                "volume": int(volume or 0),
            }
        )

    return {
        "symbol": symbol,
        "currency": meta.get("currency"),
        "exchange_name": meta.get("exchangeName"),
        "instrument_type": meta.get("instrumentType"),
        "regular_market_price": meta.get("regularMarketPrice"),
        "previous_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
        "range": chart_range,
        "interval": interval,
        "points": points,
        "source_url": f"https://finance.yahoo.com/quote/{encoded}",
        "retrieved_at": now_taipei(),
    }


def update_yahoo_market_snapshot(data: dict[str, Any], symbol: str) -> str:
    daily = yahoo_chart(symbol, "5d", "1d")
    intraday = yahoo_chart(symbol, "1d", "5m")
    market_snapshot = data.setdefault("market_snapshot", {})
    market_snapshot["yahoo_finance"] = {
        "as_of": now_taipei(),
        "ticker": symbol,
        "daily": daily,
        "intraday": intraday,
    }
    return symbol


def twse_t86_url(date_text: str) -> str:
    return (
        "https://www.twse.com.tw/rwd/zh/fund/T86?"
        f"date={date_text}&selectType=ALLBUT0999&response=json"
    )


def parse_twse_t86_stock_row(payload: dict[str, Any], stock_code: str, source_url: str) -> dict[str, Any] | None:
    fields = payload.get("fields", [])
    for row in payload.get("data", []):
        if not row or str(row[0]).strip() != stock_code:
            continue

        def by_field(name: str) -> int | None:
            try:
                return parse_int(row[fields.index(name)])
            except (ValueError, IndexError):
                return None

        return {
            "as_of": payload.get("date", ""),
            "title": payload.get("title", ""),
            "stock_code": stock_code,
            "stock_name": str(row[1]).strip() if len(row) > 1 else "",
            "unit": "shares",
            "foreign_investors": {
                "buy": by_field("外陸資買進股數(不含外資自營商)"),
                "sell": by_field("外陸資賣出股數(不含外資自營商)"),
                "net": by_field("外陸資買賣超股數(不含外資自營商)"),
            },
            "investment_trust": {
                "buy": by_field("投信買進股數"),
                "sell": by_field("投信賣出股數"),
                "net": by_field("投信買賣超股數"),
            },
            "dealers": {
                "net": by_field("自營商買賣超股數"),
            },
            "total_net": by_field("三大法人買賣超股數"),
            "source_id": "SRC_TWSE_T86",
            "source_url": source_url,
            "retrieved_at": now_taipei(),
        }
    return None


def institutional_sum(rows: list[dict[str, Any]], key: str, days: int) -> int | None:
    values: list[int] = []
    for row in rows[:days]:
        if key == "foreign":
            value = row.get("foreign_investors", {}).get("net")
        elif key == "trust":
            value = row.get("investment_trust", {}).get("net")
        elif key == "dealer":
            value = row.get("dealers", {}).get("net")
        else:
            value = row.get("total_net")
        if value is not None:
            values.append(int(value))
    return sum(values) if values else None


def foreign_streak(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows or rows[0].get("foreign_investors", {}).get("net") in (None, 0):
        return {"direction": "flat", "days": 0}
    first = int(rows[0]["foreign_investors"]["net"])
    sign = 1 if first > 0 else -1
    days = 0
    for row in rows:
        value = row.get("foreign_investors", {}).get("net")
        if value is None or int(value) * sign <= 0:
            break
        days += 1
    return {"direction": "buy" if sign > 0 else "sell", "days": days}


def update_twse_institutional_trading(data: dict[str, Any], stock_code: str) -> str:
    """Fetch latest TWSE three-major-institution trading data for one stock."""

    source_url = "https://www.twse.com.tw/zh/trading/foreign/t86.html"
    upsert_source(
        data,
        "SRC_TWSE_T86",
        "TWSE 三大法人買賣超日報",
        source_url,
        "public_stock_price",
    )

    today = datetime.now(TAIPEI_TZ).date()
    errors: list[str] = []
    history: list[dict[str, Any]] = []
    for offset in range(0, 45):
        target_date = today - timedelta(days=offset)
        date_text = target_date.strftime("%Y%m%d")
        url = twse_t86_url(date_text)
        try:
            payload = json.loads(http_get_text(url))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"{date_text}: {exc}")
            continue

        if payload.get("stat") != "OK" or not payload.get("data"):
            errors.append(f"{date_text}: {payload.get('stat', 'no data')}")
            continue

        parsed = parse_twse_t86_stock_row(payload, stock_code, url)
        if not parsed:
            errors.append(f"{date_text}: no row for {stock_code}")
            continue
        parsed["as_of"] = parsed.get("as_of") or date_text
        history.append(parsed)
        if len(history) >= 20:
            break

    if history:
        market_snapshot = data.setdefault("market_snapshot", {})
        market_snapshot["institutional_trading"] = history[0]
        market_snapshot["institutional_trading_history"] = history
        market_snapshot["institutional_trading_summary"] = {
            "source_id": "SRC_TWSE_T86",
            "latest_date": history[0].get("as_of"),
            "trading_days": len(history),
            "foreign_5d_net": institutional_sum(history, "foreign", 5),
            "foreign_20d_net": institutional_sum(history, "foreign", 20),
            "trust_5d_net": institutional_sum(history, "trust", 5),
            "trust_20d_net": institutional_sum(history, "trust", 20),
            "dealer_5d_net": institutional_sum(history, "dealer", 5),
            "dealer_20d_net": institutional_sum(history, "dealer", 20),
            "total_5d_net": institutional_sum(history, "total", 5),
            "total_20d_net": institutional_sum(history, "total", 20),
            "foreign_streak": foreign_streak(history),
            "retrieved_at": now_taipei(),
        }
        return str(history[0].get("as_of") or "")

    raise RuntimeError(f"No TWSE T86 row for {stock_code}; " + " | ".join(errors[:3]))


def tsmc_monthly_revenue_url(year: int) -> str:
    return f"https://investor.tsmc.com/english/monthly-revenue/{year}"


def parse_tsmc_monthly_revenue_page(text: str, year: int) -> list[dict[str, Any]]:
    month_map = {
        "Jan.": 1,
        "Feb.": 2,
        "Mar.": 3,
        "Apr.": 4,
        "May": 5,
        "Jun.": 6,
        "Jul.": 7,
        "Aug.": 8,
        "Sept.": 9,
        "Oct.": 10,
        "Nov.": 11,
        "Dec.": 12,
    }
    plain = strip_tags(text)
    rows: list[dict[str, Any]] = []
    for month_name, month_number in month_map.items():
        pattern = rf"\b{re.escape(month_name)}\s+([0-9,]+)\s+(-?[0-9]+(?:\.[0-9]+)?)%"
        match = re.search(pattern, plain)
        if not match:
            continue
        revenue_million = parse_int(match.group(1))
        yoy = parse_float(match.group(2))
        if revenue_million is None:
            continue
        rows.append(
            {
                "month": f"{year}-{month_number:02d}",
                "revenue": round(revenue_million / 1000, 3),
                "revenue_twd_million": revenue_million,
                "yoy_percent": yoy,
                "source_id": "SRC_TSMC_MONTHLY_REVENUE",
            }
        )
    return rows


def update_tsmc_monthly_revenue_history(data: dict[str, Any], months: int = 24) -> int:
    upsert_source(
        data,
        "SRC_TSMC_MONTHLY_REVENUE",
        "TSMC Investor Relations - Monthly Revenue",
        "https://investor.tsmc.com/english/monthly-revenue",
        "official",
    )
    current_year = datetime.now(TAIPEI_TZ).year
    rows: list[dict[str, Any]] = []
    for year in range(current_year, current_year - 4, -1):
        text = http_get_text(tsmc_monthly_revenue_url(year))
        rows.extend(parse_tsmc_monthly_revenue_page(text, year))

    rows = sorted(rows, key=lambda row: row["month"])
    for index, row in enumerate(rows):
        if index > 0 and row.get("revenue") is not None and rows[index - 1].get("revenue"):
            previous = float(rows[index - 1]["revenue"])
            row["mom_percent"] = round((float(row["revenue"]) / previous - 1) * 100, 1)

    latest_rows = rows[-months:]
    if not latest_rows:
        raise RuntimeError("No TSMC monthly revenue rows parsed")

    revenue = data.setdefault("financials", {}).setdefault("monthly_revenue", {})
    revenue["monthly_series_twd_billion"] = latest_rows
    revenue["history_months"] = len(latest_rows)
    revenue["latest_month"] = latest_rows[-1]["month"]
    revenue["revenue_twd_billion"] = latest_rows[-1]["revenue"]
    revenue["yoy_percent"] = latest_rows[-1].get("yoy_percent")
    revenue["mom_percent"] = latest_rows[-1].get("mom_percent")
    revenue["source_ids"] = list(dict.fromkeys([*revenue.get("source_ids", []), "SRC_TSMC_MONTHLY_REVENUE"]))
    revenue["retrieved_at"] = now_taipei()
    return len(latest_rows)


def strip_tags(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_mops_material_rows(text: str, limit: int = 8) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)
        clean = [strip_tags(cell) for cell in cells]
        if len(clean) < 4 or not any("2330" in cell for cell in clean):
            continue
        if any("公司代號" in cell or "主旨" in cell for cell in clean):
            continue
        rows.append(
            {
                "date": clean[0],
                "time": clean[1] if len(clean) > 1 else "",
                "company": " ".join(clean[2:4]) if len(clean) > 3 else "2330 台積電",
                "title": clean[4] if len(clean) > 4 else clean[-1],
                "raw": " | ".join(clean),
                "source_id": "SRC_MOPS_T05ST01",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def update_mops_material_info(data: dict[str, Any], stock_code: str) -> str:
    """Best-effort MOPS material information query.

    MOPS may reject automated form requests. The dashboard keeps the official
    source and fetch status so users can see whether the public source is live.
    """

    source_url = "https://mops.twse.com.tw/mops/web/t05st01"
    upsert_source(
        data,
        "SRC_MOPS_T05ST01",
        "MOPS 歷史重大訊息",
        source_url,
        "official_mops",
    )

    now = datetime.now(TAIPEI_TZ)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; tsmc-public-monitor/1.0)",
        "Referer": source_url,
    }
    try:
        opener.open(urllib.request.Request("https://mops.twse.com.tw/mops/web/index", headers=headers), timeout=20)
        opener.open(urllib.request.Request(source_url, headers=headers), timeout=20)
    except (urllib.error.URLError, TimeoutError):
        pass

    all_rows: list[dict[str, str]] = []
    errors: list[str] = []
    for offset in range(0, 3):
        month_date = (now.replace(day=1) - timedelta(days=offset * 31)).replace(day=1)
        roc_year = month_date.year - 1911
        body = urllib.parse.urlencode(
            {
                "encodeURIComponent": "1",
                "step": "1",
                "firstin": "1",
                "off": "1",
                "keyword4": "",
                "code1": "",
                "TYPEK2": "",
                "checkbtn": "",
                "queryName": "co_id",
                "inpuType": "co_id",
                "TYPEK": "all",
                "co_id": stock_code,
                "year": str(roc_year),
                "month": f"{month_date.month:02d}",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://mops.twse.com.tw/mops/web/ajax_t05st01",
            data=body,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            text = opener.open(request, timeout=20).read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            errors.append(f"{roc_year}/{month_date.month:02d}: {exc}")
            continue

        if "SECURITY REASONS" in text or "安全性考量" in text:
            errors.append(f"{roc_year}/{month_date.month:02d}: MOPS security block")
            continue

        all_rows.extend(parse_mops_material_rows(text))
        if all_rows:
            break

    data["mops_material_info"] = {
        "status": "ok" if all_rows else "unavailable",
        "stock_code": stock_code,
        "items": all_rows[:8],
        "source_id": "SRC_MOPS_T05ST01",
        "source_url": source_url,
        "retrieved_at": now_taipei(),
        "error": "" if all_rows else " | ".join(errors[:3]) or "No MOPS rows parsed",
    }
    if not all_rows:
        raise RuntimeError(data["mops_material_info"]["error"])
    return str(len(all_rows))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/data.json")
    parser.add_argument("--output-json", default="data/data.json")
    parser.add_argument("--output-js", default="data/data.js")
    parser.add_argument("--no-fetch", action="store_true", help="Skip network fetches and only rewrite output files.")
    parser.add_argument("--ticker", default=os.getenv("YAHOO_TICKER", DEFAULT_TICKER))
    parser.add_argument("--twse-code", default=os.getenv("TWSE_STOCK_CODE", DEFAULT_TWSE_CODE))
    parser.add_argument("--sheet-events-csv-url", default=os.getenv("GOOGLE_SHEET_EVENTS_CSV_URL", ""))
    parser.add_argument("--sheet-watchlist-csv-url", default=os.getenv("GOOGLE_SHEET_WATCHLIST_CSV_URL", ""))
    parser.add_argument("--sheet-mops-csv-url", default=os.getenv("GOOGLE_SHEET_MOPS_CSV_URL", ""))
    args = parser.parse_args()

    input_path = Path(args.input)
    data = load_json(input_path)
    data.setdefault("metadata", {})
    data["metadata"]["generated_at"] = now_taipei()
    data["metadata"]["data_pipeline"] = {
        "canonical_json": args.output_json,
        "browser_data_js": args.output_js,
        "google_sheet_events_configured": bool(args.sheet_events_csv_url),
        "google_sheet_watchlist_configured": bool(args.sheet_watchlist_csv_url),
        "google_sheet_mops_configured": bool(args.sheet_mops_csv_url),
        "yahoo_ticker": args.ticker,
        "twse_stock_code": args.twse_code,
    }

    run_log: list[str] = []
    if not args.no_fetch:
        try:
            update_yahoo_market_snapshot(data, args.ticker)
            run_log.append(f"Yahoo Finance updated for {args.ticker}")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            run_log.append(f"Yahoo Finance skipped: {exc}")

        try:
            update_twse_institutional_trading(data, args.twse_code)
            run_log.append(f"TWSE T86 institutional trading history updated for {args.twse_code}")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            run_log.append(f"TWSE T86 skipped: {exc}")

        try:
            month_count = update_tsmc_monthly_revenue_history(data)
            run_log.append(f"TSMC monthly revenue history rows: {month_count}")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            run_log.append(f"TSMC monthly revenue history skipped: {exc}")

        try:
            count = update_mops_material_info(data, args.twse_code)
            run_log.append(f"MOPS material info rows: {count}")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            run_log.append(f"MOPS material info skipped: {exc}")

        try:
            event_count = merge_sheet_events(data, csv_rows_from_url(args.sheet_events_csv_url))
            run_log.append(f"Google Sheet events merged: {event_count}")
        except (urllib.error.URLError, TimeoutError, csv.Error) as exc:
            run_log.append(f"Google Sheet events skipped: {exc}")

        try:
            mops_count = merge_sheet_mops_events(data, csv_rows_from_url(args.sheet_mops_csv_url))
            run_log.append(f"Google Sheet MOPS events merged: {mops_count}")
        except (urllib.error.URLError, TimeoutError, csv.Error) as exc:
            run_log.append(f"Google Sheet MOPS events skipped: {exc}")

        try:
            watch_count = replace_watchlist_from_sheet(data, csv_rows_from_url(args.sheet_watchlist_csv_url))
            run_log.append(f"Google Sheet watchlist rows: {watch_count}")
        except (urllib.error.URLError, TimeoutError, csv.Error) as exc:
            run_log.append(f"Google Sheet watchlist skipped: {exc}")
    else:
        run_log.append("Network fetches skipped by --no-fetch")

    data["metadata"]["last_pipeline_run"] = {
        "ran_at": now_taipei(),
        "log": run_log,
    }

    write_json(Path(args.output_json), data)
    write_js(Path(args.output_js), data)
    print("\n".join(run_log), file=sys.stderr)
    print(f"Wrote {args.output_json} and {args.output_js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
