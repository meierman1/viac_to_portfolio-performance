import json
import csv
import os
import re
from datetime import datetime, timedelta
import pymupdf  # PyMuPDF
import xml.etree.ElementTree as ET

# filename of generated securities csv
securities_csv = 'securities.csv'

# filename of transactions json file
transactions_fn = 'transactions.json'

# folder with the transaction pdfs
pdf_folder = 'pdfs'

# portfolio XML filename
portfolio_xml = 'portfolio.xml'

if not (os.path.isfile(transactions_fn)):
    print(
        "\033[1;31mError: No file {} with your transactions found. Follow instructions online to download it and paste it "
        "where you execute this script named {}\033[0m".format(transactions_fn, transactions_fn))
    exit(1)


# Define a function to extract shares and exchange rate from a PDF file
def extract_shares_and_exchange_rate(document_number):
    global pdf_folder
    shares = ''
    exchange_rate = ''
    isin = ''

    pdf_files = [f for f in os.listdir(pdf_folder) if document_number in f]

    if not pdf_files:
        raise FileNotFoundError(
            f"\033[1;31mError: No PDF file found containing document number {document_number}\033[0m")

    pdf_path = os.path.join(pdf_folder, pdf_files[0])

    # Open the PDF file
    pdf_document = pymupdf.open(pdf_path)

    # Iterate through each page
    for page_num in range(len(pdf_document)):
        page = pdf_document.load_page(page_num)
        text = page.get_text()

        # Search for shares value
        shares_match = re.search(r'(?:Kauf|Buy|Verkauf|Sell)\n(\d+\.\d+)', text)
        if shares_match:
            shares = shares_match.group(1)
        if len(shares) < 2:
            print(f"\033[1;31mError in transaction {document_number}. File exists but could not find number of shares!\033[0m")

        currency = ""
        # Search for exchange rate value
        exchange_rate_match = re.search(r'(?:Exchange rate|Umrechnungskurs) [A-Z]{3}/[A-Z]{3} (\d+\.\d+)\n', text)
        if exchange_rate_match:
            exchange_rate = exchange_rate_match.group(1)
            exchange_rate = "{:3.8f}".format(1 / float(exchange_rate))  # take the inverse

            currency_match = re.search(r'(?:Exchange rate|Umrechnungskurs) [A-Z]{3}/([A-Z]{3})\s*\d+\.\d+', text,
                                       re.DOTALL)
            if currency_match:
                currency = currency_match.group(1)

        # Search for ISIN
        isin_match = re.search(r'ISIN.{0,8}([A-Z0-9]{12})', text, re.DOTALL)
        if isin_match:
            isin = isin_match.group(1)

    pdf_document.close()
    return shares, exchange_rate, isin, currency


last_ex_rate = {}
last_curr = {}
last_price = {}
holding = {}


def process_transactions(account_id, transactions, securities):
    # Prepare the CSV file names
    portfolio_csv_file_name = f"{account_id}_PortfolioTransaction.csv"
    account_csv_file_name = f"{account_id}_AccountTransaction.csv"

    # Define fieldnames for each CSV file
    fieldnames = ['Date', 'Type', 'Value', 'Security Name', 'Transaction Currency', 'Shares', 'Exchange Rate', 'Note']

    # Open the CSV files for writing
    with open(portfolio_csv_file_name, 'w', newline='') as portfolio_csvfile, open(account_csv_file_name, 'w',
                                                                                   newline='') as account_csvfile:
        portfolio_writer = csv.DictWriter(portfolio_csvfile, fieldnames=fieldnames)
        account_writer = csv.DictWriter(account_csvfile, fieldnames=fieldnames)

        # Write the headers
        portfolio_writer.writeheader()
        account_writer.writeheader()

        # Track dividend cancellations to ignore corresponding dividends
        dividend_cancellations = []

        # First pass: build the list of dividend cancellations
        for transaction in transactions:
            if transaction['type'] == 'DIVIDEND_CANCELLATION':
                dividend_cancellations.append(transaction)

        # Second pass: process transactions and write to the appropriate CSV
        for transaction in transactions[::-1]:
            if transaction['type'] == 'DIVIDEND_CANCELLATION':
                continue

            if transaction['type'] == 'DIVIDEND':
                # Check if there is a matching DIVIDEND_CANCELLATION within 30 days
                cancel = False
                for cancel_transaction in dividend_cancellations:
                    cancel_date = datetime.strptime(cancel_transaction['valueDate'], '%Y-%m-%d')
                    transaction_date = datetime.strptime(transaction['valueDate'], '%Y-%m-%d')
                    if (cancel_transaction['amountInChf'] == transaction['amountInChf'] and
                            abs((transaction_date - cancel_date).days) <= 30):
                        cancel = True
                        break
                if cancel:
                    continue

            row = {
                'Date': transaction['valueDate'],
                'Type': '',
                'Value': round(abs(transaction['amountInChf']), 8),
                'Security Name': '',
                'Transaction Currency': 'CHF',
                'Shares': '',
                'Exchange Rate': '',
                'Note': ''
            }

            if transaction['type'] == 'CONTRIBUTION':
                row['Type'] = 'Deposit'
            elif transaction['type'] == 'DIVIDEND':
                row['Type'] = 'Dividend'
                row['Security Name'] = transaction.get('description', '')
                if last_ex_rate[row['Security Name']] != '':  # foreign currency divident, not working, make it interest
                    row['Type'] = 'Interest'
                    row[
                        'Note'] = 'Dividend from "{}" original currency: {}, est. exchange rate: {}, CHF amount {}'.format(
                        row['Security Name'],
                        last_curr[row['Security Name']],
                        last_ex_rate[row['Security Name']],
                        row['Value'])
                    row['Security Name'] = ''
                    # row['Gross Amount'] = row['Value']
                    # row['Gross Amount'] = row['Value']*float(row['Exchange Rate'])
                    # row['Value'] = row['Value']*float(row['Exchange Rate'])
                    # row['Value'] = row['Value']
                    # row['Transaction Currency'] = last_curr[row['Security Name']]
                    # row['Transaction Currency'] = 'CHF'
                    # row['Currency Gross Amount'] = last_curr[row['Security Name']]

            elif transaction['type'] in ['TRADE_SELL', 'TRADE_BUY']:
                row['Security Name'] = transaction.get('description', '')
                try:
                    shares, exchange_rate, isin, currency = extract_shares_and_exchange_rate(transaction['documentNumber'])
                    if isin not in securities:
                        securities[isin] = (row['Security Name'], currency)
                    row['Shares'] = shares
                    row['Exchange Rate'] = exchange_rate
                    last_ex_rate[row['Security Name']] = exchange_rate
                    last_curr[row['Security Name']] = currency
                    if float(row['Shares']) != 0:
                        last_price[row['Security Name']] = row['Value'] / float(row['Shares'])
                except FileNotFoundError as e:
                    print(f"\033[1;33mWarning: PDF not found for transaction (add it to {pdf_folder} folder!):\033[0m")
                    print(f"\033[1;33m{transaction['valueDate']} {transaction['type'].split('_')[1]} "
                        f"{transaction.get('description', '')} of CHF {transaction['amountInChf']}\033[0m")
                if transaction['type'] == 'TRADE_SELL':
                    if row['Shares'] != '': # normal case, when pdf is found
                        n_shares = round(float(row['Shares']), 3)
                    else:
                        n_shares = 0 # effectively deactivate consistency check
                    row['Type'] = 'Sell'
                    holding[row['Security Name']] -= n_shares
                else:  # transaction['type'] == 'TRADE_BUY':
                    row['Type'] = 'Buy'
                    if row['Shares'] != '': # normal case, when pdf is found
                        n_shares = round(float(row['Shares']), 3)
                    else:
                        n_shares = 999999999 # effectively deactivate consistency check
                    if row['Security Name'] not in holding:
                        holding[row['Security Name']] = n_shares
                    else:
                        holding[row['Security Name']] += n_shares


            elif transaction['type'] == 'INTEREST':
                row['Type'] = 'Interest'
            elif transaction['type'] == 'FEE_CHARGE':
                row['Type'] = 'Fees'
            elif transaction['type'] != 'DIVIDEND_CANCELLATION':
                print("\033[1;33mWarning: Unknown Transaction type {}. Transaction ignored\033[0m".format(transaction['type']))

            # Write to the appropriate CSV file
            if transaction['type'] in ['TRADE_SELL', 'TRADE_BUY']:
                portfolio_writer.writerow(row)
                if transaction['type'] == 'TRADE_SELL':  # check if we have float-dust left
                    if holding[row['Security Name']] <= -0.0009 or (
                            holding[row['Security Name']] >= 0.0009 and holding[row['Security Name']] * last_price[
                        row['Security Name']] < 5):
                        row2 = {
                            'Date': row['Date'],
                            'Type': 'Delivery (Outbound)',
                            'Value': '0.01',
                            'Security Name': row['Security Name'],
                            'Transaction Currency': 'CHF',
                            'Shares': round(holding[row['Security Name']], 3),
                            'Exchange Rate': row['Exchange Rate'],
                            'Note': 'Virtual transfer during VIAC data import to compensate rounding error'
                        }
                        holding[row['Security Name']] -= row2['Shares']
                        if row2['Shares'] < 0:
                            row2['Shares'] *= -1
                            row2['Type'] = 'Delivery (Inbound)'
                        portfolio_writer.writerow(row2)

            else:
                account_writer.writerow(row)


# Function to find security by ISIN in the XML tree of the portfolio file
def find_security_by_isin(root, isin):
    for security in root.findall('.//security'):
        if security.find('isin') is not None and security.find('isin').text == isin:
            return security
    return None


# Load the JSON data from the file
with open(transactions_fn, 'r') as file:
    transactions = json.load(file)

# Process each account in the JSON data
securities = {}
for account_id, transactions in transactions['transactions'].items():
    print("Account {} generate .csv files".format(account_id))
    process_transactions(account_id, transactions, securities)

with open(securities_csv, mode='w', newline='') as file:
    print("Generate {} of securities used in VIAC portfolio".format(securities_csv))
    writer = csv.writer(file)
    # Write the header
    writer.writerow(["ISIN", "Security Name", "Currency"])
    # Write the data
    for key, value in securities.items():
        writer.writerow([key, value[0], value[1]])

print("finished generating files!")

if not (os.path.isfile(portfolio_xml)):
    print("\033[1;33mWarning: No portfolio XML file was found. Here are your options:\033[0m")
    print("- Either add the portfolio named {} into the folder where you execute this script and run it again".format(
        portfolio_xml))
    print(
        "- Add Securities manually (either based on the {} file, or by continuing and importing the transactions). However, you will likely not get historic data for most funds.".format(
            portfolio_xml, securities_csv))
    print(
        "- If you know that the securities are already in your portfolio, just ignore this and continue with importing the transactions")
    exit(1)

print("Loading Securities into portfolio!")
# modify securities

securities_data = {}
with open(securities_csv, mode='r') as file:
    csv_reader = csv.DictReader(file)
    for row in csv_reader:
        isin = row['ISIN']
        name = row['Security Name']
        currency = row['Currency']
        securities_data[isin] = {'name': name, 'currency': currency}

# Parse portfolio xml
portfolio_tree = ET.parse(portfolio_xml)
portfolio_root = portfolio_tree.getroot()

# Check each security from the CSV in the portfolio XML
for isin, data in securities_data.items():
    name_csv = data['name']
    currency_csv = data['currency']

    security_in_portfolio = find_security_by_isin(portfolio_root, isin)

    if security_in_portfolio is not None:
        name_portfolio = security_in_portfolio.find('name').text
        if name_csv == name_portfolio:
            print(f"{isin} {name_csv} ok")
        else:
            print(
                f"\033[1;33mWarning: {isin} is already in the portfolio but name is {name_portfolio} but viac calls it {name_csv}. This may require you to select the security manually when importing transactions.\033[0m")
    else:
        # If not found in portfolio, check in pp_all_securities.xml
        all_securities_xml = 'data/pp_all_viac_securities.xml'
        all_securities_tree = ET.parse(all_securities_xml)
        all_securities_root = all_securities_tree.getroot()

        security_in_all_securities = find_security_by_isin(all_securities_root, isin)

        if security_in_all_securities is not None:
            # Copy the security to the portfolio XML
            portfolio_root.find('.//securities').append(security_in_all_securities)
            print(f"added {isin} {name_csv} to portfolio")

            # Save the updated portfolio XML
            portfolio_tree.write(portfolio_xml)
        else:
            print(
                f"\033[1;31mError: {isin} {name_csv} {currency_csv} is not in our database, please add it manually to the securities before adding transactions. You may also send this info to us through github so that we can add it to the list.\033[0m")

print(
    "all done. You can now open the portfolio file and go ahead and create the accounts (if you have not already) and import the transactions!")
