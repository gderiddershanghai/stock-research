#!/usr/bin/env python3
"""Earnings-reaction research page generator.

Usage: python scripts/research.py TICKER

Fetches the last up-to-8 completed quarterly earnings events and daily prices
(Yahoo Finance via yfinance), rebases each event window to the close before the
announcement, and writes an interactive d3 page (data embedded inline) to
docs/<ticker>.html, updating docs/index.html and docs/data/registry.json.
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
REGISTRY = DOCS / "data" / "registry.json"
TEMPLATE = ROOT / "scripts" / "template.html"

PRE_DAYS = 20   # trading days shown before the report
POST_DAYS = 10  # trading days shown after the report
MAX_EVENTS = 8

ACCENT = "#0072B2"
INK = "#24292f"
MUTED = "#6a737d"


def fetch(ticker):
    t = yf.Ticker(ticker)
    ed = t.get_earnings_dates(limit=24)
    if ed is None or ed.empty:
        sys.exit(f"No earnings dates found for {ticker}")
    ed = ed.sort_index()
    now = pd.Timestamp.now(tz=ed.index.tz)
    past = ed[(ed.index < now) & ed["Reported EPS"].notna()].iloc[-MAX_EVENTS:]
    future = ed[ed.index > now]
    next_event = future.index[0] if not future.empty else None
    next_eps = None
    if next_event is not None and pd.notna(future["EPS Estimate"].iloc[0]):
        next_eps = float(future["EPS Estimate"].iloc[0])

    start = (past.index[0] - timedelta(days=60)).date()
    hist = t.history(start=str(start), auto_adjust=True)
    if hist.empty:
        sys.exit(f"No price history for {ticker}")
    hist.index = hist.index.tz_localize(None).normalize()

    name = ticker
    try:
        name = t.history_metadata.get("shortName") or t.info.get("shortName") or ticker
    except Exception:
        pass
    return past, next_event, next_eps, hist, name


def reaction_day_index(hist, announced):
    """Index of the first trading session whose close reflects the news."""
    d = pd.Timestamp(announced.date())
    if announced.hour >= 12:  # after-market-close report -> next session
        d += timedelta(days=1)
    pos = hist.index.searchsorted(d)
    return pos if pos < len(hist) else None


def build_events(hist, past):
    closes = hist["Close"]
    events = []
    for ann, row in past.iterrows():
        est = None if pd.isna(row["EPS Estimate"]) else float(row["EPS Estimate"])
        act = None if pd.isna(row["Reported EPS"]) else float(row["Reported EPS"])
        surprise = (act - est) / abs(est) * 100 if est not in (None, 0) and act is not None else None
        i0 = reaction_day_index(hist, ann)
        if i0 is None or i0 - PRE_DAYS - 1 < 0:
            continue
        ref = closes.iloc[i0 - 1]  # close the day before the report
        pts = []
        for off in range(-PRE_DAYS, POST_DAYS + 1):
            j = i0 + off
            if 0 <= j < len(closes):
                pts.append((off, (closes.iloc[j] / ref - 1) * 100))
        day0 = dict(pts).get(0)
        events.append({
            "announced": ann,
            "label": ann.strftime("%b '%y"),
            "points": pts,
            "reaction": day0,
            "eps_est": est, "eps_act": act, "surprise": surprise,
            "runup_20": (ref / closes.iloc[i0 - 1 - PRE_DAYS] - 1) * 100,
            "runup_10": (ref / closes.iloc[i0 - 1 - 10] - 1) * 100,
            "runup_5": (ref / closes.iloc[i0 - 1 - 5] - 1) * 100,
            "runup_1": (ref / closes.iloc[i0 - 2] - 1) * 100,
            "drift_1": (closes.iloc[i0 + 1] / closes.iloc[i0] - 1) * 100 if i0 + 1 < len(closes) and day0 is not None else None,
            "drift_5": (closes.iloc[i0 + 5] / closes.iloc[i0] - 1) * 100 if i0 + 5 < len(closes) and day0 is not None else None,
            "drift_10": (closes.iloc[i0 + 10] / closes.iloc[i0] - 1) * 100 if i0 + 10 < len(closes) and day0 is not None else None,
        })
    return events


def median(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def fmt(v):
    return "–" if v is None else f"{v:+.1f}%".replace("-", "−")


def headline(ticker, events):
    downs = [e for e in events if e["reaction"] is not None and e["reaction"] < 0]
    n = len([e for e in events if e["reaction"] is not None])
    med = median([e["reaction"] for e in events])
    if len(downs) * 2 > n:
        return f"{ticker} has fallen on report day in {len(downs)} of the last {n} quarters (median move {fmt(med)})"
    return f"{ticker} has risen on report day in {n - len(downs)} of the last {n} quarters (median move {fmt(med)})"


def page_data(ticker, name, events, next_event, next_eps, hist, today):
    med = []
    for off in range(-PRE_DAYS, POST_DAYS + 1):
        m = median([dict(e["points"]).get(off) for e in events])
        if m is not None:
            med.append([off, round(m, 3)])
    return {
        "ticker": ticker, "name": name, "updated": today,
        "lastClose": round(float(hist["Close"].iloc[-1]), 2),
        "lastDate": hist.index[-1].strftime("%b %-d, %Y"),
        "nextReport": next_event.strftime("%A, %B %-d, %Y") if next_event is not None else None,
        "nextWhen": ("before the market opens" if next_event.hour < 12 else "after the market closes")
                    if next_event is not None else None,
        "nextEps": round(next_eps, 2) if next_eps is not None else None,
        "median": med,
        "events": [{
            "label": e["label"],
            "reaction": round(e["reaction"], 2) if e["reaction"] is not None else None,
            "epsEst": e["eps_est"], "epsAct": e["eps_act"],
            "surprise": round(e["surprise"], 1) if e["surprise"] is not None else None,
            "points": [[o, round(v, 3)] for o, v in e["points"]],
            "runup20": round(e["runup_20"], 2), "runup10": round(e["runup_10"], 2),
            "runup5": round(e["runup_5"], 2), "runup1": round(e["runup_1"], 2),
            "drift1": round(e["drift_1"], 2) if e["drift_1"] is not None else None,
            "drift5": round(e["drift_5"], 2) if e["drift_5"] is not None else None,
            "drift10": round(e["drift_10"], 2) if e["drift_10"] is not None else None,
        } for e in events],
    }


def render_index(registry, today):
    rows = "".join(
        f'<li><a href="{t.lower()}.html"><span class="tk">{t}</span> <span class="nm">{r["name"]}</span></a>'
        f'<div class="meta">{r["headline"]}</div>'
        f'<div class="meta">Next report: {r["next"] or "not scheduled"} · updated {r["updated"]}</div></li>'
        for t, r in sorted(registry.items()))
    return f"""<title>Stock research</title>
<style>
  body {{ background:#fff; color:{INK}; font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
         margin:0; padding:24px 16px 48px; }}
  main {{ max-width:760px; margin:0 auto; }}
  h1 {{ font-size:26px; }} a {{ color:{ACCENT}; text-decoration:none; }} a:hover {{ text-decoration:underline; }}
  ul {{ list-style:none; padding:0; }} li {{ padding:14px 0; border-bottom:1px solid #eef1f4; }}
  .tk {{ font-weight:700; font-size:18px; }} .nm {{ color:{MUTED}; }}
  .meta {{ color:{MUTED}; font-size:14px; }}
</style>
<main>
<h1>Stock research — earnings history</h1>
<p>How each stock has behaved around its quarterly results announcements.</p>
<ul>{rows}</ul>
<footer style="color:{MUTED};font-size:13px">Updated {today}. Informational only.</footer>
</main>"""


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: research.py TICKER")
    ticker = sys.argv[1].upper()
    past, next_event, next_eps, hist, name = fetch(ticker)
    events = build_events(hist, past)
    if not events:
        sys.exit("Not enough price history around earnings dates")

    today = pd.Timestamp.today().strftime("%b %-d, %Y")
    data = page_data(ticker, name, events, next_event, next_eps, hist, today)
    page = (TEMPLATE.read_text()
            .replace("__TICKER__", ticker)
            .replace("__DATA__", json.dumps(data)))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data").mkdir(exist_ok=True)
    (DOCS / f"{ticker.lower()}.html").write_text(page)

    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
    registry[ticker] = {
        "name": name, "updated": today,
        "headline": headline(ticker, events),
        "next": next_event.strftime("%b %-d, %Y") if next_event is not None else None,
    }
    REGISTRY.write_text(json.dumps(registry, indent=2))
    (DOCS / "index.html").write_text(
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">{render_index(registry, today)}</html>")
    print(f"Wrote docs/{ticker.lower()}.html ({len(events)} events)")


if __name__ == "__main__":
    main()
