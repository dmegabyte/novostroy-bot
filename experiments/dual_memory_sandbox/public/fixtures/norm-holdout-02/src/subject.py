def clean_sku(value):
    """Return a canonical SKU."""
    return value.strip().upper().replace(" ", "-").replace(".", "-")
