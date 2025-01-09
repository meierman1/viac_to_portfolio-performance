# VIAC 3A data transfer to Portfolio Performance

This repository provides a manual and python code to transfer your VIAC portfolio history to [Portfolio Performance](https://www.portfolio-performance.info/). Unfortunately, this process still includes manual steps as not all required data is readily available.

# Requirements

All you need is internet access, your VIAC login, an installation of Portfolio Performance, and Python installed. The only non-standard package used is PyMuPDF to read pdf files. If you don't have it, install using:

`pip install pymupdf`

# Data Preparation

The data needed for this process is extracted from two sources: A .json file which contains all transactions, as well as the pdf files for buy and sell transactions.

1. Clone this repository or just download viac_to_pp.py and paste it into an otherwise empty folder
2. Create a subfolder `pdfs`

## transactions.json

1. Log in to your VIAC account
2. Click on one of your 3A portfolios
3. Open the developpers console of your webbrowser (Ctrl+Shift+I for Firefox).
4. Go to the Network tab
5. Reload the VIAC page without cache (Ctrl + F5 for Firefox)
6. Use search bar to find the transactions entry
7. Click on it, click on response, select raw and copy paste the json into a text editor and save as transactions.json in the previously created folder next to the python file

## Buy and Sells

Unfortunately, the amount of shares of each buy and sell operation as well as the exchange rate is only available in the corresponding transaction pdf. Follow these steps to download all necessary file within a few minutes (even if it is multiple hundreds).

1. In your browser change the settings to not open pdf files upon download. For Firefox:
	1. Go to settings
	2. Search for Applications
	3. For PDF select "save file"
2. For each VIAC portfolio go to Transactions and filter to only display Buy and sell
3. Click on every entry to download the pdf. Don't worry about duplicates, they are no issue. If you miss one, the tool will later tell you. So you can go fast!
4. Copy all downloaded pdfs into the previously created `pdfs` folder. Do not rename them! If they have indication in the filename of duplicate downloads (e.g., `(1)`) don't worry about them!

# Generate csv files

Now run the python file:

`py viac_to_pp.py`

Pay attention to the output! If there are warnings or errors, make sure to fix them (e.g., download missing pdf files) and run the script again.


# Import all the data

## Create Accounts

Create a CHF-Denominated Account and a Portfolio for each VIAC Portfolio. Naming does not matter here.

## Import Securities

1. In PP: "File -> Import -> CSV" and select the securities.csv file which you previously generated
2. Change Type of Data to "Securities" and make sure all Columns are correctly recognized (green). Otherwise, manually select the corresponding column type.
3. On the next screen, check for errors (there should be none) and click finish
4. The next screen shows which funds were recognized and data available (See Updated Configuration Column). Unfortunately, a lot of the funds are not recognized this way due to the CS - UBS merger. Press OK.
5. If you want price data for the funds (recommended), you have to manually replace the non-recognized funds:
	1. 

## Import Portfolio transactions


## Import Account Transactions


# Know Issues / Limitations

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

