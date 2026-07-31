def initials(names, limit):
    return " ".join(name[0].upper() for name in names[:limit])
