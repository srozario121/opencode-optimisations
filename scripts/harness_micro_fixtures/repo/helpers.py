"""Small helper functions for the harness-micro fixture app."""


def clamp(value, low, high):
    return max(low, min(value, high))


def is_even(n):
    return n % 2 == 0
