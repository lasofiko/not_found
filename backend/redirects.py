"""Validate legacy paths before creating an origin-relative redirect."""

import re
from urllib.parse import quote, unquote


def legacy_dashboard_location(path: str) -> str:
    # ASGI already decodes URL paths; inspect remaining escapes too, so nested
    # encodings cannot hide a leading separator or a browser-normalized backslash.
    try:
        for _ in range(4):
            if re.search(r"%(?![0-9a-fA-F]{2})", path):
                return "/"
            decoded = unquote(path, errors="strict")
            if decoded == path:
                break
            path = decoded
        else:
            return "/"
    except UnicodeDecodeError:
        return "/"
    if path.startswith("/") or "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        return "/"
    if any(part in {".", ".."} for part in path.split("/")):
        return "/"
    if path in {"", "index.html"}:
        return "/"
    if path in {"capacity", "capacity/", "capacity.html"}:
        return "/#capacity"
    return "/" + quote(path, safe="/@:-._~!$&'()*+,;=")
