def normalize_slug(value):
    """Return a lowercase dash-separated slug."""
    return value.strip().lower().replace(" ", "-")
