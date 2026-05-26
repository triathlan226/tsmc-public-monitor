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
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore


DEFAULT_TICKER = "2330.TW"
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


def is_google_sheet_event(event: dict[str, Any], sheet_source_ids: set[str]) -> bool:
    event_id = str(event.get("id", ""))
    source_ids = event.get("source_ids", [])
    if event_id.startswith("SHEET_EVENT_"):
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/data.json")
    parser.add_argument("--output-json", default="data/data.json")
    parser.add_argument("--output-js", default="data/data.js")
    parser.add_argument("--no-fetch", action="store_true", help="Skip network fetches and only rewrite output files.")
    parser.add_argument("--ticker", default=os.getenv("YAHOO_TICKER", DEFAULT_TICKER))
    parser.add_argument("--sheet-events-csv-url", default=os.getenv("GOOGLE_SHEET_EVENTS_CSV_URL", ""))
    parser.add_argument("--sheet-watchlist-csv-url", default=os.getenv("GOOGLE_SHEET_WATCHLIST_CSV_URL", ""))
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
        "yahoo_ticker": args.ticker,
    }

    run_log: list[str] = []
    if not args.no_fetch:
        try:
            update_yahoo_market_snapshot(data, args.ticker)
            run_log.append(f"Yahoo Finance updated for {args.ticker}")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            run_log.append(f"Yahoo Finance skipped: {exc}")

        try:
            event_count = merge_sheet_events(data, csv_rows_from_url(args.sheet_events_csv_url))
            run_log.append(f"Google Sheet events merged: {event_count}")
        except (urllib.error.URLError, TimeoutError, csv.Error) as exc:
            run_log.append(f"Google Sheet events skipped: {exc}")

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
