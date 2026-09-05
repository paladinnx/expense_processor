# Expense processor

This folder contains a local-only expense analysis script.

## Files

- `expense_processor.py` — main program
- `rules.csv` — public, generic classification rules
- `personal_rules.csv` — optional private rules for location-specific or personally identifying transactions; keep this file local
- Rule files are inputs only; the processor does not generate or embed fallback rules.
- `README.txt` — instructions

## Recommended folder layout

Put your three exports in the same folder:

    credit_card.csv
    checking.csv
    amazon.csv

## Run

Open Terminal, change to this folder, and run:

    python3 expense_processor.py --credit-card credit_card.csv --bank checking.csv --amazon amazon.csv --rules rules.csv --rules personal_rules.csv

It creates:

    expenses_clean.csv
    amazon_matches.csv

Open `expenses_clean.csv` directly in Google Sheets.

## Visualizer

Open `expense_visualizer.html` in a browser while `expenses_clean.csv` is in the same folder. It loads the cleaned data locally and provides filters for merchant, category, account, intent, time window, and flow type. Expenses are shown by default; use the flow toggles to include refunds, income, or transfers. If a browser blocks loading the adjacent CSV, use **Load another CSV** and choose `expenses_clean.csv`.

The visualizer can also load `rules.csv` and categorize transactions. The transaction table includes the full description/item text, including itemized Amazon products. Use **Categorize** beside a transaction to set its category, subcategory, and intent. Merchant matching is the default; choose description/keyword matching for a narrower rule. Saving applies the new priority-5 rule to matching expenses currently shown and keeps it in memory. Use **Download rules.csv**, replace the project copy, and rerun the processor to use the rule for future exports.

## Important behavior

- Credit-card payments are marked as transfers, so they aren't counted as spending.
- Bank account movements, investment contributions, card autopays, and transfers to checking/savings institutions are marked as `Transfer` before expense rules run, so they aren't counted as spending by default.
- Outgoing Venmo/Zelle payments, payments to people, and ATM withdrawals remain `Expense` because they represent money spent. Incoming payments remain `Income`.
- Bank income is retained but marked `Income`.
- Credit-card returns/adjustments are marked `Refund`.
- Amazon orders are matched conservatively to Amazon credit-card charges.
- A matched Amazon charge is replaced by itemized Amazon rows so the charge isn't double-counted.
- Amazon item rows use product keyword rules first, then apply merchant rules to the Amazon merchant as a fallback.
- Unmatched Amazon charges stay in the output and are flagged for review.
- Ambiguous Amazon matches are not automatically assigned.
- `rules.csv` is the main place to customize your categories.

The visualizer starts with only `Expense` selected. Use the flow toggles to inspect transfers, income, refunds, or unusual `Other` rows separately.

Click a category bar to filter the dashboard to that category; the category panel then shows its subcategories. Use the transaction sort control to order rows by newest date or amount.

The spending-over-time chart is stacked by expense category. Hover over a colored segment to see its month, category, and amount; the legend below the chart maps colors to categories.

## Amount convention

Normal expenses are positive numbers in the final CSV.
Refunds are negative.
Income is positive.
Transfers may be positive or negative.

This makes spreadsheet pivot tables easier to read.
