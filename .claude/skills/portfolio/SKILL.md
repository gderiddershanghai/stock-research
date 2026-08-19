---
name: portfolio
description: Analyze a stock portfolio pasted in chat plus a new-cash amount, and suggest additional stocks via the diversification screen. Use when the user provides holdings (tickers with shares or dollar values) and an amount to invest, or says "portfolio", "suggest stocks", "what should he buy".
---

# Portfolio diversification screen

Flow when the user pastes holdings and an amount in chat:

1. Parse the holdings into `private/holdings.json` (gitignored — never commit,
   never echo the file into anything under `docs/`):

   ```json
   {"cash": 10000,
    "holdings": [{"ticker": "AAPL", "shares": 12},
                 {"ticker": "VOO", "value": 8000}]}
   ```

   Each holding needs `ticker` plus either `shares` or `value` (dollars).
   US-listed tickers only (Yahoo Finance).

2. Run `.venv/bin/python scripts/portfolio.py` (takes ~1–2 min; it batch-downloads
   a year of prices for candidates in the underweight sectors).

3. Outputs:
   - `private/report.md` — full analysis: holdings, weights, sector gaps,
     concentration, correlated pairs, suggestions with dollar amounts. Private.
   - `docs/portfolio.html` — suggestions only (no holdings, no dollars), unlisted:
     not linked from the index. Committing/pushing `docs/` publishes it on GitHub
     Pages — ask before pushing.

4. Report back with the suggested split and the key gap stats from the private
   report. Stats only — no "should buy" language, and note it is not advice.

Privacy rules: holdings and dollar amounts stay in `private/`. The public page
may name underweight sectors and correlation numbers but never holdings,
position sizes, portfolio value, or the cash amount.
