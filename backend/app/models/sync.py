"""Sync queue and observability: jobs, runs, run events (ADR-0004/0016).

Three tables, one lifecycle. A **job** is a scheduled intention to sync one
connection and is the only thing that can be claimed; a **run** is one execution
of a job and carries what happened; a **run event** is one ordered, sanitized line
of that run's log.

All three are household-scoped and RLS-protected like the rest of the ledger. The
worker discovers work by enumerating ``households`` (an identity table that is
non-RLS by design) and claiming *inside* each household's scope, so it never needs
a privileged cross-tenant read — see ADR-0004's resolution note.

Two deliberate departures from the usual shape, both because these rows are
history rather than editable records:

* ``sync_runs`` and ``sync_run_events`` carry **no** ``created_at``/``updated_at``.
  A run's creation time *is* its ``started_at``, and having both invites the
  question of which to read. ``sync_jobs`` keeps the mixin because it is a mutable
  queue row that genuinely benefits from ``updated_at``.
* ``sync_run_events`` carries its own ``household_id`` even though it is a child of
  ``sync_runs``. Same reason ``balance_snapshots`` does: it buys a *direct*
  household policy (no ``EXISTS`` against the parent), a single-table prune, and no
  join for the dashboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin

# Job lifecycle. ``cancelled`` and ``expired`` are distinct terminal states because
# the dashboard reports them separately: cancelled means a human stopped it,
# expired means it died repeatedly and the worker gave up.
JOB_STATUSES = ("queued", "running", "done", "error", "cancelled", "expired")
JOB_TRIGGERS = ("cron", "manual", "reconnect")

# Run outcome. ``partial`` exists because the bridge reports per-account warnings on
# an otherwise-successful fetch — without it one bad account marks the whole run a
# failure and the alert channel cries wolf.
RUN_STATUSES = ("running", "ok", "partial", "error", "cancelled")
EVENT_LEVELS = ("debug", "info", "warning", "error")


class SyncJob(UUIDPkMixin, TimestampMixin, Base):
    """One queued intention to sync one connection.

    Claiming is ``FOR UPDATE SKIP LOCKED`` over ``status='queued'`` rows whose
    ``not_before`` has arrived. Three columns make that safe:

    * ``heartbeat_at`` — the reaper's clock. Measuring from ``started_at`` instead
      would reap a worker that is merely slow, which is the whole reason the
      timeout is anchored to a heartbeat rather than to the claim.
    * ``claim_token`` — a fencing token. Every worker write is
      ``WHERE id = :id AND claim_token = :token``, so a worker whose job was reaped
      and re-claimed touches zero rows and discards its results instead of
      resurrecting a terminal state.
    * ``cancel_requested_at`` — a *timestamp*, not a status, because "a cancel is
      pending" and "the job is running" are true at the same time. A status value
      cannot express both, and would lose the fact that the job is still executing.
    """

    __tablename__ = "sync_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','error','cancelled','expired')",
            name="status_valid",
        ),
        CheckConstraint(
            "trigger IN ('cron','manual','reconnect')", name="trigger_valid"
        ),
        # ADR-0004's one-running-job-per-connection, enforced by the database rather
        # than by the claim's care. Declared here *and* in the migration, or
        # `alembic check` fails on a fresh DB.
        Index(
            "uq_sync_jobs_connection_running",
            "connection_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_sync_jobs_household_status", "household_id", "status"),
        Index("ix_sync_jobs_connection_id", "connection_id"),
        # The claim's index: "the next due job".
        Index("ix_sync_jobs_claimable", "status", "not_before", "created_at"),
    )

    # No ``index=True``: the composite (household_id, status) index below covers
    # household_id-prefix lookups, so a single-column one would be dead weight —
    # the same call ``TransactionSplit`` makes for its (parent_txn_id, owner_id).
    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
    )
    # CASCADE: a job for a deleted connection is meaningless, and this is what makes
    # "remove the connection" clean without touching the queue.
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("account_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="cron")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Backoff gate: the job is claimable only once this has passed.
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncRun(UUIDPkMixin, Base):
    """One execution of a job, with its counts and HTTP timings.

    ``connection_id`` is ``ON DELETE SET NULL``, not CASCADE: a reconnect *is* a
    remove + re-add (ADR-0009), so cascading would erase the run history at exactly
    the moment the user wants to read it. ``connection_label`` carries the
    institution's name forward so that history can still say where it came from.
    """

    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','ok','partial','error','cancelled')",
            name="status_valid",
        ),
        Index("ix_sync_runs_household_started", "household_id", "started_at"),
        Index("ix_sync_runs_connection_started", "connection_id", "started_at"),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("account_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sync_jobs.id", ondelete="SET NULL"), nullable=True
    )
    connection_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="cron")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_fetched: Mapped[int | None] = mapped_column(Integer, nullable=True)

    accounts_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accounts_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Not in ARCHITECTURE §3's count list, but the M2 acceptance bar is specifically
    # "a reconnect loses zero transactions and creates zero dupes" — so the number
    # that proves it belongs on the row the dashboard reads.
    accounts_remapped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    txns_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Counted only when a value actually changed, so a no-op re-sync reports
    # inserted == 0 *and* updated == 0. That is the idempotency bar.
    txns_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    txns_reconciled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pendings_expired: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transfers_matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rules_applied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncRunEvent(UUIDPkMixin, Base):
    """One ordered, sanitized line of a run's log (ADR-0016).

    ``seq`` exists because ``ts`` cannot order these: Postgres ``now()`` is the
    *transaction* timestamp, so every event written in one ingest transaction shares
    it exactly. Ordering by ``ts`` would let the dashboard's feed shuffle. This is
    the same trap ``services/rules.py`` documents for ``Rule.created_at``.

    ``detail`` is sanitized **at write time**, never at read time — write time is the
    only place that can also prevent the raw provider body (which is financial PII)
    from landing in the column at all. Never store a payload fragment here; store a
    *shape* description, such as which key was missing and how many bytes arrived.
    """

    __tablename__ = "sync_run_events"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "seq"),
        CheckConstraint(
            "level IN ('debug','info','warning','error')", name="level_valid"
        ),
        Index("ix_sync_run_events_household_ts", "household_id", "ts"),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False,
    )
    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sync_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="info")
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


__all__ = [
    "EVENT_LEVELS",
    "JOB_STATUSES",
    "JOB_TRIGGERS",
    "RUN_STATUSES",
    "SyncJob",
    "SyncRun",
    "SyncRunEvent",
]
