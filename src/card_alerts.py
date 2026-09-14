"""Recognise a card issuer's per-transaction alert email and pull out the facts.

Brittle by nature (every bank words it differently), so every pattern is
listed here and unrecognised mail is simply not a purchase. Never stores a card
number: only the last four digits the issuer itself prints in the alert.
"""

import re

AMOUNT = r"(?P<cur>[$€£])\s?(?P<amt>\d{1,3}(?:,\d{3})*(?:\.\d{2})?)"
CURRENCIES = {"$": "USD", "€": "EUR", "£": "GBP"}

# Where a merchant name stops: end of sentence or line, or the usual next clause.
END = r"(?= on your| was | on |,|\.(?:\s|$)|\n|$)"
MERCHANT = rf"(?P<merchant>[^\n]+?){END}"

PATTERNS = [
    # Chase: "You made a $12.34 transaction with STARBUCKS"
    re.compile(rf"made an? {AMOUNT} (?:transaction|purchase) (?:with|at) {MERCHANT}", re.I),
    # Amex: "A charge of $45.00 was made at AMAZON.COM on your card ending in 1004"
    re.compile(rf"charge of {AMOUNT} (?:was|has been) (?:made|posted) (?:at|to|with) {MERCHANT}", re.I),
    # Capital One / generic: "A purchase of $8.15 at TRADER JOE'S"
    re.compile(rf"purchase of {AMOUNT} (?:at|from|with) {MERCHANT}", re.I),
    # Generic labelled: "Amount: $99.00 ... Merchant: FOO"
    re.compile(rf"Amount:\s*{AMOUNT}.*?Merchant:\s*(?P<merchant>[^\n]+)", re.I | re.S),
    # Generic: "$19.99 at NETFLIX.COM"
    re.compile(rf"{AMOUNT} (?:at|to) (?P<merchant>[A-Z0-9][^\n]{{2,60}}?){END}"),
]
LAST4 = re.compile(r"(?:ending(?: in)?|last four(?: digits)?|card \*{0,4}|x{4}|\*{4})\s*[:#]?\s*(\d{4})\b", re.I)
ALERT_HINTS = ("transaction", "purchase", "charge", "card", "payment")


def parse_card_alert(subject: str, body: str) -> dict | None:
    text = f"{subject}\n{body}"
    if not any(h in text.lower() for h in ALERT_HINTS):
        return None
    for pattern in PATTERNS:
        m = pattern.search(text)
        if m:
            amount = float(m.group("amt").replace(",", ""))
            last4 = LAST4.search(text)
            return {
                "merchant": m.group("merchant").strip().rstrip(","),
                "amount": amount,
                "currency": CURRENCIES.get(m.group("cur"), "USD"),
                "last4": last4.group(1) if last4 else None,
            }
    return None
