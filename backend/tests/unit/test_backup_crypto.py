"""The backup filter: round trip, tamper detection, and the key it refuses to run without.

``scripts/backup.py`` is the only thing standing between a full dump of the
household's ledger and a file on disk, so the tests that matter are the negative
ones: a flipped byte, a truncated file, a wrong key and an unset key must all
fail loudly, and the ciphertext must not contain the plaintext. A round-trip
test alone passes for a filter that writes the plaintext through unchanged, so
it is the least interesting test in this file.

The filter is imported by path and driven with bytes, which is the whole reason
it is a filter instead of something that talks to Postgres: none of this needs a
database. What ``scripts/backup.sh`` and ``scripts/restore.sh`` actually consume
is the process contract — the exit status, and stdout being *empty* on a failure
— so those are checked by running it as a subprocess.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

CHUNK = 1 << 20
#: 64 characters, so it stays off the module's weak-key warning path.
KEY = "0123456789abcdef" * 4
OTHER_KEY = "fedcba9876543210" * 4


def _script_path() -> Path:
    """``scripts/backup.py``, from wherever this file happens to be running.

    A walk up the tree rather than a fixed number of ``..``: on the host this
    resolves to ``<repo>/scripts/backup.py``, and inside the api container —
    which mounts ``backend/`` at ``/app``, so the repo root *is* ``/`` — it
    resolves to ``/scripts/backup.py``, the same path CI mounts. Hardcoding
    either answer fails in the other place with a bare FileNotFoundError, which
    reads as a broken test rather than a missing mount.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "scripts" / "backup.py"
        if candidate.is_file():
            return candidate
    raise AssertionError(
        "scripts/backup.py not found in any parent directory. In the api "
        "container this file lives at /scripts, which docker-compose.yml mounts "
        "read-only; without that mount, run pytest via "
        "`docker compose run --rm -T -v \"$PWD/scripts\":/scripts:ro api ...`."
    )


SCRIPT = _script_path()
_FRAME_OVERHEAD = 4 + 16  # length prefix + tag


def _import_backup() -> ModuleType:
    spec = importlib.util.spec_from_file_location("metalmark_backup_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = _import_backup()


def _encrypt(plaintext: bytes, key: str = KEY) -> bytes:
    out = BytesIO()
    backup.encrypt(BytesIO(plaintext), out, backup._aes_key(key))
    return out.getvalue()


def _decrypt(archive: bytes, key: str = KEY) -> bytes:
    out = BytesIO()
    backup.decrypt(BytesIO(archive), out, backup._aes_key(key))
    return out.getvalue()


def _run(mode: str, data: bytes, key: str | None = KEY) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    if key is None:
        env.pop(backup.ENV_KEY, None)
    else:
        env[backup.ENV_KEY] = key
    return subprocess.run(
        [sys.executable, str(SCRIPT), mode],
        input=data,
        capture_output=True,
        env=env,
        check=False,
    )


# --------------------------------------------------------------- round trips


def test_text_round_trips() -> None:
    plaintext = b"COPY public.accounts (id, name) FROM stdin;\n" * 10
    assert _decrypt(_encrypt(plaintext)) == plaintext


def test_binary_round_trips() -> None:
    # A custom-format dump is not text: it holds binary COPY data, and a filter
    # that quietly encoded or newline-translated it would corrupt every row.
    plaintext = bytes(range(256)) * 40 + b"\x00\xff"
    assert _decrypt(_encrypt(plaintext)) == plaintext


def test_empty_input_round_trips() -> None:
    # The terminator is written even for nothing: the archive is still a valid
    # archive, and an empty dump restores as an empty schema rather than as a
    # corrupt file.
    archive = _encrypt(b"")
    assert len(archive) == len(backup.MAGIC) + backup.NONCE_LEN + _FRAME_OVERHEAD
    assert _decrypt(archive) == b""


def test_a_large_input_spans_several_chunks() -> None:
    # 2 MiB + 4 bytes: two full frames and a short third, so the chunk loop —
    # the part a single AESGCM call would not have — actually runs. The size is
    # asserted exactly, because "it round-tripped" is also true of one frame.
    plaintext = bytes(range(256)) * (2 * CHUNK // 256) + b"tail"
    archive = _encrypt(plaintext)
    expected = (
        len(backup.MAGIC)
        + backup.NONCE_LEN
        + 2 * (CHUNK + _FRAME_OVERHEAD)  # two full frames
        + (4 + _FRAME_OVERHEAD)  # the 4-byte remainder
        + _FRAME_OVERHEAD  # the terminator
    )
    assert len(archive) == expected
    assert _decrypt(archive) == plaintext


def test_the_ciphertext_does_not_contain_the_plaintext() -> None:
    # The entire point. A distinctive marker makes the failure mode legible:
    # if this ever fails, the archive is a plaintext dump with a header.
    marker = b"ACCOUNT-4242-BALANCE-1337.42"
    archive = _encrypt(marker * 1000)
    assert marker not in archive
    assert b"4242" not in archive


def test_every_archive_is_different() -> None:
    # A random base nonce, not a fixed one: two backups of the same database
    # must not be byte-identical, or a leak of one archive's key material tells
    # an attacker something about the other.
    plaintext = b"same bytes both times"
    assert _encrypt(plaintext) != _encrypt(plaintext)


# ------------------------------------------------------------------ tampering


#: One archive, built once, so the parametrisation below can cover every byte
#: offset in it. Framing depends only on the plaintext length, so the offsets
#: are stable across runs even though the nonce is random.
SAMPLE = _encrypt(b"frame one " * 12)


@pytest.mark.parametrize("offset", range(len(SAMPLE)))
def test_flipping_any_single_byte_is_detected(offset: int) -> None:
    # Every byte, not a sampled few: the magic, the base nonce, the length
    # prefixes, the ciphertext, the tags and the terminator are each load
    # bearing, and a scheme that authenticates only the payload would pass a
    # test that flipped one byte in the middle.
    tampered = bytearray(SAMPLE)
    tampered[offset] ^= 0x01
    with pytest.raises(backup.ArchiveError):
        _decrypt(bytes(tampered))


@pytest.mark.parametrize(
    "cut",
    [1, len(backup.MAGIC), len(backup.MAGIC) + 4, len(SAMPLE) - 1, len(SAMPLE) - _FRAME_OVERHEAD],
    ids=["one byte", "mid-magic", "mid-nonce", "last byte gone", "terminator gone"],
)
def test_truncation_is_detected(cut: int) -> None:
    # `terminator gone` is the case a chunked format gets wrong: the cut lands
    # exactly on a frame boundary, every remaining frame still authenticates,
    # and without the final frame nothing says the dump was meant to continue.
    with pytest.raises(backup.ArchiveError):
        _decrypt(SAMPLE[:cut])


def test_reordering_frames_is_detected() -> None:
    # Two full frames are interchangeable bytes with tags that verify on their
    # own, so without the frame counter in the AAD a swap is silent — and a
    # restore of the swapped dump puts one table's rows inside another's.
    plaintext = bytes(range(256)) * (2 * CHUNK // 256)
    archive = _encrypt(plaintext)
    header = len(backup.MAGIC) + backup.NONCE_LEN
    frame = CHUNK + _FRAME_OVERHEAD
    swapped = (
        archive[:header]
        + archive[header + frame : header + 2 * frame]
        + archive[header : header + frame]
        + archive[header + 2 * frame :]
    )
    assert swapped != archive
    with pytest.raises(backup.ArchiveError):
        _decrypt(swapped)


def test_trailing_bytes_after_the_terminator_are_detected() -> None:
    # Two archives concatenated (`cat a.mmbak b.mmbak > c.mmbak`) must not
    # decrypt as the first one and look fine.
    with pytest.raises(backup.ArchiveError):
        _decrypt(SAMPLE + SAMPLE)


def test_a_wrong_key_fails() -> None:
    with pytest.raises(backup.ArchiveError):
        _decrypt(_encrypt(b"the ledger"), key=OTHER_KEY)


def test_something_that_is_not_an_archive_fails() -> None:
    with pytest.raises(backup.ArchiveError):
        _decrypt(b"this is a pg_dump, not an archive")


# ------------------------------------------------------------------ the CLI


def test_cli_round_trips_over_a_pipe() -> None:
    # Through stdin/stdout, the way the shell scripts use it — a redirect rather
    # than a file argument is the only interface there is.
    plaintext = b"pipe in, pipe out\x00\x01\x02"
    encrypted = _run("encrypt", plaintext)
    assert encrypted.returncode == 0
    assert encrypted.stdout != plaintext
    decrypted = _run("decrypt", encrypted.stdout)
    assert decrypted.returncode == 0
    assert decrypted.stdout == plaintext


def test_cli_refuses_a_flipped_byte_and_writes_nothing() -> None:
    # The property the two-pass decrypt exists for: stdout is empty, not a
    # prefix of the ledger. A partial dump that exists on disk is the file an
    # operator restores from at 3am, because the good one does not.
    archived = _run("encrypt", b"the ledger" * 500).stdout
    tampered = bytearray(archived)
    tampered[len(tampered) // 2] ^= 0x01
    failed = _run("decrypt", bytes(tampered))
    assert failed.returncode != 0
    assert failed.stdout == b""
    assert b"authentication" in failed.stderr


def test_cli_refuses_a_truncated_archive() -> None:
    archived = _run("encrypt", b"the ledger" * 500).stdout
    failed = _run("decrypt", archived[:-1])
    assert failed.returncode != 0
    assert failed.stdout == b""


def test_cli_refuses_a_wrong_key() -> None:
    archived = _run("encrypt", b"the ledger" * 500).stdout
    failed = _run("decrypt", archived, key=OTHER_KEY)
    assert failed.returncode != 0
    assert failed.stdout == b""


def test_cli_refuses_to_run_without_a_key() -> None:
    # Refusing *before* stdin is read is the point: the caller redirects stdout
    # to the archive path, and a zero-byte file left there looks like a backup
    # until the day it is opened.
    result = _run("encrypt", b"the ledger", key=None)
    assert result.returncode != 0
    assert result.stdout == b""
    assert backup.ENV_KEY.encode() in result.stderr


def test_cli_refuses_to_run_with_an_empty_key() -> None:
    # `METALMARK_BACKUP_KEY=` in a shell is a mistake, not a key.
    result = _run("encrypt", b"the ledger", key="")
    assert result.returncode != 0
    assert result.stdout == b""


def test_cli_rejects_an_unknown_mode() -> None:
    result = _run("rotate", b"")
    assert result.returncode != 0
    assert result.stdout == b""


def test_cli_runs_with_a_short_key_but_warns_about_it() -> None:
    # Not a refusal: an operator who wants a short key gets one, and gets told
    # what it costs. Silently accepting it would be the dishonest version.
    result = _run("encrypt", b"the ledger", key="hunter2")
    assert result.returncode == 0
    assert result.stdout != b""
    assert b"hunter2 is" not in result.stderr  # the key itself never reaches a log
    assert b"entropy" in result.stderr
