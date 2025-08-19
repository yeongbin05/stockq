# stocks/utils.py
import hashlib, urllib.parse

def normalize_url(url: str) -> str:
    if not url:
        return ""
    u = urllib.parse.urlsplit(url)
    # utm_* 제거
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    q = [(k, v) for (k, v) in q if not k.lower().startswith("utm_")]
    query = urllib.parse.urlencode(sorted(q))
    netloc = u.netloc.lower()
    path = u.path or "/"
    return urllib.parse.urlunsplit((u.scheme, netloc, path, query, ""))

def make_url_hash(url: str) -> str:
    norm = normalize_url(url)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()
