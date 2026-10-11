"""Money helpers. Amounts are stored as integer paise; never use floats."""
import re
from decimal import Decimal

PLAIN_AMOUNT = re.compile(r"^\d+(\.\d{1,2})?$")


def parse_amount(text):
    """'118000.00' -> 11800000 (paise). Raises ValueError for anything that is not a plain positive number."""
    text = (text or "").strip()
    if not PLAIN_AMOUNT.match(text):
        raise ValueError("not a plain number")
    paise = int((Decimal(text) * 100).to_integral_value())
    if paise <= 0:
        raise ValueError("must be greater than 0")
    return paise


def format_inr(paise):
    """11800000 -> '1,18,000.00' (Indian grouping)."""
    sign = "-" if paise < 0 else ""
    rupees, p = divmod(abs(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{sign}{s}.{p:02d}"
