# Stock research — earnings history

Generates an interactive page per ticker showing how the stock behaved around its
last up-to-8 quarterly earnings reports (price rebased to the close before each
announcement), plus the next scheduled report date. Published via GitHub Pages
from `docs/`.

## Usage

```bash
python3 -m venv .venv
.venv/bin/pip install yfinance matplotlib lxml
.venv/bin/python scripts/research.py TICKER   # e.g. TPR
```

Writes `docs/<ticker>.html`, updates `docs/index.html` and
`docs/data/registry.json`. Re-running a ticker overwrites its page.

Data: Yahoo Finance via yfinance (dividend-adjusted closes). US-listed tickers.
Informational only — not financial advice.
