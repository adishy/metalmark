"""Reading credentials from files: docker's `_FILE` convention, and its edges.

`deploy/docker-compose.yaml` generates every credential it needs into a volume, so
`Settings` has to be able to read all three from a path — the Fernet key could
already, and the two database passwords are what this covers. The interesting
cases are not the happy path but the three ways a file-based secret goes wrong in
practice: a trailing newline, a file that is set but unreadable, and a stale
environment variable sitting next to a correct file.

`get_settings()` is `lru_cache`d, so every test here constructs `Settings()`
directly. A test that went through the cache would pass or fail depending on
which test ran first.
"""

from __future__ import annotations

import pytest

from app.settings import Settings

pytestmark = pytest.mark.unit

#: Every variable this module's subject reads, so a stray one in the ambient
#: environment cannot make a test pass for the wrong reason.
_CLEARED = (
    "METALMARK_SECRET_KEY",
    "METALMARK_SECRET_KEY_FILE",
    "POSTGRES_PASSWORD",
    "POSTGRES_PASSWORD_FILE",
    "APP_DB_PASSWORD",
    "APP_DB_PASSWORD_FILE",
)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _CLEARED:
        monkeypatch.delenv(name, raising=False)


def test_a_password_can_come_from_a_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "postgres_password"
    path.write_text("generated-by-secret-init")
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", str(path))

    settings = Settings()

    assert settings.postgres_password == "generated-by-secret-init"
    # …and it is what the DSN actually carries, since that is the only place the
    # value is used and a field that loads but does not reach the connection
    # string is a field that does nothing.
    assert "generated-by-secret-init" in settings.owner_dsn


def test_the_file_wins_over_the_environment_variable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both set is the realistic state on a machine that has ever run the dev
    stack: `.env` carries a password, and the deployment generated a different
    one. The file is the deliberate one — it is what a container was pointed at —
    so it is the one that has to win. The reverse makes a deployment fail
    authentication against a database it created itself."""
    path = tmp_path / "postgres_password"
    path.write_text("from-the-file")
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", str(path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "from-the-environment")

    assert Settings().postgres_password == "from-the-file"


def test_a_trailing_newline_is_stripped(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret written by a person ends in a newline, and the Postgres image
    strips it too — both sides read this same file, so both have to agree. Left
    in, it fails as `password authentication failed`, which points at the
    database rather than at one stray byte."""
    path = tmp_path / "app_db_password"
    path.write_text("hunter2hunter2\n")
    monkeypatch.setenv("APP_DB_PASSWORD_FILE", str(path))

    assert Settings().app_db_password == "hunter2hunter2"


def test_an_unreadable_file_leaves_the_environment_value_alone(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set but missing — a volume that was not mounted, a path that was never
    created. Blanking the password here would turn a mount mistake into
    `password authentication failed`, which sends an operator to look at the
    database; keeping the environment value leaves whatever they configured
    working, and a genuinely wrong value fails as loudly either way."""
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", str(tmp_path / "not-mounted"))
    monkeypatch.setenv("POSTGRES_PASSWORD", "still-here")

    assert Settings().postgres_password == "still-here"


def test_an_empty_file_also_leaves_it_alone(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the same rule: `secret-init` writes with `O_EXCL`, so an
    empty file is not something it produces — but a volume that was truncated, or
    a `touch`ed placeholder, would otherwise silently set the password to the
    empty string, and Postgres accepts an empty password in some auth modes."""
    path = tmp_path / "postgres_password"
    path.write_text("")
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", str(path))
    monkeypatch.setenv("POSTGRES_PASSWORD", "still-here")

    assert Settings().postgres_password == "still-here"


def test_the_secret_key_still_raises_when_it_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one credential with no fallback, and it has to stay that way.

    `secret_key` is a property that raises rather than a field with a default, so
    a deployment that lost its key fails at the moment it tries to encrypt —
    loud, and at the right place. A default here would mean silently encrypting
    bank credentials with a string that is in the repository.
    """
    monkeypatch.setenv("METALMARK_SECRET_KEY_FILE", "/nonexistent/key")

    with pytest.raises(RuntimeError, match="METALMARK_SECRET_KEY"):
        _ = Settings().secret_key
