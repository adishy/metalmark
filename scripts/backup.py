"""Encrypt a ``pg_dump`` archive, as a filter: stdin in, stdout out.

    python scripts/backup.py encrypt < dump.pgc > dump.mmbak
    python scripts/backup.py decrypt < dump.mmbak > dump.pgc

A backup is the whole instance in one file — every account, transaction, holding
and session in the household — so ARCHITECTURE §5 requires it to be encrypted at
the destination, with the key stored **physically separate** from the dumps: a
dump and its key in the same place is a full compromise. This is the encryption
half of ``scripts/backup.sh`` and the decryption half of ``scripts/restore.sh``
and ``scripts/restore_drill.sh``.

It is a filter rather than something that talks to Postgres because the two
containers cannot each do both halves: ``api`` has ``cryptography`` but no
``pg_dump``/``psql``, and ``db`` has ``pg_dump``/``psql`` but no Python, and
adding either binary to the other's image is out of scope. The dump is produced
in ``db``, encrypted in ``api``, and the pipe is the only place the two meet.
That is also why this file is testable with no database at all — see
``backend/tests/unit/test_backup_crypto.py``, which drives these two functions
with bytes.

Framing (``‖`` is concatenation, integers are 4-byte big-endian)::

    archive    := MAGIC ‖ base_nonce ‖ frame* ‖ terminator
    frame      := length ‖ ciphertext ‖ tag        # length = len(ciphertext) + 16
    terminator := the frame whose plaintext is empty
    nonce[i]   := base_nonce XOR i                 # 96-bit field, so XOR is injective
    aad[i]     := MAGIC ‖ base_nonce ‖ i ‖ length

The MAGIC byte carries the format version, so a later change to any of this can
be refused by an old reader instead of silently misread by it.

**The counter is not optional.** GCM is the one cipher here with a catastrophic
failure mode: a repeated (key, nonce) pair leaks the XOR of the two plaintexts
*and* the authentication subkey, which turns forgery from infeasible into
routine. A dump is hundreds of MB and one GCM message cannot be (the same key
and nonce must never repeat, and 64 GiB is the ceiling on a message), so the
file has to be chunked — and the moment it is chunked, "just use the base nonce
for every chunk" is nonce reuse on every chunk after the first. For a ledger
that is a plaintext disclosure with no attacker effort worth mentioning. The
base nonce is random per archive and the counter makes each frame's nonce
unique; the same counter goes into the AAD, which is what stops frames from
being *reordered* — two full frames have tags that each verify on their own, so
without a position in the AAD an attacker can swap them and a restore silently
gets the wrong rows.

**The terminator is not decoration.** Without a final frame, an archive cut
exactly at a frame boundary decrypts to a valid-looking prefix of the dump:
every remaining frame authenticates and nothing says the file was meant to
continue. The terminator is itself an authenticated frame, so someone holding
only the ciphertext cannot forge one (an unauthenticated end marker could be
forged, and truncation would go back to being undetectable), and a stream that
ends without it is refused.

**Decryption reads the archive twice, deliberately.** Pass one authenticates
every frame and writes nothing; only then does pass two emit plaintext. A filter
cannot un-write the chunks it has already written, so a single-pass decrypt of a
tampered archive leaves a *prefix of the ledger* on stdout — and that is the
file most likely to be restored from later, precisely because it exists and the
good one does not. Reading twice costs I/O and no memory (each pass holds one
chunk at a time), which is a trade worth making against handing an operator a
plausible-looking truncated dump. It is also why ``decrypt`` needs either a
seekable input or a temporary spool: piped input is copied to an unlinked temp
file **as ciphertext**, never as plaintext, because spooling plaintext would put
the whole ledger in ``/tmp`` — the co-location §5 warns about — and the caller
already holds the ciphertext anyway.

**The key.** ``METALMARK_BACKUP_KEY`` is hashed with SHA-256 into a 32-byte
AES-256 key, the way ``app/security/crypto.py::_fernet_key_from_secret`` derives
its Fernet key from ``METALMARK_SECRET_KEY``: arbitrary high-entropy strings
work, and an operator has one mental model for both keys. What SHA-256 does not
do is *add* entropy — this is a key, not a passphrase. A short or guessable
``METALMARK_BACKUP_KEY`` makes every archive offline-brute-forceable at whatever
rate the attacker's hardware allows, and that is the operator's problem to avoid
(``openssl rand -hex 32`` is a key; ``hunter2`` is not). A slow KDF is
deliberately not used: it would imply we defend against a low-entropy key, and
we do not, so the honest thing is a warning on stderr and this paragraph.

Memory is O(chunk) in every path — a report of hundreds of MB must not decide
whether the backup fits in RAM.

Usage: exit 0 on success; 2 if invoked wrongly or without a key (nothing is read
or written in that case); 1 if the archive does not authenticate, is truncated,
or a pipe closed early. Every message goes to stderr, because stdout is the
payload and a stray byte in it corrupts the archive.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV_KEY = "METALMARK_BACKUP_KEY"

#: ``MMBK`` plus a format version byte. The version is *in* the magic rather
#: than a separate field so a reader that does not know a future version refuses
#: the file it cannot parse, instead of parsing the parts it recognises.
MAGIC = b"MMBK\x01"
NONCE_LEN = 12
TAG_LEN = 16

#: 1 MiB of plaintext per frame. Small enough that the chunk buffer is
#: irrelevant next to the dump, large enough that the 20 bytes of framing per
#: frame do not matter (they are 0.002% at this size).
CHUNK_SIZE = 1 << 20

#: The frame counter is packed into 8 bytes of AAD, so 2**64 frames is the
#: hard ceiling: 2**84 bytes of dump, ~10^13 times the largest ledger anyone
#: will have. Checked rather than assumed, because the failure it guards —
#: two frames sharing a nonce — is the one GCM cannot survive.
MAX_FRAMES = 1 << 64

#: Below this, warn on stderr. Not a refusal: the spec is a refusal to run
#: *without* a key, and refusing a key the operator has chosen and understands
#: would make the tool lie about who is in charge.
WEAK_KEY_CHARS = 32

_LENGTH = struct.Struct(">I")
_COUNTER = struct.Struct(">Q")


class ArchiveError(Exception):
    """The archive is not what it claims to be.

    Wrong key, a flipped byte, a truncated file and a forged end marker all
    land here, and the caller must not try to tell them apart before deciding
    what to do: every one of them means "the plaintext in front of you is not
    the dump that was written", and the only correct response is to stop.
    """


def _aes_key(secret: str) -> bytes:
    """SHA-256 of the operator's key string, as a 32-byte AES-256 key.

    Same move as ``_fernet_key_from_secret`` in ``app/security/crypto.py``: an
    arbitrary string must work as a key, because the alternative is an operator
    inventing a base64 encoded 32-byte value by hand and getting it subtly
    wrong. Unlike that function there is no "if it already decodes to 32 bytes,
    use it as-is" branch — Fernet needs the *text* form preserved, AES needs raw
    bytes, and a pass-through here would mean two different strings could name
    the same key while looking unrelated.
    """
    return hashlib.sha256(secret.encode()).digest()


def _nonce(base_nonce: bytes, counter: int) -> bytes:
    """The base nonce with the frame counter XORed into it.

    XOR rather than concatenation. Within an archive the counter is what does
    the work — it starts at 0 and never reaches 2**20, so no two frames can
    share a nonce. Across archives it is the random base nonce: a collision
    needs two 96-bit values to agree in every bit the counters could reach
    (``b1 ^ b2`` below the frame count, so ~2**-76 for any realistic number of
    frames), which is the ordinary birthday bound of any random-nonce scheme.
    Concatenating a small counter onto random bytes has the same bound *until*
    the counter field wraps, at which point the nonces repeat, silently, and
    GCM's one unforgivable failure is back.
    """
    return (int.from_bytes(base_nonce, "big") ^ counter).to_bytes(NONCE_LEN, "big")


def _aad(base_nonce: bytes, counter: int, length: int) -> bytes:
    """What a frame is bound to, authenticated but not encrypted.

    The counter is the load-bearing part: it is what makes a frame's position in
    the archive part of what its tag proves, so frames cannot be reordered or
    repeated. The length is included because it is read before the tag is
    checked — binding it means a tampered length fails the tag rather than
    steering how much is read — and the magic and base nonce are included
    because they are the frame's own context and cost nothing.
    """
    return MAGIC + base_nonce + _COUNTER.pack(counter) + _LENGTH.pack(length)


def _read_exactly(src: BinaryIO, count: int) -> bytes:
    """Up to ``count`` bytes, looping until EOF.

    A single ``read(n)`` on a pipe is allowed to return short without being at
    EOF, and a short read inside a frame would be indistinguishable from a
    truncated archive — the failure this whole module is built to catch, so it
    must not be caused by the reader.
    """
    buf = bytearray()
    while len(buf) < count:
        piece = src.read(count - len(buf))
        if not piece:
            break
        buf += piece
    return bytes(buf)


@contextmanager
def _rewindable(src: BinaryIO) -> Iterator[BinaryIO]:
    """``src`` positioned at the start of the archive, seekable for two passes.

    A shell redirect (``< dump.mmbak``) gives a seekable file for free, so the
    usual path copies nothing. A pipe does not, and the copy is of the
    *ciphertext*: what lands in ``/tmp`` is the archive the caller already has,
    not the ledger inside it.
    """
    if src.seekable():
        src.seek(0)
        yield src
        return
    with tempfile.TemporaryFile() as spool:
        shutil.copyfileobj(src, spool, CHUNK_SIZE)
        spool.seek(0)
        yield spool


def _read_header(src: BinaryIO) -> bytes:
    """Validate the magic and return the base nonce."""
    head = _read_exactly(src, len(MAGIC) + NONCE_LEN)
    if len(head) < len(MAGIC) + NONCE_LEN:
        raise ArchiveError(
            "archive is too short to hold its header — it is truncated, "
            "or this is not a MetalMark archive at all"
        )
    if head[: len(MAGIC)] != MAGIC:
        raise ArchiveError(
            f"archive does not start with {MAGIC!r}; refusing to guess what it is"
        )
    return head[len(MAGIC) :]


def _plaintext_frames(src: BinaryIO, key: bytes) -> Iterator[bytes]:
    """Yield each frame's authenticated plaintext, refusing anything malformed.

    Ordering matters here and is the reason this is a generator: a frame's
    plaintext is not yielded until its tag has verified, so no caller can be
    holding unauthenticated bytes when an error is raised further along.
    """
    base_nonce = _read_header(src)
    aes = AESGCM(key)
    counter = 0
    while True:
        raw = _read_exactly(src, _LENGTH.size)
        if not raw:
            raise ArchiveError(
                f"archive ends after frame {counter} without its terminator frame — "
                "it was truncated (this is the cut a plain 'every frame verified' "
                "check would have accepted: the remaining prefix is still a valid dump)"
            )
        if len(raw) < _LENGTH.size:
            raise ArchiveError(f"archive is truncated inside frame {counter}'s length")
        length = _LENGTH.unpack(raw)[0]
        if length < TAG_LEN:
            raise ArchiveError(
                f"frame {counter} claims {length} bytes, less than a tag — not an archive "
                "this writer ever produced"
            )
        body = _read_exactly(src, length)
        if len(body) < length:
            raise ArchiveError(
                f"archive is truncated inside frame {counter}: {length} bytes claimed, "
                f"{len(body)} present"
            )
        try:
            plaintext = aes.decrypt(
                _nonce(base_nonce, counter), body, _aad(base_nonce, counter, length)
            )
        except InvalidTag as exc:
            raise ArchiveError(
                f"frame {counter} failed authentication: the archive is corrupt or tampered "
                f"with, or {ENV_KEY} is not the key it was written with"
            ) from exc

        if not plaintext:
            # The terminator. Nothing may follow it: bytes after the end marker
            # are either a second archive concatenated onto this one or an
            # attacker's addition, and neither is something to decrypt hopefully.
            if _read_exactly(src, 1):
                raise ArchiveError("archive continues past its terminator frame")
            return

        yield plaintext
        counter += 1
        if counter >= MAX_FRAMES:  # pragma: no cover - 2**64 frames is unreachable
            raise ArchiveError(f"archive has more than {MAX_FRAMES} frames; refusing to continue")


def encrypt(src: BinaryIO, dst: BinaryIO, key: bytes) -> None:
    """Encrypt ``src`` into ``dst``. Nothing is written before the header.

    One pass: encryption has no authenticity to check, so the only way this
    fails is I/O (a full disk, a closed pipe), and those raise instead of
    returning a short archive as if it were complete.
    """
    base_nonce = os.urandom(NONCE_LEN)
    dst.write(MAGIC + base_nonce)
    aes = AESGCM(key)
    counter = 0
    while chunk := _read_exactly(src, CHUNK_SIZE):
        body = aes.encrypt(
            _nonce(base_nonce, counter), chunk, _aad(base_nonce, counter, len(chunk) + TAG_LEN)
        )
        dst.write(_LENGTH.pack(len(body)))
        dst.write(body)
        counter += 1
        if counter >= MAX_FRAMES:  # pragma: no cover - 2**64 frames is unreachable
            raise ArchiveError(f"input is larger than {MAX_FRAMES} frames; refusing to continue")

    # The terminator: an ordinary frame whose plaintext is empty, so it is
    # authenticated like every other one.
    end = aes.encrypt(
        _nonce(base_nonce, counter), b"", _aad(base_nonce, counter, TAG_LEN)
    )
    dst.write(_LENGTH.pack(len(end)))
    dst.write(end)


def decrypt(src: BinaryIO, dst: BinaryIO, key: bytes) -> None:
    """Decrypt ``src`` into ``dst``, writing nothing until all of it verifies.

    Two passes over the archive — see the module docstring for why a
    partial plaintext file is the one outcome worse than an error.
    """
    with _rewindable(src) as stream:
        for _ in _plaintext_frames(stream, key):
            pass
        stream.seek(0)
        for chunk in _plaintext_frames(stream, key):
            dst.write(chunk)


def _warn_if_weak_key(secret: str) -> None:
    if len(secret) < WEAK_KEY_CHARS:
        print(
            f"{ENV_KEY} is {len(secret)} characters. SHA-256 does not add entropy, so this "
            "archive is only as strong as that string; `openssl rand -hex 32` is a key.",
            file=sys.stderr,
        )


def main(argv: list[str]) -> int:
    name = Path(argv[0]).name
    if len(argv) != 2 or argv[1] not in {"encrypt", "decrypt"}:
        print(f"usage: {name} encrypt < plaintext > archive", file=sys.stderr)
        print(f"       {name} decrypt < archive > plaintext", file=sys.stderr)
        return 2

    # Before stdin is touched. A missing key must not produce a file, not even
    # an empty one: the caller redirects stdout to the archive path, and a
    # zero-byte *success-looking* file is a backup that will be discovered to be
    # nothing at the moment it is needed.
    secret = os.environ.get(ENV_KEY, "")
    if not secret:
        print(
            f"{ENV_KEY} is not set. Refusing to run: an unencrypted dump is cleartext PII, "
            "and a dump encrypted with a default key is the same thing with extra steps.",
            file=sys.stderr,
        )
        return 2
    _warn_if_weak_key(secret)
    key = _aes_key(secret)

    try:
        if argv[1] == "encrypt":
            encrypt(sys.stdin.buffer, sys.stdout.buffer, key)
        else:
            decrypt(sys.stdin.buffer, sys.stdout.buffer, key)
        sys.stdout.buffer.flush()
    except ArchiveError as exc:
        print(f"{argv[1]}: {exc}", file=sys.stderr)
        if argv[1] == "decrypt":
            print(
                "The archive is not trustworthy. Delete any file this command was writing "
                "and take a backup from a source you trust.",
                file=sys.stderr,
            )
        return 1
    except BrokenPipeError:
        # `| head`, or a caller that went away. The bytes the other end received
        # are a partial archive, so this is a failure, not a shrug. Point the
        # interpreter's own final flush at /dev/null so it does not also print a
        # traceback about a stream that is already gone.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        print(f"{argv[1]}: stdout closed before the archive was complete", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
