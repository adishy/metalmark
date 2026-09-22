"""Is the session cookie `Secure`, and who decides (ADR-0042).

`Secure` is not a detail: on a `http://` origin that is not a secure context a
browser drops the cookie, so the login appears to succeed and every request after
it is anonymous. The default has to keep marking it — that is what makes the
TLS deployment safe by doing nothing — and the one deployment that cannot have TLS
has to be able to say so, because otherwise the app is simply unusable there.

Two halves, and both are tested here rather than through the API: the setting's
resolution, and the header the function that sets the cookie actually emits. They
are the two places a regression could hide, and neither needs a database — the
gate that boots the real deployment checks the same claim end to end, over TLS and
over plain HTTP (`scripts/verify.sh`, the `prod` gate).

`Settings()` is constructed directly, as in `test_settings_secrets.py`:
`get_settings()` is `lru_cache`d, so a test that went through the cache would pass
or fail depending on which test ran first.
"""

from __future__ import annotations

import pytest
from fastapi import Response
from pydantic import ValidationError

from app.api.auth import _set_session_cookie
from app.settings import Settings

pytestmark = pytest.mark.unit

#: Every variable these tests' subject reads, so an ambient value can neither make
#: one pass for the wrong reason nor silently change what "unset" means.
_CLEARED = ("METALMARK_ENV", "METALMARK_SESSION_COOKIE_SECURE")


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _CLEARED:
        monkeypatch.delenv(name, raising=False)


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()


def _set_cookie(monkeypatch: pytest.MonkeyPatch, **env: str) -> str:
    """The `Set-Cookie` header the app really sends, under `env`."""
    settings = _settings(monkeypatch, **env)
    monkeypatch.setattr("app.api.auth.get_settings", lambda: settings)
    response = Response()
    _set_session_cookie(response, "session-token")
    return response.headers["set-cookie"]


# ---- what the setting resolves to ------------------------------------------


def test_prod_marks_the_cookie_secure_without_being_asked(monkeypatch):
    """The default that matters: a deployment does not have to know this setting
    exists to get the protection."""
    assert _settings(monkeypatch, METALMARK_ENV="prod").cookie_is_secure is True


def test_dev_does_not(monkeypatch):
    """The dev stack is served over plain http on `localhost`, and the cookie has
    to survive there — this is the half of the old derivation that stays."""
    assert _settings(monkeypatch, METALMARK_ENV="dev").cookie_is_secure is False


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on"])
def test_an_affirmative_value_wins_over_dev(monkeypatch, value):
    settings = _settings(monkeypatch, METALMARK_ENV="dev",
                         METALMARK_SESSION_COOKIE_SECURE=value)
    assert settings.cookie_is_secure is True


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off"])
def test_a_negative_value_wins_over_prod(monkeypatch, value):
    """The whole point of the setting: a deployment with no TLS says `false` and
    a login over plain http sticks. Anything else and the app is unusable there."""
    settings = _settings(monkeypatch, METALMARK_ENV="prod",
                         METALMARK_SESSION_COOKIE_SECURE=value)
    assert settings.cookie_is_secure is False


@pytest.mark.parametrize("value", ["", "  ", "auto", "AUTO"])
def test_blank_and_auto_are_the_default_rather_than_an_opt_out(monkeypatch, value):
    """Blank is what both places an operator writes this produce: the compose file
    interpolates `${VAR:-auto}`, and an empty `.env` value is far more likely to
    mean "leave it alone" than "turn it off"."""
    settings = _settings(monkeypatch, METALMARK_ENV="prod",
                         METALMARK_SESSION_COOKIE_SECURE=value)
    assert settings.session_cookie_secure == "auto"
    assert settings.cookie_is_secure is True


def test_a_typo_is_refused_rather_than_guessed(monkeypatch):
    """`falze` must not resolve to anything. A setting whose failure mode is a
    silently weaker cookie is a setting that has to fail loudly."""
    with pytest.raises(ValidationError) as err:
        _settings(monkeypatch, METALMARK_ENV="prod",
                  METALMARK_SESSION_COOKIE_SECURE="falsy")
    assert "METALMARK_SESSION_COOKIE_SECURE" in str(err.value)


# ---- what the app actually sends -------------------------------------------


def test_the_header_carries_secure_on_a_prod_deployment(monkeypatch):
    header = _set_cookie(monkeypatch, METALMARK_ENV="prod")
    assert "Secure" in header
    assert "HttpOnly" in header and "SameSite=lax" in header


def test_the_header_does_not_carry_it_when_the_deployment_opts_out(monkeypatch):
    header = _set_cookie(monkeypatch, METALMARK_ENV="prod",
                         METALMARK_SESSION_COOKIE_SECURE="false")
    assert "Secure" not in header
    # …and it is still a session cookie, not a hole: the flags that guard it
    # against script access and cross-site requests are not what this turns off.
    assert "HttpOnly" in header and "SameSite=lax" in header
