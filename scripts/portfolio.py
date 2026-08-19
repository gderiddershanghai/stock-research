#!/usr/bin/env python3
"""Portfolio diversification screen.

Usage: python scripts/portfolio.py [private/holdings.json]

Reads holdings + new-cash amount from a JSON file kept in the gitignored
private/ directory:

    {"cash": 10000,
     "holdings": [{"ticker": "AAPL", "shares": 12},
                  {"ticker": "VOO", "value": 8000}]}

Computes current weights, sector exposure vs approximate S&P 500 sector
weights, concentration, and pairwise correlations (1y daily returns).
Screens S&P 500 constituents in the most underweight sectors for low
correlation with the current portfolio and suggests a split of the new cash.

Writes private/report.md (full detail, incl. holdings and dollars — never
committed) and docs/portfolio.html (suggestions only, no holdings, no
dollar amounts; unlisted — not added to the index).
"""
import html
import io
import json
import math
import sys
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "private"
DOCS = ROOT / "docs"

ACCENT = "#0072B2"
INK = "#24292f"
MUTED = "#6a737d"

N_SUGGEST = 5
MAX_PER_SECTOR = 2
N_GAP_SECTORS = 4          # how many underweight sectors to screen
MIN_DOLLAR_VOL = 10e6      # median daily dollar volume floor for candidates
MIN_DAYS = 200             # candidate must have this much 1y history

# Approximate S&P 500 GICS sector weights (early 2026). Used only to rank
# which sectors the portfolio is light on, so precision is not critical.
BENCH = {
    "Information Technology": 33.0, "Financials": 14.0,
    "Consumer Discretionary": 11.0, "Communication Services": 10.0,
    "Health Care": 10.0, "Industrials": 8.0, "Consumer Staples": 5.0,
    "Energy": 3.0, "Utilities": 2.5, "Real Estate": 2.0, "Materials": 1.5,
}

# yfinance sector names -> GICS names used by the S&P constituent list
YF_TO_GICS = {
    "Technology": "Information Technology",
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Communication Services": "Communication Services",
    "Healthcare": "Health Care",
    "Industrials": "Industrials",
    "Consumer Defensive": "Consumer Staples",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Basic Materials": "Materials",
}


def load_holdings(path):
    spec = json.loads(Path(path).read_text())
    cash = float(spec["cash"])
    rows = spec["holdings"]
    if not rows or cash <= 0:
        sys.exit("Need at least one holding and a positive cash amount")
    return cash, rows


def closes(tickers, period="1y"):
    px = yf.download(tickers, period=period, auto_adjust=True, progress=False)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(tickers[0])
    return px.dropna(how="all")


def holding_details(rows):
    """Resolve each holding to value, weight, sector, name."""
    out = []
    for r in rows:
        tk = r["ticker"].upper()
        t = yf.Ticker(tk)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass
        price = info.get("regularMarketPrice") or t.fast_info.get("lastPrice")
        if price is None:
            sys.exit(f"No price for {tk}")
        value = float(r["value"]) if "value" in r else float(r["shares"]) * price
        qt = (info.get("quoteType") or "").upper()
        sector = YF_TO_GICS.get(info.get("sector"))
        if sector is None:
            sector = "Fund (diversified)" if qt in ("ETF", "MUTUALFUND") else "Other"
        out.append({"ticker": tk, "value": value, "sector": sector,
                    "name": info.get("shortName") or tk})
    total = sum(h["value"] for h in out)
    for h in out:
        h["weight"] = h["value"] / total * 100
    return out, total


def sp500_constituents():
    html = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    df = pd.read_html(io.StringIO(html))[0]
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
    return df[["Symbol", "Security", "GICS Sector"]]


def portfolio_returns(holdings):
    px = closes([h["ticker"] for h in holdings])
    rets = px.pct_change(fill_method=None).dropna(how="all")
    w = pd.Series({h["ticker"]: h["weight"] / 100 for h in holdings})
    w = w.reindex(rets.columns).fillna(0)
    return (rets.fillna(0) @ w), rets


def sector_gaps(holdings):
    held = {}
    for h in holdings:
        held[h["sector"]] = held.get(h["sector"], 0) + h["weight"]
    gaps = [(s, bench, held.get(s, 0.0), held.get(s, 0.0) - bench)
            for s, bench in BENCH.items()]
    return sorted(gaps, key=lambda g: g[3]), held


def screen(gaps, holdings, port_ret):
    sp = sp500_constituents()
    held_tks = {h["ticker"] for h in holdings}
    target_sectors = [s for s, *_ in gaps[:N_GAP_SECTORS]]
    pool = sp[sp["GICS Sector"].isin(target_sectors) & ~sp["Symbol"].isin(held_tks)]
    tks = pool["Symbol"].tolist()
    print(f"Screening {len(tks)} candidates in: {', '.join(target_sectors)}")

    raw = yf.download(tks, period="1y", auto_adjust=True, progress=False)
    px, vol = raw["Close"], raw["Volume"]
    rets = px.pct_change(fill_method=None)
    meta = pool.set_index("Symbol")
    gap_size = {s: -g for s, _, _, g in gaps[:N_GAP_SECTORS]}

    cands = []
    for tk in tks:
        if tk not in px.columns:
            continue
        p = px[tk].dropna()
        if len(p) < MIN_DAYS:
            continue
        dvol = (p * vol[tk].reindex(p.index)).median()
        if pd.isna(dvol) or dvol < MIN_DOLLAR_VOL:
            continue
        r = rets[tk].dropna()
        joined = pd.concat([r, port_ret], axis=1, join="inner").dropna()
        if len(joined) < MIN_DAYS:
            continue
        corr = joined.iloc[:, 0].corr(joined.iloc[:, 1])
        sector = meta.loc[tk, "GICS Sector"]
        # low correlation is the point; mild penalty for extreme volatility
        vol_ann = r.std() * math.sqrt(252) * 100
        score = (1 - corr) * gap_size[sector] - max(0, vol_ann - 45) * 0.05
        cands.append({
            "ticker": tk, "name": meta.loc[tk, "Security"], "sector": sector,
            "corr": corr, "vol": vol_ann,
            "ret1y": (p.iloc[-1] / p.iloc[0] - 1) * 100, "score": score,
        })

    cands.sort(key=lambda c: c["score"], reverse=True)
    picks, per_sector = [], {}
    for c in cands:
        if per_sector.get(c["sector"], 0) >= MAX_PER_SECTOR:
            continue
        picks.append(c)
        per_sector[c["sector"]] = per_sector.get(c["sector"], 0) + 1
        if len(picks) == N_SUGGEST:
            break
    return picks, gap_size


def allocate(picks, gap_size, cash):
    by_sector = {}
    for p in picks:
        by_sector.setdefault(p["sector"], []).append(p)
    total_gap = sum(gap_size[s] for s in by_sector)
    for s, ps in by_sector.items():
        for p in ps:
            p["share"] = gap_size[s] / total_gap / len(ps) * 100
            p["dollars"] = round(cash * p["share"] / 100 / 50) * 50
    # rounding drift goes to the top pick
    drift = cash - sum(p["dollars"] for p in picks)
    picks[0]["dollars"] += drift
    for p in picks:
        p["share"] = p["dollars"] / cash * 100
    picks.sort(key=lambda p: -p["dollars"])


def concentration(holdings):
    ws = sorted((h["weight"] for h in holdings), reverse=True)
    hhi = sum((w / 100) ** 2 for w in ws)
    return ws[0], sum(ws[:3]), 1 / hhi


def write_private_report(holdings, total, cash, gaps, corr_mat, picks, today):
    top1, top3, eff_n = concentration(holdings)
    lines = [f"# Portfolio screen — {today}", "",
             f"Portfolio value ${total:,.0f} · new cash ${cash:,.0f}", "",
             "## Holdings", "",
             "| Ticker | Name | Sector | Value | Weight |", "|---|---|---|---|---|"]
    for h in sorted(holdings, key=lambda h: -h["weight"]):
        lines.append(f"| {h['ticker']} | {h['name']} | {h['sector']} "
                     f"| ${h['value']:,.0f} | {h['weight']:.1f}% |")
    lines += ["", f"Top holding {top1:.1f}% · top 3 {top3:.1f}% "
              f"· effective number of positions {eff_n:.1f}", "",
              "## Sector exposure vs S&P 500 (approx weights)", "",
              "| Sector | Portfolio | S&P 500 | Gap |", "|---|---|---|---|"]
    for s, bench, have, gap in gaps:
        lines.append(f"| {s} | {have:.1f}% | {bench:.1f}% | {gap:+.1f}% |")
    hi = [(a, b, corr_mat.loc[a, b]) for a in corr_mat.columns for b in corr_mat.columns
          if a < b and corr_mat.loc[a, b] > 0.7]
    if hi:
        lines += ["", "## Highly correlated pairs (1y daily returns, r > 0.7)", ""]
        lines += [f"- {a} / {b}: {r:.2f}" for a, b, r in sorted(hi, key=lambda x: -x[2])]
    lines += ["", "## Suggested additions", "",
              "| Ticker | Name | Sector | Corr w/ portfolio | 1y vol | 1y return | Amount |",
              "|---|---|---|---|---|---|---|"]
    for p in picks:
        lines.append(f"| {p['ticker']} | {p['name']} | {p['sector']} | {p['corr']:.2f} "
                     f"| {p['vol']:.0f}% | {p['ret1y']:+.0f}% | ${p['dollars']:,.0f} |")
    lines += ["", "Screen: S&P 500 names in the most underweight sectors, ranked by low "
              "correlation with the current portfolio (liquidity- and volatility-filtered). "
              "Informational only, not advice.", ""]
    (PRIVATE / "report.md").write_text("\n".join(lines))


def write_public_page(picks, gaps, today):
    max_share = max(p["share"] for p in picks)
    rows = "".join(
        f'<tr><td class="tk">{p["ticker"]}</td><td>{html.escape(p["name"])}</td><td>{p["sector"]}</td>'
        f'<td class="num">{p["corr"]:.2f}</td><td class="num">{p["vol"]:.0f}%</td>'
        f'<td class="bar"><div style="width:{p["share"] / max_share * 100:.0f}%"></div>'
        f'<span>{p["share"]:.0f}%</span></td></tr>'
        for p in picks)
    under = ", ".join(s for s, *_ in gaps[:N_GAP_SECTORS])
    top = picks[0]
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio additions</title>
<style>
  body {{ background:#fff; color:{INK}; font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
         margin:0; padding:24px 16px 48px; }}
  main {{ max-width:760px; margin:0 auto; }}
  h1 {{ font-size:24px; margin-bottom:4px; }}
  .sub {{ color:{MUTED}; font-size:14px; margin-top:0; }}
  table {{ border-collapse:collapse; width:100%; margin-top:20px; font-variant-numeric:tabular-nums; }}
  th {{ text-align:left; color:{MUTED}; font-size:13px; font-weight:600; padding:6px 10px 6px 0; }}
  td {{ padding:8px 10px 8px 0; border-top:1px solid #eef1f4; font-size:15px; }}
  .tk {{ font-weight:700; }} .num {{ text-align:right; }}
  th.r {{ text-align:right; }}
  .bar {{ min-width:130px; }} .bar div {{ display:inline-block; height:12px; background:{ACCENT};
         border-radius:2px; vertical-align:middle; }}
  .bar span {{ margin-left:6px; color:{INK}; font-size:14px; }}
  footer {{ color:{MUTED}; font-size:13px; margin-top:28px; }}
</style>
<main>
<h1>{top["ticker"]} leads a {len(picks)}-stock diversification screen</h1>
<p class="sub">S&amp;P 500 names in the portfolio's most underweight sectors ({under}),
ranked by low correlation with the current portfolio over the last year.
Holdings and amounts are not shown. Updated {today}.</p>
<table>
<tr><th>Ticker</th><th>Name</th><th>Sector</th><th class="r">Corr w/ portfolio</th>
<th class="r">1y volatility</th><th>Share of new money</th></tr>
{rows}
</table>
<footer>Screen output from public price data (Yahoo Finance). Informational only — not
financial advice.</footer>
</main></html>"""
    (DOCS / "portfolio.html").write_text(page)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else PRIVATE / "holdings.json"
    cash, rows = load_holdings(path)
    holdings, total = holding_details(rows)
    port_ret, rets = portfolio_returns(holdings)
    gaps, _ = sector_gaps(holdings)
    picks, gap_size = screen(gaps, holdings, port_ret)
    if not picks:
        sys.exit("No candidates survived the screen")
    allocate(picks, gap_size, cash)

    today = pd.Timestamp.today().strftime("%b %-d, %Y")
    write_private_report(holdings, total, cash, gaps, rets.corr(), picks, today)
    write_public_page(picks, gaps, today)
    print(f"Wrote private/report.md and docs/portfolio.html")
    for p in picks:
        print(f"  {p['ticker']:<6} {p['sector']:<24} ${p['dollars']:>7,.0f}  "
              f"corr {p['corr']:.2f}")


if __name__ == "__main__":
    main()
