def read(cache, key):
    """Return a cached value, or None when no value is present."""
    return cache.get(key)
