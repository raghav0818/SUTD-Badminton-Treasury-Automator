"""Small shared formatting helpers used across handlers and jobs."""


def money(cents: int) -> str:
    """Render integer cents as a Singapore-dollar string, e.g. 2000 -> 'S$20.00'."""
    return f"S${cents / 100:.2f}"
