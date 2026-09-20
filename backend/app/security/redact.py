"""Redaction for text that might carry a credential into a log (ADR-0016).

The SimpleFIN access URL is a Basic-auth URL — ``https://user:pass@host/…`` — and
it is the one secret here that reaches a log line without anyone deciding to put
it there. ``httpx`` logs every request URL at INFO, and ``HTTPStatusError``
stringifies the request it failed on. So "remember not to log it" is not a rule
that can hold; it has to be a function that every message passes through, in the
one place that can actually guarantee the credential is gone.

Two layers, because they fail differently:

* **Named secrets** are replaced literally. This is the precise layer: it removes
  the whole URL, including a path no pattern would recognize as secret.
* **Generic userinfo stripping** is the backstop. A URL nobody told us about — or
  one reassembled inside a library's repr — still loses its credentials.

Neither layer substitutes for not building the message in the first place. This
is what makes a mistake survivable, not what makes one acceptable: a caller that
knows the URL should still log a status code and a host instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PLACEHOLDER = "[redacted]"

# Long enough that a real diagnostic survives, short enough that a leaked payload
# cannot ride along. Error text is read by a human in a dashboard, not parsed.
MAX_CHARS = 500

# ``scheme://userinfo@`` — the userinfo is everything up to the last ``@`` before
# the first ``/``. Excluding ``/`` from the class is what keeps a URL whose *path*
# contains an ``@`` from matching: ``https://host/a@b`` has no userinfo, and this
# pattern correctly declines to invent one.
_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s@]+@")


def sanitize(text: str, *, secrets: Iterable[str] = ()) -> str:
    """Return ``text`` with credentials removed, then bounded to ``MAX_CHARS``.

    Order matters and is not arbitrary: **redact, then truncate**. Truncating
    first could cut a secret short enough that the literal match no longer
    applies, and the surviving fragment would be a partial credential — which is
    still a credential.

    ``secrets`` should carry the access URL and the setup token where the caller
    has them. Empty strings are ignored, since replacing them would pepper the
    text with placeholders and destroy the diagnostic for no gain.
    """
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, PLACEHOLDER)
    text = _USERINFO.sub(r"\g<scheme>" + PLACEHOLDER + "@", text)
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 1] + "…"
    return text
