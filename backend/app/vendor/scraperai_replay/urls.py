"""URL helpers (vendored from ``scraperai/utils/urls.py``). Stdlib only."""
from urllib.parse import urlparse


def fix_relative_url(base_url: str, url: str) -> str:
    components = urlparse(base_url)
    base_url = components.scheme + '://' + components.netloc + '/'
    if url.startswith('http'):
        return url
    return base_url + url.lstrip('/')
