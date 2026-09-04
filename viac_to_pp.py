"""Turn a VIAC 3a transaction export into CSV files Portfolio Performance can import.

Typical use, with the zip produced by viac_capture.js:

    py viac_to_pp.py viac_export.zip

Or with files you collected by hand (transactions.json + a pdfs folder):

    py viac_to_pp.py

Run  py viac_to_pp.py --help  for all options.
"""

import argparse
import csv
import json
import os
import re
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

import pymupdf  # PyMuPDF

HERE = os.path.dirname(os.path.abspath(__file__))
ALL_SECURITIES_XML = os.path.join(HERE, 'data', 'pp_all_viac_securities.xml')

CSV_FIELDNAMES = ['Date', 'Type', 'Value', 'Security Name', 'Transaction Currency',
                  'Shares', 'Exchange Rate', 'Note']

# Words that introduce the number of shares / the exchange rate in a VIAC PDF.
RE_SHARES = re.compile(r'(?:Kauf|Buy|Verkauf|Sell|Achat|Vente|Acquisto|Vendita)\n(\d+\.\d+)')
RE_FX = re.compile(r'(?:Exchange rate|Umrechnungskurs|Taux de change|Tasso di cambio) '
                   r'([A-Z]{3})/([A-Z]{3}) (\d+\.\d+)\n')
RE_ISIN = re.compile(r'ISIN.{0,8}([A-Z0-9]{12})', re.DOTALL)


# --------------------------------------------------------------------- output --
class Out:
    """Console output that degrades gracefully when colours are unavailable."""

    def __init__(self, color=True):
        self.color = color
        self.problems = []

    def _c(self, text, code):
        return '\033[{}m{}\033[0m'.format(code, text) if self.color else text

    def info(self, msg):
        print(msg)

    def good(self, msg):
        print(self._c(msg, '1;32'))

    def warn(self, msg, remember=True):
        print(self._c('Warning: ' + msg, '1;33'))
        if remember:
            self.problems.append(('warning', msg))

    def error(self, msg, remember=True):
        print(self._c('Error: ' + msg, '1;31'))
        if remember:
            self.problems.append(('error', msg))


def enable_ansi():
    """Turn on ANSI escape handling in the legacy Windows console."""
    if os.name != 'nt':
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # 7 == STD_OUTPUT_HANDLE, 0x0004 == ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7))
    except Exception:
        return False


# ----------------------------------------------------------------- pdf reading --
class PdfIndex:
    """Finds and parses the buy/sell PDFs, caching what it read between runs."""

    def __init__(self, folder, out, cache_path=None):
        self.folder = folder
        self.out = out
        self.cache_path = cache_path
        self.cache = {}
        self.files = []
        self.hits = 0
        self.parsed = 0

        if folder and os.path.isdir(folder):
            for root, _dirs, names in os.walk(folder):
                for name in names:
                    if name.lower().endswith('.pdf'):
                        self.files.append((name, os.path.join(root, name)))

        if cache_path and os.path.isfile(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as fh:
                    self.cache = json.load(fh)
            except (ValueError, OSError):
                self.cache = {}

    def save_cache(self):
        if not self.cache_path:
            return
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as fh:
                json.dump(self.cache, fh)
        except OSError:
            pass

    def _find_file(self, document_number):
        for name, path in self.files:
            if document_number in name:
                return path
        return None

    def lookup(self, document_number):
        """Return (shares, exchange_rate, isin, currency) or raise FileNotFoundError."""
        path = self._find_file(document_number)
        if path is None:
            raise FileNotFoundError(document_number)

        stat = os.stat(path)
        cached = self.cache.get(document_number)
        if cached and cached.get('size') == stat.st_size and cached.get('mtime') == int(stat.st_mtime):
            self.hits += 1
            return cached['shares'], cached['rate'], cached['isin'], cached['currency']

        shares, rate, isin, currency = self._parse(path, document_number)
        self.parsed += 1
        self.cache[document_number] = {
            'shares': shares, 'rate': rate, 'isin': isin, 'currency': currency,
            'size': stat.st_size, 'mtime': int(stat.st_mtime), 'file': os.path.basename(path),
        }
        return shares, rate, isin, currency

    def _parse(self, path, document_number):
        shares = ''
        exchange_rate = ''
        isin = ''
        currency = ''

        with pymupdf.open(path) as document:
            for page in document:
                text = page.get_text()

                match = RE_SHARES.search(text)
                if match:
                    shares = match.group(1)

                match = RE_FX.search(text)
                if match:
                    currency = match.group(2)
                    # VIAC quotes CHF per unit of the foreign currency; PP wants the inverse.
                    exchange_rate = '{:3.8f}'.format(1 / float(match.group(3)))

                match = RE_ISIN.search(text)
                if match:
                    isin = match.group(1)

        if len(shares) < 2:
            self.out.error('{} ({}): the PDF exists but the number of shares could not be '
                           'read from it.'.format(document_number, os.path.basename(path)))
        return shares, exchange_rate, isin, currency


# ------------------------------------------------------------ transaction pass --
class Converter:
    def __init__(self, pdfs, out, out_dir):
        self.pdfs = pdfs
        self.out = out
        self.out_dir = out_dir
        self.securities = {}
        self.missing_pdfs = []
        self.unknown_types = Counter()
        self.accounts = []          # (account_id, portfolio rows, account rows)
        self.written = []
        # Prices and exchange rates carry over between portfolios; share counts do not.
        self.last_ex_rate = {}
        self.last_curr = {}
        self.last_price = {}

    def process_account(self, account_id, transactions):
        # Share counts are per VIAC portfolio: each one becomes its own PP securities account.
        holding = {}
        cancellations = [t for t in transactions if t.get('type') == 'DIVIDEND_CANCELLATION']
        portfolio_rows = []
        account_rows = []

        for transaction in transactions[::-1]:
            kind = transaction.get('type')
            if kind == 'DIVIDEND_CANCELLATION':
                continue
            if kind == 'DIVIDEND' and self._is_cancelled(transaction, cancellations):
                continue

            row = {
                'Date': transaction.get('valueDate', ''),
                'Type': '',
                'Value': round(abs(transaction.get('amountInChf', 0)), 8),
                'Security Name': '',
                'Transaction Currency': 'CHF',
                'Shares': '',
                'Exchange Rate': '',
                'Note': '',
            }

            if kind == 'CONTRIBUTION':
                row['Type'] = 'Deposit'
            elif kind == 'DIVIDEND':
                self._dividend(row, transaction)
            elif kind in ('TRADE_BUY', 'TRADE_SELL'):
                self._trade(row, transaction, holding)
            elif kind == 'INTEREST':
                row['Type'] = 'Interest'
            elif kind == 'FEE_CHARGE':
                row['Type'] = 'Fees'
            else:
                self.unknown_types[kind] += 1
                continue

            if kind in ('TRADE_BUY', 'TRADE_SELL'):
                portfolio_rows.append(row)
                if kind == 'TRADE_SELL':
                    dust = self._dust_row(row, holding)
                    if dust is not None:
                        portfolio_rows.append(dust)
            else:
                account_rows.append(row)

        self.accounts.append((account_id, portfolio_rows, account_rows))

    def write_csvs(self, rename=None):
        """Write the buffered rows, optionally under different security names."""
        rename = rename or {}
        self.written = []
        for account_id, portfolio_rows, account_rows in self.accounts:
            portfolio_path = os.path.join(self.out_dir,
                                          '{}_PortfolioTransaction.csv'.format(account_id))
            account_path = os.path.join(self.out_dir,
                                        '{}_AccountTransaction.csv'.format(account_id))
            for path, rows in ((portfolio_path, portfolio_rows), (account_path, account_rows)):
                with open(path, 'w', newline='') as fh:
                    writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
                    writer.writeheader()
                    for row in rows:
                        name = row['Security Name']
                        if name in rename:
                            row = dict(row, **{'Security Name': rename[name]})
                        writer.writerow(row)
            self.written.append((account_id, portfolio_path, len(portfolio_rows),
                                 account_path, len(account_rows)))

    @staticmethod
    def _is_cancelled(transaction, cancellations):
        """A dividend that was reversed within 30 days by an equal cancellation."""
        try:
            when = datetime.strptime(transaction['valueDate'], '%Y-%m-%d')
        except (KeyError, ValueError):
            return False
        for cancellation in cancellations:
            try:
                cancelled_on = datetime.strptime(cancellation['valueDate'], '%Y-%m-%d')
            except (KeyError, ValueError):
                continue
            if (cancellation.get('amountInChf') == transaction.get('amountInChf')
                    and abs((when - cancelled_on).days) <= 30):
                return True
        return False

    def _dividend(self, row, transaction):
        name = transaction.get('description', '')
        row['Type'] = 'Dividend'
        row['Security Name'] = name
        if self.last_ex_rate.get(name, ''):
            # PP cannot import dividends in a foreign currency, so book it as interest
            # and record what it really was in the note.
            row['Type'] = 'Interest'
            row['Note'] = ('Dividend from "{}" original currency: {}, est. exchange rate: {}, '
                           'CHF amount {}'.format(name, self.last_curr.get(name, ''),
                                                  self.last_ex_rate.get(name, ''), row['Value']))
            row['Security Name'] = ''

    def _trade(self, row, transaction, holding):
        name = transaction.get('description', '')
        row['Security Name'] = name
        row['Type'] = 'Sell' if transaction['type'] == 'TRADE_SELL' else 'Buy'

        try:
            shares, exchange_rate, isin, currency = self.pdfs.lookup(transaction['documentNumber'])
            if isin and isin not in self.securities:
                self.securities[isin] = (name, currency)
            row['Shares'] = shares
            row['Exchange Rate'] = exchange_rate
            self.last_ex_rate[name] = exchange_rate
            self.last_curr[name] = currency
            if shares and float(shares) != 0:
                self.last_price[name] = row['Value'] / float(shares)
        except (FileNotFoundError, KeyError):
            self.missing_pdfs.append({
                'date': transaction.get('valueDate', ''),
                'kind': row['Type'],
                'name': name,
                'amount': transaction.get('amountInChf', 0),
                'document': transaction.get('documentNumber', '?'),
            })

        if row['Shares'] != '':
            n_shares = round(float(row['Shares']), 3)
        elif row['Type'] == 'Sell':
            n_shares = 0            # unknown: keep the consistency check from firing
        else:
            n_shares = 999999999    # unknown: keep the consistency check from firing

        if row['Type'] == 'Sell':
            holding[name] = holding.get(name, 0) - n_shares
        else:
            holding[name] = holding.get(name, 0) + n_shares

    def _dust_row(self, row, holding):
        """After a sell, book away any leftover fraction of a share.

        VIAC rounds the share counts it prints, so a position that was fully sold can
        be left holding a few thousandths of a share. PP would carry that forever.
        """
        name = row['Security Name']
        left = holding.get(name, 0)
        price = self.last_price.get(name, 0)
        if not (left <= -0.0009 or (left >= 0.0009 and left * price < 5)):
            return None

        dust = {
            'Date': row['Date'],
            'Type': 'Delivery (Outbound)',
            'Value': '0.01',
            'Security Name': name,
            'Transaction Currency': 'CHF',
            'Shares': round(left, 3),
            'Exchange Rate': row['Exchange Rate'],
            'Note': 'Virtual transfer during VIAC data import to compensate rounding error',
        }
        holding[name] = left - dust['Shares']
        if dust['Shares'] < 0:
            dust['Shares'] *= -1
            dust['Type'] = 'Delivery (Inbound)'
        return dust


# -------------------------------------------------------------- portfolio xml --
def real_securities(root):
    """The actual securities, not the empty <security reference=.../> stubs that
    every transaction in the file uses to point back at one."""
    container = root.find('./securities')
    return list(container) if container is not None else []


def find_security_by_isin(root, isin):
    for security in real_securities(root):
        node = security.find('isin')
        if node is not None and node.text == isin:
            return security
    return None


def names_by_isin(root):
    out = {}
    for security in real_securities(root):
        isin = security.findtext('isin')
        name = security.findtext('name')
        if isin and name:
            out.setdefault(isin, name)
    return out


def build_rename_map(existing, securities, out):
    """Map VIAC's security names onto the names the portfolio already uses.

    Same ISIN means the same instrument, so the safe fix for a fund that has been
    renamed (the Credit Suisse funds became UBS ones) is to write the name the
    portfolio already knows into the CSV, leaving the portfolio itself alone.
    """
    owners = {}
    for isin, name in existing.items():
        owners.setdefault(name, []).append(isin)
    viac_names = Counter(name for name, _currency in securities.values())

    rename = {}
    skipped = []
    for isin, (viac_name, _currency) in sorted(securities.items()):
        portfolio_name = existing.get(isin)
        if not portfolio_name or portfolio_name == viac_name:
            continue
        if viac_names[viac_name] > 1:
            skipped.append((isin, viac_name, portfolio_name,
                            'VIAC uses that name for more than one ISIN'))
        elif len(owners.get(portfolio_name, [])) > 1:
            skipped.append((isin, viac_name, portfolio_name,
                            'your portfolio uses that name for more than one security'))
        else:
            rename[viac_name] = portfolio_name
    return rename, skipped


def ask_about_renames(rename, skipped, out, decision):
    """decision: True = always rename, False = never, None = ask."""
    if not rename and not skipped:
        return {}

    if rename:
        out.info('')
        out.info('{} securit{} in your portfolio under a different name than VIAC uses:'
                 .format(len(rename), 'y is' if len(rename) == 1 else 'ies are'))
        for viac_name, portfolio_name in sorted(rename.items()):
            out.info('    your portfolio: {}'.format(portfolio_name))
            out.info('    VIAC:           {}'.format(viac_name))
        out.info('Same ISIN, so the same fund - renamed by the provider.')

    for isin, viac_name, portfolio_name, why in skipped:
        out.warn('{} "{}" vs "{}" left alone: {}. Pick it by hand while importing.'
                 .format(isin, portfolio_name, viac_name, why))

    if not rename:
        return {}

    if decision is None:
        decision = True             # nobody to ask: do the useful thing, and say so
        if sys.stdin is not None and sys.stdin.isatty():
            try:
                answer = input("Use your portfolio's names in the CSV files, so the "
                               "import matches them? [Y/n] ").strip().lower()
                decision = answer in ('', 'y', 'yes', 'j', 'ja')
            except EOFError:
                pass                # no input available after all - keep the default
            except KeyboardInterrupt:
                out.info('')
                decision = False

    if decision:
        out.good("Using your portfolio's names for {} securit{}."
                 .format(len(rename), 'y' if len(rename) == 1 else 'ies'))
        return rename

    out.info("Keeping VIAC's names. You will have to pick those securities by hand "
             "while importing.")
    return {}


def update_portfolio_xml(portfolio_xml, tree, root, securities, out, backup=True):
    """Copy the securities VIAC uses (with their price history) into the PP file."""
    container = root.find('./securities')
    if container is None:
        out.error('{} does not look like a Portfolio Performance XML export '
                  '(no <securities> element).'.format(portfolio_xml))
        return

    known_root = ET.parse(ALL_SECURITIES_XML).getroot()

    added = 0
    unknown = []

    for isin, (name, currency) in sorted(securities.items()):
        if find_security_by_isin(root, isin) is not None:
            continue                      # already there; naming handled separately

        known = find_security_by_isin(known_root, isin)
        if known is None:
            unknown.append((isin, name, currency))
            continue

        container.append(known)
        added += 1
        out.info('  added {}  {}'.format(isin, name))

    if added:
        if backup:
            backup_path = portfolio_xml + '.viac-backup'
            if not os.path.exists(backup_path):
                shutil.copy2(portfolio_xml, backup_path)
                out.info('  backup of your original file: {}'.format(backup_path))
        tree.write(portfolio_xml, encoding='UTF-8', xml_declaration=True)
        out.good('  added {} securit{} to {}'.format(added, 'y' if added == 1 else 'ies',
                                                     portfolio_xml))
    else:
        out.info('  all {} securities already present, {} left untouched'
                 .format(len(securities), portfolio_xml))

    for isin, name, currency in unknown:
        out.error('{} "{}" ({}) is not in this tool\'s database. Add it to your securities by hand '
                  'before importing the transactions, and please report the ISIN, name and currency '
                  'on GitHub so it can be added.'.format(isin, name, currency))


# ------------------------------------------------------------------ the inputs --
def unpack_export(path, work_dir, out):
    """Extract a viac_export.zip and return (transactions.json path, pdf folder)."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        json_names = [n for n in names if n.lower().endswith('transactions.json')]
        if not json_names:
            out.error('{} does not contain a transactions.json.'.format(path))
            return None, None
        if os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        archive.extractall(work_dir)
        n_pdfs = sum(1 for n in names if n.lower().endswith('.pdf'))
    out.info('Unpacked {} ({} PDFs) into {}'.format(os.path.basename(path), n_pdfs, work_dir))
    return os.path.join(work_dir, json_names[0]), work_dir


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Convert a VIAC 3a export into Portfolio Performance CSV files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n'
               '  py viac_to_pp.py viac_export.zip\n'
               '  py viac_to_pp.py -t transactions.json -p pdfs -x portfolio.xml\n')
    parser.add_argument('export', nargs='?',
                        help='viac_export.zip from viac_capture.js, or a folder holding '
                             'transactions.json and the PDFs')
    parser.add_argument('-t', '--transactions', default='transactions.json',
                        help='transactions JSON file (default: %(default)s)')
    parser.add_argument('-p', '--pdfs', default='pdfs',
                        help='folder with the buy/sell PDFs (default: %(default)s)')
    parser.add_argument('-x', '--portfolio', default='portfolio.xml',
                        help='Portfolio Performance XML export to add securities to '
                             '(default: %(default)s, skipped when absent)')
    parser.add_argument('-o', '--out', default='out',
                        help='folder for the generated CSV files (default: %(default)s)')
    parser.add_argument('--rename', dest='rename', action='store_true', default=None,
                        help='without asking, write the names your portfolio already uses '
                             'for securities VIAC has since renamed')
    parser.add_argument('--no-rename', dest='rename', action='store_false',
                        help="without asking, keep VIAC's names")
    parser.add_argument('--no-cache', action='store_true',
                        help='re-read every PDF instead of reusing the cached results')
    parser.add_argument('--no-color', action='store_true', help='plain output, no ANSI colours')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    use_color = not args.no_color and not os.environ.get('NO_COLOR') \
        and sys.stdout.isatty() and enable_ansi()
    out = Out(color=use_color)

    transactions_fn = args.transactions
    pdf_folder = args.pdfs
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    from_zip = False
    if args.export:
        if zipfile.is_zipfile(args.export):
            from_zip = True
            transactions_fn, pdf_folder = unpack_export(
                args.export, os.path.join(out_dir, 'export'), out)
            if transactions_fn is None:
                return 1
        elif os.path.isdir(args.export):
            transactions_fn = os.path.join(args.export, 'transactions.json')
            pdf_folder = os.path.join(args.export, 'pdfs')
            if not os.path.isdir(pdf_folder):
                pdf_folder = args.export
        else:
            out.error('{} is neither a zip file nor a folder.'.format(args.export))
            return 1

    if not os.path.isfile(transactions_fn):
        out.error('no transactions file at "{}".'.format(transactions_fn), remember=False)
        out.info('')
        out.info('Get one the easy way: log into https://app.viac.ch, paste viac_capture.js')
        out.info('into the browser console, follow its two prompts, then run:')
        out.info('    py viac_to_pp.py viac_export.zip')
        out.info('The manual route is described in the README.')
        return 1

    with open(transactions_fn, 'r', encoding='utf-8') as fh:
        payload = json.load(fh)
    accounts = payload.get('transactions')
    if not isinstance(accounts, dict) or not accounts:
        out.error('{} has no "transactions" object in it. Did the whole response get '
                  'saved?'.format(transactions_fn), remember=False)
        return 1

    if not os.path.isdir(pdf_folder):
        out.warn('no PDF folder at "{}". Buy and sell rows will be missing their share counts '
                 'and exchange rates.'.format(pdf_folder))

    cache_path = None if args.no_cache else os.path.join(out_dir, '.pdf_cache.json')
    pdfs = PdfIndex(pdf_folder, out, cache_path)
    converter = Converter(pdfs, out, out_dir)

    for account_id, transactions in accounts.items():
        out.info('Portfolio {}: {} transactions'.format(account_id, len(transactions)))
        converter.process_account(account_id, transactions)
    pdfs.save_cache()

    # The portfolio is read before the CSVs are written, so securities it already
    # holds under an older name can be written out under that name and match on
    # import. Parsed once - these files run to tens of megabytes.
    portfolio_tree = portfolio_root = None
    rename = {}
    if os.path.isfile(args.portfolio):
        try:
            portfolio_tree = ET.parse(args.portfolio)
            portfolio_root = portfolio_tree.getroot()
        except ET.ParseError as exc:
            out.error('could not read {}: {}'.format(args.portfolio, exc))
        else:
            candidates, skipped = build_rename_map(
                names_by_isin(portfolio_root), converter.securities, out)
            rename = ask_about_renames(candidates, skipped, out, args.rename)

    converter.write_csvs(rename)

    securities_csv = os.path.join(out_dir, 'securities.csv')
    with open(securities_csv, 'w', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['ISIN', 'VIAC Name', 'Currency', 'Name Used In CSV'])
        for isin, (name, currency) in sorted(converter.securities.items()):
            writer.writerow([isin, name, currency, rename.get(name, name)])

    out.info('')
    out.good('Generated files in {}/'.format(out_dir))
    for account_id, portfolio_path, n_portfolio, account_path, n_account in converter.written:
        out.info('  {}: {} portfolio rows, {} account rows'.format(
            account_id, n_portfolio, n_account))
    out.info('  securities.csv: {} securities'.format(len(converter.securities)))
    if pdfs.hits or pdfs.parsed:
        out.info('  PDFs: {} read, {} taken from cache'.format(pdfs.parsed, pdfs.hits))

    out.info('')
    if portfolio_root is not None:
        out.info('Checking the securities in {} ...'.format(args.portfolio))
        update_portfolio_xml(args.portfolio, portfolio_tree, portfolio_root,
                             converter.securities, out)
    elif os.path.isfile(args.portfolio):
        pass                              # unreadable; already reported above
    else:
        out.info('No portfolio XML at "{}" - skipping the securities step.'.format(args.portfolio))
        out.info('In Portfolio Performance use File -> Save as -> XML, put the file here and run')
        out.info('again to get the funds including their price history. Otherwise add the')
        out.info('securities from {} by hand.'.format(securities_csv))

    # ------------------------------------------------------------- what is left --
    out.info('')
    if converter.missing_pdfs:
        out.warn('{} buy/sell transaction(s) have no PDF, so their share count and exchange rate '
                 'are missing:'.format(len(converter.missing_pdfs)), remember=False)
        for item in sorted(converter.missing_pdfs, key=lambda i: i['date']):
            out.info('    {}  {:4s}  CHF {:>10.2f}  {}  [document {}]'.format(
                item['date'], item['kind'], item['amount'], item['name'], item['document']))
        if from_zip:
            out.info('  Run viac_capture.js again to fetch them (it reports downloads it could')
            out.info('  not make), or download them by hand into a folder and point the tool at')
            out.info('  it:  py viac_to_pp.py -t {} -p <folder>'.format(transactions_fn))
        else:
            out.info('  Add those PDFs to "{}" and run again - re-running is cheap because the'
                     .format(pdf_folder))
            out.info('  PDFs already read are cached.')

    if converter.unknown_types:
        out.warn('transaction types this tool does not handle were skipped:', remember=False)
        for kind, count in converter.unknown_types.most_common():
            out.info('    {} x {}'.format(count, kind))
        out.info('  If you need these, please open a GitHub issue with an anonymised example.')

    problems = len(converter.missing_pdfs) + len(converter.unknown_types) + len(out.problems)
    out.info('')
    if problems:
        out.warn('{} thing(s) above need your attention before the import is complete.'
                 .format(problems), remember=False)
    else:
        out.good('No problems found.')
    out.info('Next: create a CHF account and a securities account per VIAC portfolio in Portfolio')
    out.info('Performance, then import the CSV files from {}/ (see the README).'.format(out_dir))
    return 0


if __name__ == '__main__':
    sys.exit(main())
