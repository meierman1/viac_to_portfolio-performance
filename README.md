# VIAC 3A data transfer to Portfolio Performance

This repository provides a method to transfer your VIAC portfolio history to [Portfolio Performance](https://www.portfolio-performance.info/). Unfortunately, this process still includes manual steps as not all required data is readily available.

# Requirements

All you need is internet access, your VIAC login, an installation of Portfolio Performance, and Python installed. The only non-standard package used is PyMuPDF to read pdf files. If you don't have it, install using:

`pip install pymupdf`

Alternatively, you can also use `pip install -r requirements.txt` which does the same thing.

# Data Preparation


The data needed for this process is extracted from two sources: A .json file which contains all transactions, as well as the pdf files for buy and sell transactions.

1. Clone this repository (or download and extract a .zip image)
2. Create a subfolder `pdfs`

## Get your transactions.json file

1. Log into your VIAC account
2. Click on one of your 3A portfolios
3. Open the Developer Tools of your webbrowser (Ctrl+Shift+I for Firefox).
4. Go to the Network tab in the Developer Tools
5. Reload the VIAC page without using cached data (Ctrl + F5 for Firefox)
6. Use search bar to find the `transactions` entry
7. Click on it, click on response, select raw and copy paste the json into a text editor and save it as `transactions.json` in the previously created folder next to the python file

## Get your Buy and Sells Transaction files

Unfortunately, the amount of shares of each buy and sell operation as well as the exchange rate is only available in the corresponding transaction pdf. Follow these steps to download all necessary file within a few minutes (even if it is multiple hundreds).

1. In your browser change the settings to not open pdf files upon download. For Firefox:
	1. Go to settings
	2. Search for Applications
	3. For PDF select "save file"
2. Log into your VIAC account, make sure it is set to German or English (pdfs need to be in that language)
3. For each VIAC portfolio go to Transactions and filter to only display Buy and sell transactions
4. Click on every entry to download the pdf. Don't worry about duplicates, they are no issue. If you miss one, the tool will later tell you. _You can go fast!_
5. Copy all downloaded pdfs into the previously created `pdfs` folder. Again, don't bother to remove duplicates. Do not rename the files! If they have indication in the filename of duplicate downloads (e.g., `(1)`) don't worry about them!
6. Reset your browser settings to open PDFs again

## Save your Portfolio as XML

Since securities importing as csv does not produce historic data for most VIAC funds, this tool can enter them directly into the raw XML portfolio file. This is optional. If you do not want that or prefer to add them manually, ignore this step.

1. Open your portfolio, click `Save as -> XML`, save into the same folder as the transactions.json

_Make sure to keep the original portfolio in the original location as a backup_

Please note that this may cause double entries in the Securities in case your existing ones have no ISIN entered. Make sure to delete possible duplicates before importing transactions.

# Run the tool

Now run the python file:

`py viac_to_pp.py`

Pay attention to the output! If there are warnings or errors, make sure to fix them (e.g., download missing pdf files) and run the script again. Don't worry 


# Import all the data

## Create Accounts

Create a CHF-Denominated Account and a Portfolio for each VIAC Portfolio. Naming does not matter here.

## Import Portfolio and Account transactions

Repeat for each one of your VIAC portfolios:

1. In PP `File->Import->CSV files`, select the PorfolioTransaction file that was generated
2. In the preview, change Type of data to `Portfolio Transactions` and make sure all columns are recognized (green), hit next
3. Select the Cash and Securities Accounts and scroll through all transactions (they should all appear with a green checkmark). If you imported the securities before, make sure there are no securities imported at the bottom of the list. If there are, right click and replace them with the right entry (this may be caused by mismatched naming which is used for identifiaction).
4. Repeat the process for the AccountTransaction file (and select `Account Transactions` accordingly), again make sure no new Securities are imported in case some names are mis-matched.

That's it, you are done. You can now save your portfolio back into a binary or a securely encoded file. Keep this folder around - it makes later updates easier to have the whole transaction history.

# Updating the Portfolio
If you want to come back later and update your VIAC portfolio with new transactions, simply download the transactions.json file again and the new buy and sell pdfs. Then rerun the tool. No duplicate securities will be generated (and duplicate transactions are rejected during import).

# Known Issues / Limitations

## Old funds
The automatic import of older funds may not work because it is not in our dummy portfolio (see data folder). The script will print a warning. Feel free to open an issue with the corresponding ISIN, fund name (as used by VIAC) and fund currency. I will then add them.

## Dividends in foreign Currencies
PP currently has a bug that does not allow the import of Dividends in foreign currencies. As a workaround, these dividends are treated as interest on the cash with a note explaining. You can later go and manually replace them if you want (adding these dividends works through the GUI).

## Unknown transactions
This script does not currently handle the following transactions:

- Allocation Segment
- Payout
- Fusion
- Transfer
- Correction
- Buy Cancellation
- Sell Cancellation

The script will produce a warning if one of those are found.
I do not have data to test these unfortunately. If you have any of these, you can send me the transactions.json entry and the pdf (both can be modified/anonymized ofc before doing so)

