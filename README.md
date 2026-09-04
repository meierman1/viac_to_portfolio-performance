# VIAC 3a → Portfolio Performance

Transfers your VIAC pillar 3a history into
[Portfolio Performance](https://www.portfolio-performance.info/).

VIAC has no export, so the data is taken out of the web app itself:
`viac_capture.js` collects it in your browser, `viac_to_pp.py` turns it into CSV
files you can import.

```bash
pip install -r requirements.txt
```

## 1. Export from VIAC

Log into <https://app.viac.ch>, set the language to German or English (the PDFs
have to be in one of those), open the DevTools console (F12), then paste in all of
[`viac_capture.js`](viac_capture.js). Chrome and Edge ask you to type
`allow pasting` first.

Then **open one of your portfolios**. That is the whole procedure: the rest
happens by itself, ending in a `viac_export.zip` containing `transactions.json`
and one PDF per buy/sell.

Useful in the console: `viac.status()`, `viac.run()`, `viac.portfolios()`.

<details>
<summary>Doing it by hand instead</summary>

1. In DevTools → Network, reload with Ctrl+F5, find the `transactions` request and
   save its raw response as `transactions.json`.
2. Set the browser to save PDFs instead of opening them, then click every buy and
   sell entry to download it. Duplicates and `(1)` suffixes are fine; don't rename
   the files. Put them in a `pdfs` folder next to the JSON.
</details>

## 2. Save your portfolio as XML (recommended)

In Portfolio Performance: `Save as → XML`, next to the script as `portfolio.xml`.
The tool then adds the VIAC funds including their price history, which a CSV
import cannot do. **Keep your original portfolio file as a backup** - a
`portfolio.xml.viac-backup` is written before anything changes.

Skip this to add the securities by hand.

## 3. Convert

```bash
py viac_to_pp.py viac_export.zip
```

Results land in `out/`. Read the summary: it lists any missing PDFs and skipped
transaction types. Fix and re-run - parsed PDFs are cached, so it is quick.

If a fund has been renamed since you last imported (the Credit Suisse funds are
now UBS ones), it offers to write the name your portfolio already uses into the
CSVs, so the import matches. `--rename` / `--no-rename` decide without asking.

| Option | |
| --- | --- |
| `-t`, `--transactions` | transactions JSON (default `transactions.json`) |
| `-p`, `--pdfs` | folder with the PDFs (default `pdfs`) |
| `-x`, `--portfolio` | PP XML to add securities to (default `portfolio.xml`) |
| `-o`, `--out` | output folder (default `out`) |
| `--rename` / `--no-rename` | don't ask about renamed funds |
| `--no-cache` | re-read every PDF |
| `--no-color` | plain output |

## 4. Import

Create a CHF account and a securities account per VIAC portfolio, then for each
portfolio: `File → Import → CSV files`, pick `out/<id>_PortfolioTransaction.csv`,
set *Type of data* to `Portfolio Transactions`, check the columns are green, and
select the two accounts. Repeat with `out/<id>_AccountTransaction.csv` as
`Account Transactions`.

Watch for securities being created at the bottom of the preview list - that means
a name did not match. Right click and replace them with the right entry.

To update later, run both steps again. Duplicate securities are not created, and
Portfolio Performance rejects duplicate transactions.

## Limitations

- **Foreign-currency dividends** cannot be imported by Portfolio Performance, so
  they are booked as interest with a note giving the original currency, rate and
  amount. You can replace them through the GUI afterwards.
- **Unhandled transaction types** (Allocation Segment, Payout, Fusion, Transfer,
  Correction, Buy/Sell Cancellation) are skipped with a warning - no test data.
  Send an anonymised `transactions.json` entry and PDF and they can be added.
- **Funds VIAC no longer offers** may be missing from `data/`, so they get no
  price history. The script names the ISIN; please report it.
- Securities in your portfolio **without an ISIN** may end up duplicated. Delete
  the duplicates before importing transactions.

## Why you have to open a portfolio

Opening one makes the app fetch
`GET /rest/web/<account>/portfolio/transactions`, which returns every portfolio at
once. The script reads that response as it goes past. It cannot make that request
itself: the account number is not served anywhere reachable, and `/rest/web/` calls
are refused with `403` unless they carry the `X-Same-Domain` header the app sends.

From there the PDFs are fetched from `GET /files/document/<documentNumber>`, which
is hard-coded (measured, not documented). It is verified before use - the reply has
to start with `%PDF` - so if VIAC ever changes it, the script asks you to click one
buy/sell entry and learns the new URL from that, rather than producing a bad
export.

The script only repeats requests your own logged-in session already makes. It
never sees your password and nothing leaves the browser tab.
