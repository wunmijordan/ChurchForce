import re


def normalize(value: str):
    """
    Normalize strings for flexible matching.
    """
    return re.sub(r"[\s\-\_]+", "", value or "").lower()