"""Connection and sync-run shapes.

Every response model here lists its fields **explicitly**. That is the whole
security design of this module, and it is worth being blunt about why: the
credential is ``AccountConnection.access_url_encrypted``, a ciphertext of a
Basic-auth URL. ``from_attributes`` with an explicit field list cannot leak it —
there is no field to populate — whereas a single ``model_dump()`` over the ORM
object would hand the ciphertext (or, with the key to hand, the live credential)
to whoever asked. The rule generalizes past this model: an object that holds a
secret never gets serialized by reflection.

``access_url`` appears nowhere in this file, in any form. Not a null field, not
an excluded one. A field that exists but is always null is a field somebody
fills in later.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.ledger import (
    SYNC_INTERVAL_DEFAULT_MINUTES,
    SYNC_INTERVAL_MAX_MINUTES,
    SYNC_INTERVAL_MIN_MINUTES,
)

#: Generous, because a setup token is base64 of a URL and the length of neither
#: is ours to decide. Bounded, because it is request input that gets decoded and
#: then posted to a third party — an unbounded body is an unbounded decode.
SETUP_TOKEN_MAX_CHARS = 4096


class ConnectionClaim(BaseModel):
    """``POST /connections/claim`` — the only route that accepts a credential.

    The token is a body *field*, not a query parameter: query strings land in
    access logs, in ``Referer`` headers, and in browser history, and a single-use
    credential should appear in none of them.

    Never echoed back. ``ConnectionOut`` has no field for it, so a response
    cannot repeat it even by accident.
    """

    setup_token: str = Field(min_length=1, max_length=SETUP_TOKEN_MAX_CHARS)


class ConnectionUpdate(BaseModel):
    """The two knobs the control panel turns.

    ``is_enabled`` is pause/resume and ``sync_interval_minutes`` is the cadence.
    Absent means no change; neither has a "clear" semantics, because a nullable
    pause flag or a nullable interval would both need a third state to mean
    "inherit", and the columns give each exactly one meaning.
    """

    is_enabled: bool | None = None
    sync_interval_minutes: int | None = Field(
        default=None,
        ge=SYNC_INTERVAL_MIN_MINUTES,
        le=SYNC_INTERVAL_MAX_MINUTES,
    )


class ConnectionOut(BaseModel):
    """A connection as the dashboard and the settings tab see it.

    ``status`` and ``is_enabled`` are separate on purpose and the client must
    render them as two things: health (``ok`` / ``auth_error`` / ``error``) is
    what the *bank* is doing, and ``is_enabled`` is what the *user* did. A paused
    connection showing "auth error" is a paused connection that also needs
    reconnecting, and collapsing the two into one badge would lose one of them.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    org_name: str | None
    status: str
    last_synced_at: datetime | None
    last_error: str | None

    is_enabled: bool
    sync_interval_minutes: int
    next_sync_at: datetime | None

    created_at: datetime

    #: When a sync last brought anything new — a transaction inserted, changed
    #: or settled — and how many successful syncs have run since without any.
    #: A bank bridge can keep answering "ok" with the same cached payload for
    #: days (found on a live instance, session 06); these say so, so a stall
    #: reads as a stall rather than as the app losing transactions.
    last_new_data_at: datetime | None = None
    quiet_syncs: int = 0


class SyncJobOut(BaseModel):
    """A queued or running job, for the panel's live table.

    ``heartbeat_at`` is exposed rather than hidden behind an "elapsed" figure:
    the reaper's clock is ``heartbeat_at < now() - reap_after``, so a job whose
    heartbeat has stopped advancing is exactly what a stuck job looks like from
    the outside, and the operator should be able to see the same signal.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connection_id: uuid.UUID
    trigger: str
    status: str
    attempts: int
    created_at: datetime
    not_before: datetime | None
    claimed_at: datetime | None
    heartbeat_at: datetime | None
    cancel_requested_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class SyncRunOut(BaseModel):
    """One run's counters and timings — the history row.

    ``connection_label`` is what keeps this readable after the connection is
    gone: ``connection_id`` is SET NULL on delete, so without the label a
    disconnect would erase the institution's name from its own history.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connection_id: uuid.UUID | None
    connection_label: str | None
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    http_ms: int | None
    http_status: int | None
    bytes_fetched: int | None

    accounts_seen: int
    accounts_created: int
    accounts_remapped: int
    txns_rekeyed: int
    txns_inserted: int
    txns_updated: int
    txns_reconciled: int
    pendings_expired: int
    transfers_matched: int
    rules_applied: int
    error: str | None


class SyncRunEventOut(BaseModel):
    """One line of a run's log.

    ``detail`` is a JSONB blob that was sanitized at **write** time (ADR-0016),
    so it is returned as-is — re-sanitizing on the way out would suggest the
    stored form is untrusted, and the stored form is the one that matters. It is
    typed ``dict`` rather than ``Any`` because the writer always emits an object
    and a client should be able to index it without a null check.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    ts: datetime
    level: str
    event: str
    detail: dict


class SyncRunDetail(BaseModel):
    """A run and its log together — one round trip for the expanded row."""

    run: SyncRunOut
    events: list[SyncRunEventOut]


class NotificationOut(BaseModel):
    """One notice the worker decided to send, as the browser shows it (ADR-0037).

    Flattened to a title and a body because that is the shape a notification has,
    and composed **server-side** (``Trouble.notice``) because the decision that
    produced it lives in the worker: a browser allowed to build its own wording
    from raw fields is a browser that can be persuaded to put an amount in it.
    Both strings were sanitized at write time and are returned as they were stored.

    ``connection_id`` is what the browser passes as the notification's ``tag``, so
    a repeat about one connection replaces the standing notification rather than
    stacking a column of them. It is nullable, and deliberately not a 500 when it
    is: a connection disconnected since the failure leaves its notices behind,
    which is correct — the notice is history and the failure still happened.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    ts: datetime
    connection_id: uuid.UUID | None
    title: str
    body: str


class ConnectionDefaults(BaseModel):
    """The cadence floor, ceiling and default, so the UI's slider needs no
    hard-coded copy of them.

    Its own route rather than a field on the connection list: it is a property of
    the *schema*, not of the household — the same three integers for every request
    — and folding it into a list response would repeat them per connection and
    invite a client to read the bounds off whichever row it happened to get."""

    sync_interval_minutes: int = SYNC_INTERVAL_DEFAULT_MINUTES
    sync_interval_min_minutes: int = SYNC_INTERVAL_MIN_MINUTES
    sync_interval_max_minutes: int = SYNC_INTERVAL_MAX_MINUTES
