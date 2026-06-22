"""A tiny arithmetic module used as a harness-micro fixture.

Deterministic and self-contained: the micro-tests grep/read/edit named symbols
here. Keep line numbers stable — several tests assert on specific lines.
"""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def target_func(x):
    # The grep→read tier-2 test locates this definition line, then reads around it.
    return x * x + 1


def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b
