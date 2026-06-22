"""Utility helpers, located by the glob→read tier-2 test (name starts 'util')."""


def slugify(text):
    return text.strip().lower().replace(" ", "-")
