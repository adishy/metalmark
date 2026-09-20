"""The redaction primitive. Small, but it is the backstop for ADR-0016."""

from __future__ import annotations

import httpx
import pytest

from app.security.redact import MAX_CHARS, PLACEHOLDER, sanitize
from app.services.aggregator import ProviderError

ACCESS_URL = "https://alice:s3cr3t-pw@bridge.example.com/simplefin/abcdef"


def test_named_secret_is_removed_entirely() -> None:
    assert sanitize(f"failed to fetch {ACCESS_URL}", secrets=[ACCESS_URL]) == (
        f"failed to fetch {PLACEHOLDER}"
    )


def test_userinfo_is_stripped_even_when_not_named_as_a_secret() -> None:
    # The backstop case: nobody told us about this URL, so only the generic
    # rule can save it.
    cleaned = sanitize(f"GET {ACCESS_URL} -> 403")
    assert "s3cr3t-pw" not in cleaned
    assert "alice" not in cleaned
    assert "bridge.example.com" in cleaned  # the host is not a secret


def test_a_longer_secret_is_replaced_before_a_shorter_substring() -> None:
    # Replacing the short one first would leave a surviving fragment of the
    # long one, which is still a credential.
    long_secret = "https://u:pw@host.example.com/simplefin/AAAAAAAA"
    short_secret = "https://u:pw@host.example.com"
    cleaned = sanitize(f"x {long_secret} y", secrets=[short_secret, long_secret])
    assert "AAAAAAAA" not in cleaned
    assert cleaned == f"x {PLACEHOLDER} y"


def test_empty_secrets_are_ignored() -> None:
    # "" would otherwise match between every character.
    assert sanitize("hello", secrets=["", None]) == "hello"  # type: ignore[list-item]


def test_truncation_happens_after_redaction() -> None:
    # Truncating first could cut a secret short enough to survive the literal
    # match, leaving a partial credential in the log.
    padded = "x" * (MAX_CHARS * 2) + ACCESS_URL
    cleaned = sanitize(padded, secrets=[ACCESS_URL])
    assert "s3cr3t-pw" not in cleaned
    assert len(cleaned) == MAX_CHARS


def test_an_at_sign_in_a_path_is_not_treated_as_userinfo() -> None:
    # ``https://host/a@b`` has no credentials; the pattern must not invent them
    # and mangle a legitimate URL.
    url = "https://host.example.com/a@b"
    assert sanitize(url) == url


def test_provider_error_sanitizes_a_stringified_httpx_exception() -> None:
    """The realistic mistake: ``raise ProviderError(str(exc))``.

    ``httpx.HTTPStatusError`` stringifies the request it failed on, so the
    naive version of this line puts the credential in the message. The error
    class must make that survivable.
    """
    request = httpx.Request("GET", ACCESS_URL)
    response = httpx.Response(403, request=request)
    exc = httpx.HTTPStatusError("Client error '403 Forbidden'", request=request, response=response)

    error = ProviderError(f"fetch failed: {exc}")

    assert "s3cr3t-pw" not in str(error)
    assert "s3cr3t-pw" not in error.message
    assert "s3cr3t-pw" not in repr(error)


def test_provider_error_does_not_retain_the_secrets_it_was_given() -> None:
    # An exception is the object most likely to be logged or handed to an error
    # tracker, so keeping the credential on it would recreate the problem.
    error = ProviderError("boom", secrets=[ACCESS_URL])
    assert ACCESS_URL not in vars(error).values()


def test_provider_error_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError):
        ProviderError("boom", kind="nonsense")
