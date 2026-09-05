#!/usr/bin/env python3
"""
Local expense normalizer / classifier.

Runs entirely on your computer. No internet connection or external APIs are used.

Expected input files:
  --credit-card  Credit-card CSV
  --bank         Checking-account CSV
  --amazon       Amazon order-history CSV

Outputs:
  expenses_clean.csv   - combined, normalized transaction file
  amazon_matches.csv  - audit trail of Amazon orders matched to card charges

Optional:
  rules.csv            - merchant/keyword classification rules

Example:
  python expense_processor.py \
      --credit-card credit_card.csv \
      --bank checking.csv \
      --amazon amazon.csv \
      --output expenses_clean.csv \
      --rules rules.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path


DATE_FORMATS = (
    "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%d",
)

BANK_TRANSFER_PATTERNS = (
    "CREDIT CARD",
    "CARD PAYMENT",
    "CAPITAL ONE",
    "CHASE CARD",
    "AMERICAN EXPRESS",
    "AMEX",
    "DISCOVER",
    "CITI CARD",
    "BARCLAYS",
    "SYNCHRONY",
    "WELLS FARGO CARD",
    "SCHWAB",
    "SCHOLARSHARE",
    "BROKERAGE",
    "MONEYLINK",
    "ALLY BANK",
    "SAVINGS",
    "ONLINE TRANSFER TO CHK",
    "ONLINE TRANSFER FROM SAV",
)

BANK_CARD_PAYMENT_PATTERNS = (
    "AUTOPAY",
    "CREDIT CARD",
    "CARD PAYMENT",
    "CAPITAL ONE",
    "CHASE CARD",
    "AMERICAN EXPRESS",
    "AMEX",
    "DISCOVER",
    "CITI CARD",
    "BARCLAYS",
    "SYNCHRONY",
    "WELLS FARGO CARD",
)

OUTPUT_FIELDS = [
    "transaction_date",
    "post_date",
    "source",
    "source_id",
    "merchant",
    "description",
    "amount",
    "flow_type",
    "category",
    "subcategory",
    "discretionary",
    "amazon_order_id",
    "amazon_product",
    "amazon_quantity",
    "original_category",
    "review",
]

DEFAULT_RULES = [
    # priority, match_type, pattern, category, subcategory, discretionary
    (10, "merchant", "AMAZON", "Shopping", "Amazon", "Review"),
    (20, "merchant", "COSTCO", "Groceries", "Warehouse", "No"),
    (20, "merchant", "TRADER JOE", "Groceries", "Groceries", "No"),
    (20, "merchant", "SAFEWAY", "Groceries", "Groceries", "No"),
    (20, "merchant", "VONS", "Groceries", "Groceries", "No"),
    (20, "merchant", "WHOLE FOODS", "Groceries", "Groceries", "No"),
    (20, "merchant", "RALPHS", "Groceries", "Groceries", "No"),
    (20, "merchant", "WALMART", "Groceries", "Groceries", "No"),
    (30, "merchant", "TARGET", "Shopping", "General", "Review"),
    (30, "merchant", "HOME DEPOT", "Home", "Home improvement", "Review"),
    (30, "merchant", "LOWE'S", "Home", "Home improvement", "Review"),
    (30, "merchant", "LOWES", "Home", "Home improvement", "Review"),
    (30, "merchant", "SHELL", "Transportation", "Gas", "No"),
    (30, "merchant", "CHEVRON", "Transportation", "Gas", "No"),
    (30, "merchant", "ARCO", "Transportation", "Gas", "No"),
    (30, "merchant", "76", "Transportation", "Gas", "No"),
    (30, "merchant", "STARBUCKS", "Restaurants", "Coffee", "Yes"),
    (30, "merchant", "MCDONALD", "Restaurants", "Fast food", "Yes"),
    (30, "merchant", "CHIPOTLE", "Restaurants", "Fast food", "Yes"),
    (30, "merchant", "DOORDASH", "Restaurants", "Delivery", "Yes"),
    (30, "merchant", "UBER EATS", "Restaurants", "Delivery", "Yes"),
    (30, "merchant", "GRUBHUB", "Restaurants", "Delivery", "Yes"),
    (30, "merchant", "NETFLIX", "Entertainment", "Streaming", "Yes"),
    (30, "merchant", "SPOTIFY", "Entertainment", "Streaming", "Yes"),
    (30, "merchant", "DISNEY", "Entertainment", "Streaming", "Yes"),
    (30, "merchant", "APPLE.COM/BILL", "Entertainment", "Digital services", "Yes"),
    (30, "merchant", "STEAMGAMES", "Entertainment", "Games", "Yes"),
    (30, "merchant", "PLAYSTATION", "Entertainment", "Games", "Yes"),
    (30, "merchant", "XBOX", "Entertainment", "Games", "Yes"),
    (30, "merchant", "AIRBNB", "Travel", "Lodging", "Yes"),
    (30, "merchant", "DELTA AIR", "Travel", "Flights", "Yes"),
    (30, "merchant", "UNITED AIR", "Travel", "Flights", "Yes"),
    (30, "merchant", "SOUTHWEST", "Travel", "Flights", "Yes"),
    (30, "merchant", "AMERICAN AIR", "Travel", "Flights", "Yes"),
    (30, "merchant", "T-MOBILE", "Utilities", "Cell phone", "No"),
    (30, "merchant", "AT&T", "Utilities", "Cell phone", "No"),
    (30, "merchant", "VERIZON", "Utilities", "Cell phone", "No"),
    (30, "merchant", "SDGE", "Utilities", "Electric/gas", "No"),
    (30, "merchant", "SAN DIEGO GAS", "Utilities", "Electric/gas", "No"),
    (30, "merchant", "GEICO", "Insurance", "Auto", "No"),
    (30, "merchant", "PROGRESSIVE", "Insurance", "Auto", "No"),
    (30, "keyword", "DENTIST|DENTAL", "Health", "Dental", "No"),
    (30, "keyword", "PHARMACY|CVS|WALGREENS", "Health", "Medical/pharmacy", "No"),
    (30, "keyword", "SCHOOL|DAYCARE|DAY CARE", "Daughter", "School/childcare", "No"),
    (30, "keyword", "DMV", "Transportation", "DMV", "No"),
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--credit-card", required=True, type=Path)
    p.add_argument("--bank", required=True, type=Path)
    p.add_argument("--amazon", required=True, type=Path)
    p.add_argument("--output", default="expenses_clean.csv", type=Path)
    p.add_argument("--rules", default="rules.csv", type=Path)
    p.add_argument("--amazon-window", default=5, type=int,
                   help="Days before/after card transaction allowed for Amazon matching.")
    p.add_argument("--amazon-tolerance", default="0.02",
                   help="Dollar tolerance for Amazon/card matching.")
    return p.parse_args()


def money(value) -> Decimal:
    if value is None or str(value).strip() == "":
        return Decimal("0")
    s = str(value).strip().replace("$", "").replace(",", "").replace("'", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Could not parse amount: {value!r}")


def parse_date(value: str) -> datetime.date:
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Could not parse date: {value!r}")


def clean_text(value: str) -> str:
    value = (value or "").replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", value).strip()


def normalize_merchant(description: str) -> str:
    s = clean_text(description).upper()
    s = re.sub(r"\b\d{3,}\b", " ", s)
    s = re.sub(r"\*+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -")


def load_csv(path: Path) -> list[dict]:
    # utf-8-sig handles common Excel/merchant-export BOMs.
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def ensure_rules_file(path: Path):
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "priority", "match_type", "pattern",
            "category", "subcategory", "discretionary"
        ])
        for row in DEFAULT_RULES:
            w.writerow(row)


def load_rules(path: Path) -> list[dict]:
    ensure_rules_file(path)
    rows = load_csv(path)
    rules = []
    for r in rows:
        rules.append({
            "priority": int(r.get("priority") or 100),
            "match_type": clean_text(r.get("match_type")).lower(),
            "pattern": clean_text(r.get("pattern")).upper(),
            "category": clean_text(r.get("category")),
            "subcategory": clean_text(r.get("subcategory")),
            "discretionary": clean_text(r.get("discretionary")),
        })
    return sorted(rules, key=lambda x: x["priority"])


def classify(text: str, rules: list[dict]):
    text_u = clean_text(text).upper()
    for r in rules:
        if r["match_type"] == "merchant":
            matched = r["pattern"] in text_u
        elif r["match_type"] == "keyword":
            patterns = [x.strip() for x in r["pattern"].split("|") if x.strip()]
            matched = any(x in text_u for x in patterns)
        elif r["match_type"] == "regex":
            matched = bool(re.search(r["pattern"], text_u))
        else:
            matched = False

        if matched:
            return r["category"], r["subcategory"], r["discretionary"]

    return "Uncategorized", "Needs review", "Review"


def classify_amazon_item(merchant: str, product: str, rules: list[dict]):
    """Use product rules first, then apply merchant rules to the merchant."""
    product_rules = [r for r in rules if r["match_type"] != "merchant"]
    category, subcategory, discretionary = classify(product, product_rules)
    if category != "Uncategorized":
        return category, subcategory, discretionary
    return classify(merchant, [r for r in rules if r["match_type"] == "merchant"])


def make_id(source: str, row_number: int, description: str, date, amount: Decimal) -> str:
    return f"{source}-{row_number:06d}"


def read_credit_card(path: Path):
    rows = load_csv(path)
    required = {"Transaction Date", "Post Date", "Description", "Category", "Type", "Amount"}
    missing = required - set(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"Credit-card CSV missing columns: {sorted(missing)}")

    result = []
    for i, r in enumerate(rows, 2):
        tx_date = parse_date(r["Transaction Date"])
        post_date = parse_date(r["Post Date"])
        amount = money(r["Amount"])
        tx_type = clean_text(r["Type"])
        desc = clean_text(r["Description"])

        if tx_type == "Payment":
            flow = "Transfer"
        elif tx_type in ("Return", "Adjustment"):
            flow = "Refund"
        elif amount < 0:
            flow = "Expense"
        elif amount > 0:
            flow = "Other"
        else:
            flow = "Other"

        result.append({
            "transaction_date": tx_date,
            "post_date": post_date,
            "source": "credit_card",
            "source_id": make_id("CC", i, desc, tx_date, amount),
            "merchant": normalize_merchant(desc),
            "description": desc,
            # Expenses are negative in the source. Convert to positive
            # spending magnitude; refunds remain negative.
            "amount": -amount if flow == "Expense" else amount,
            "flow_type": flow,
            "original_category": clean_text(r["Category"]),
            "card_type": tx_type,
            "amazon_order_id": "",
            "amazon_product": "",
            "amazon_quantity": "",
            "review": "",
        })
    return result


def read_bank(path: Path):
    rows = load_csv(path)
    required = {"Details", "Posting Date", "Description", "Amount"}
    missing = required - set(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"Bank CSV missing columns: {sorted(missing)}")

    result = []
    for i, r in enumerate(rows, 2):
        tx_date = parse_date(r["Posting Date"])
        amount = money(r["Amount"])
        details = clean_text(r["Details"]).upper()
        desc = clean_text(r["Description"])

        desc_u = desc.upper()
        # Detect account movements before applying generic debit/credit logic.
        # These are not purchases even when the bank export marks them as debits.
        if any(pattern in desc_u for pattern in BANK_TRANSFER_PATTERNS):
            flow = "Transfer"
        elif amount < 0 and any(pattern in desc_u for pattern in BANK_CARD_PAYMENT_PATTERNS):
            flow = "Transfer"
        elif details == "CREDIT" or amount > 0:
            flow = "Income"
        elif details == "DEBIT" and amount < 0:
            flow = "Expense"
        elif details == "CHECK" and amount < 0:
            flow = "Expense"
        else:
            flow = "Other"

        result.append({
            "transaction_date": tx_date,
            "post_date": tx_date,
            "source": "bank",
            "source_id": make_id("BANK", i, desc, tx_date, amount),
            "merchant": normalize_merchant(desc),
            "description": desc,
            "amount": -amount if flow == "Expense" else amount,
            "flow_type": flow,
            "original_category": clean_text(r.get("Type", "")),
            "card_type": "",
            "amazon_order_id": "",
            "amazon_product": "",
            "amazon_quantity": "",
            "review": "",
        })
    return result


def read_amazon(path: Path):
    rows = load_csv(path)
    required = {
        "Order Date", "Order ID", "Original Quantity", "Payment Method Type",
        "Product Name", "Shipment Item Subtotal", "Shipment Item Subtotal Tax",
        "Shipping Charge", "Total Discounts", "Total Amount"
    }
    missing = required - set(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"Amazon CSV missing columns: {sorted(missing)}")

    # Keep individual items, then aggregate each order for matching.
    items = []
    for i, r in enumerate(rows, 2):
        order_date = parse_date(r["Order Date"])
        order_id = clean_text(r["Order ID"])
        product = clean_text(r["Product Name"])
        total = money(r["Total Amount"])
        qty = clean_text(r["Original Quantity"])

        items.append({
            "row": i,
            "order_date": order_date,
            "order_id": order_id,
            "product": product,
            "quantity": qty,
            "total": total,
            "payment_method": clean_text(r["Payment Method Type"]),
        })

    orders = defaultdict(list)
    for item in items:
        orders[item["order_id"]].append(item)

    order_summaries = []
    for order_id, its in orders.items():
        order_summaries.append({
            "order_id": order_id,
            "date": min(x["order_date"] for x in its),
            "total": sum((x["total"] for x in its), Decimal("0")),
            "payment_methods": " | ".join(sorted(set(x["payment_method"] for x in its))),
            "items": its,
        })
    return order_summaries


def match_amazon_to_card(card_rows, amazon_orders, window_days, tolerance):
    """
    Conservative matching:
      * only negative card sales
      * amount must match Amazon order total within tolerance
      * order date must be within +/- window_days
      * ambiguous matches are NOT automatically assigned

    Matched Amazon orders replace the generic Amazon card transaction
    with one row per Amazon item, preventing double counting.
    """
    amazon_cards = [
        r for r in card_rows
        if r["flow_type"] == "Expense"
        and "AMAZON" in r["merchant"]
    ]

    candidates = []
    used_cards = set()
    matches = []

    for order in sorted(amazon_orders, key=lambda x: x["date"]):
        possible = []
        for idx, card in enumerate(amazon_cards):
            if idx in used_cards:
                continue
            days = abs((card["transaction_date"] - order["date"]).days)
            if days <= window_days and abs(card["amount"] - order["total"]) <= tolerance:
                possible.append((idx, card, days))

        if len(possible) == 1:
            idx, card, days = possible[0]
            used_cards.add(idx)
            matches.append((order, card, days))
        elif len(possible) > 1:
            # Leave ambiguous matches alone.
            candidates.append((order["order_id"], "ambiguous", len(possible)))
        else:
            candidates.append((order["order_id"], "unmatched", 0))

    return matches, candidates


def build_output(card_rows, bank_rows, amazon_orders, rules, args):
    matches, amazon_unmatched = match_amazon_to_card(
        card_rows,
        amazon_orders,
        args.amazon_window,
        money(args.amazon_tolerance),
    )

    matched_card_ids = {card["source_id"] for _, card, _ in matches}
    output = []

    # Normal credit-card and bank transactions.
    for r in card_rows + bank_rows:
        if r["source"] == "credit_card" and r["source_id"] in matched_card_ids:
            continue

        if r["flow_type"] in ("Expense", "Refund"):
            text_for_classification = f'{r["merchant"]} {r["description"]}'
            category, subcategory, discretionary = classify(text_for_classification, rules)

            # Credit-card payments and other non-expense transactions remain
            # in the data, but are classified separately.
        else:
            category, subcategory, discretionary = "Transfer/Income", r["flow_type"], "No"

        output.append({
            **{k: r.get(k, "") for k in [
                "transaction_date", "post_date", "source", "source_id",
                "merchant", "description", "amount", "flow_type",
                "original_category", "amazon_order_id", "amazon_product",
                "amazon_quantity", "review"
            ]},
            "category": category,
            "subcategory": subcategory,
            "discretionary": discretionary,
        })

    # Replace matched generic Amazon charge with individual Amazon items.
    for order, card, days in matches:
        for n, item in enumerate(order["items"], 1):
            category, subcategory, discretionary = classify_amazon_item(
                card["merchant"], item["product"], rules
            )

            # Allocate the card charge proportionally to Amazon item totals.
            # This makes the itemized rows sum exactly to the card transaction.
            if order["total"] != 0:
                allocated = (card["amount"] * item["total"] / order["total"]).quantize(Decimal("0.01"))
            else:
                allocated = Decimal("0.00")

            output.append({
                "transaction_date": item["order_date"],
                "post_date": card["post_date"],
                "source": "amazon_matched",
                "source_id": f'AMZ-{order["order_id"]}-{n}',
                "merchant": "AMAZON",
                "description": item["product"],
                "amount": allocated,
                "flow_type": "Expense",
                "category": category,
                "subcategory": subcategory,
                "discretionary": discretionary,
                "amazon_order_id": order["order_id"],
                "amazon_product": item["product"],
                "amazon_quantity": item["quantity"],
                "original_category": "",
                "review": "",
            })

    # Add a review marker to unmatched/ambiguous Amazon card charges.
    unmatched_order_ids = {x[0] for x in amazon_unmatched}
    for r in output:
        if (
            r["source"] == "credit_card"
            and r["flow_type"] == "Expense"
            and "AMAZON" in r["merchant"]
        ):
            r["review"] = "Amazon charge not matched to an order"

    # Sort chronologically.
    output.sort(key=lambda r: (r["transaction_date"], r["post_date"], r["source_id"]))

    # Convert dates and Decimal values for CSV.
    for r in output:
        r["transaction_date"] = r["transaction_date"].isoformat()
        r["post_date"] = r["post_date"].isoformat()
        r["amount"] = f'{r["amount"]:.2f}'

    return output, matches, amazon_unmatched


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_match_audit(path: Path, matches, unmatched):
    fields = ["status", "amazon_order_id", "amazon_date", "amazon_total",
              "card_source_id", "card_date", "card_amount", "days_apart"]
    rows = []

    for order, card, days in matches:
        rows.append({
            "status": "matched",
            "amazon_order_id": order["order_id"],
            "amazon_date": order["date"].isoformat(),
            "amazon_total": f'{order["total"]:.2f}',
            "card_source_id": card["source_id"],
            "card_date": card["transaction_date"].isoformat(),
            "card_amount": f'{card["amount"]:.2f}',
            "days_apart": days,
        })

    for order_id, status, count in unmatched:
        rows.append({
            "status": status,
            "amazon_order_id": order_id,
            "amazon_date": "",
            "amazon_total": "",
            "card_source_id": "",
            "card_date": "",
            "card_amount": "",
            "days_apart": "",
        })

    write_csv(path, rows, fields)


def main():
    args = parse_args()
    rules = load_rules(args.rules)

    card_rows = read_credit_card(args.credit_card)
    bank_rows = read_bank(args.bank)
    amazon_orders = read_amazon(args.amazon)

    output, matches, unmatched = build_output(
        card_rows, bank_rows, amazon_orders, rules, args
    )

    write_csv(args.output, output, OUTPUT_FIELDS)

    audit_path = args.output.with_name("amazon_matches.csv")
    write_match_audit(audit_path, matches, unmatched)

    print(f"Wrote {len(output):,} transactions to {args.output}")
    print(f"Matched {len(matches):,} Amazon orders.")
    print(f"Amazon orders needing review: {len(unmatched):,}")
    print(f"Wrote Amazon audit to {audit_path}")
    print(f"Rules file: {args.rules}")


if __name__ == "__main__":
    main()
