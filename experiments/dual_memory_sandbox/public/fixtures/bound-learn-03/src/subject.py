def collect_tags(tags, limit):
    return [tag.lower() for tag in tags][:limit]
