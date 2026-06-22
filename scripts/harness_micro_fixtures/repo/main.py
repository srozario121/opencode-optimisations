"""Entry point for the fixture store app — calls into store.py.

Used by the multi-file tests: a rename in store.py must also update the import
and call sites here.
"""

from store import order_total, format_price, apply_discount


def main():
    total = order_total(120, 10)
    print(format_price(total))
    print(apply_discount(120, 10))


if __name__ == "__main__":
    main()
