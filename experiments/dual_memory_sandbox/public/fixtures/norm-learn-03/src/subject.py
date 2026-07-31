def canonical_phone(value):
    """Return a punctuation-free phone number."""
    return value.strip().replace(" ", "")
