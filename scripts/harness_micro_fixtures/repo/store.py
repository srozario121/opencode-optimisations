"""Order-pricing helpers for the harness-micro fixture (v3 navigation target).

Deliberately many small, SIMILAR functions so locating the *right* one is
non-trivial (a model must read/grep to disambiguate, not guess from the name).
Keep line numbers and signatures stable — several tests assert on specific
symbols and on the byte-identity of the functions they must NOT touch.
"""

TAX_RATE = 0.2
FREE_SHIPPING_THRESHOLD = 50
DEFAULT_CURRENCY = "USD"


def apply_tax(amount):
    """Add sales tax to an amount."""
    return amount * (1 + TAX_RATE)


def apply_discount(amount, pct):
    """Reduce an amount by a percentage discount."""
    return amount * (1 - pct / 100)


def apply_coupon(amount, value):
    """Subtract a fixed coupon value from an amount."""
    return max(0, amount - value)


def apply_surcharge(amount, fee):
    """Add a flat surcharge fee to an amount."""
    return amount + fee


def shipping_cost(amount):
    """Return 0 if the order qualifies for free shipping, else a flat 5."""
    if amount >= FREE_SHIPPING_THRESHOLD:
        return 0
    return 5


def round_money(amount):
    """Round a monetary amount to 2 decimal places."""
    return round(amount, 2)


def format_price(amount):
    """Format an amount as a currency-prefixed price string."""
    return f"{DEFAULT_CURRENCY} {round_money(amount):.2f}"


def order_total(amount, pct):
    """Final order total: discount, then tax, then add shipping."""
    discounted = apply_discount(amount, pct)
    taxed = apply_tax(discounted)
    return taxed + shipping_cost(taxed)
