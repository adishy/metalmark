"""Install-time credentials: generated once, never regenerated, never printed.

``app.bootstrap_secrets`` is what makes ``deploy/docker-compose.yaml`` a file with no
credential in it — the deployment runs it as a one-shot container before anything
that needs a secret, so every test here is about the two ways that could go
wrong rather than about whether it writes a file.

The first is **regeneration**. The Fernet key decrypts the household's stored
bank credentials, so a second run that rewrote it would not rotate anything: it
would make every existing one undecryptable, silently, and the damage would
surface at the next sync rather than here. A test that only asserts the files are
created passes for a version that overwrites them every restart, so the
idempotency test is the one that matters.

The second is **disclosure**. The output of this runs in an install log and in CI
logs, so the assertion is not that it prints something sensible — it is that no
generated value appears in what it prints, which is the same discipline
``scripts/secret_scan.sh`` enforces on the repository.
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

import pytest

from app.bootstrap_secrets import GENERATORS, MODES, generate

pytestmark = pytest.mark.unit


def test_generates_all_three_credentials(tmp_path: Path) -> None:
    created = generate(tmp_path)

    assert created == {
        "metalmark_secret_key": True,
        "postgres_password": True,
        "app_db_password": True,
    }
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(GENERATORS)


def test_the_key_is_a_fernet_key_and_the_passwords_are_not_the_key(tmp_path: Path) -> None:
    """Each credential has to be usable by the thing that consumes it.

    The key is checked by *decoding* it — 32 bytes of urlsafe base64, which is
    what `Fernet` accepts — rather than by matching its length, and the passwords
    are checked for the characters that would break the DSN built in
    `settings._dsn` or the `CREATE ROLE ... PASSWORD '…'` statement in migration
    0001. That second check is the real one: a password containing a quote is not
    a password that fails to authenticate, it is a migration that fails to parse.
    """
    generate(tmp_path)

    raw = (tmp_path / "metalmark_secret_key").read_text()
    assert len(base64.urlsafe_b64decode(raw)) == 32

    for name in ("postgres_password", "app_db_password"):
        value = (tmp_path / name).read_text()
        assert len(value) >= 32
        assert not set(value) & set("'\"@:/\\% \n"), value


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    """The property the whole design rests on: restarting is not rotating."""
    generate(tmp_path)
    before = {p.name: p.read_text() for p in tmp_path.iterdir()}

    created = generate(tmp_path)

    assert created == dict.fromkeys(GENERATORS, False)
    assert {p.name: p.read_text() for p in tmp_path.iterdir()} == before


def test_a_missing_file_is_regenerated_but_its_neighbours_are_not(tmp_path: Path) -> None:
    """Partial state is the realistic case: half a deleted volume, not all of it.

    Only the missing file is written. This is the shape that makes the failure
    recoverable — a deleted app password is re-made rather than taking the Fernet
    key down with it.
    """
    generate(tmp_path)
    key_before = (tmp_path / "metalmark_secret_key").read_text()
    (tmp_path / "app_db_password").unlink()

    created = generate(tmp_path)

    assert created == {
        "metalmark_secret_key": False,
        "postgres_password": False,
        "app_db_password": True,
    }
    assert (tmp_path / "metalmark_secret_key").read_text() == key_before


def test_the_fernet_key_is_not_world_readable(tmp_path: Path) -> None:
    """0600 for the key, 0644 for the Postgres owner's password — and the second
    is not an oversight.

    The database container runs as uid 999 (`postgres`), so a 0600 password file
    is one it cannot read, and the deployment fails at first boot with a
    permissions error it does not explain. 0644 is also the mode docker gives a
    mounted swarm secret. The key has no such constraint — every container that
    reads it runs as root — so it gets the tighter mode.
    """
    generate(tmp_path)

    assert MODES["metalmark_secret_key"] == 0o600
    assert (tmp_path / "metalmark_secret_key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "postgres_password").stat().st_mode & 0o777 == 0o644


def test_it_creates_the_directory_when_the_volume_is_empty(tmp_path: Path) -> None:
    """A fresh named volume is an empty directory that already exists — but the
    path is configurable, and a nested one may not."""
    nested = tmp_path / "secrets" / "inner"

    generate(nested)

    assert (nested / "metalmark_secret_key").exists()


def test_the_output_never_contains_a_generated_value(tmp_path: Path) -> None:
    """Run as a subprocess, because what is under test is what it *prints*.

    `METALMARK_SECRETS_DIR` is the only thing the module reads, and it is read at
    import time, so this cannot be an in-process test with monkeypatch — the
    import would have happened already. Subprocess is also closer to the truth:
    in the deployment this is `python -m app.bootstrap_secrets` in a container.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "app.bootstrap_secrets"],
        env={"METALMARK_SECRETS_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    # Every value, absent from both streams. `stdout` is what an install log
    # keeps; `stderr` is what an operator pastes into an issue.
    printed = proc.stdout + proc.stderr
    for path in tmp_path.iterdir():
        assert path.read_text() not in printed
    # …and the run is visible in that log, or the check above would pass for a
    # program that printed nothing at all.
    assert printed.count("generated") == 3
