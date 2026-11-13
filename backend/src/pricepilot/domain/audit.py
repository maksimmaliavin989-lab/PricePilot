"""Audit & Export module: append-only audit events (who/when/what, never full documents)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pricepilot.domain.common import ensure_utc, new_id

LOCAL_ACTOR = "local:owner"  # single-user local principal; cloud identities come later


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: UUID
    workspace_id: UUID
    actor_id: str | None
    entity_type: str
    entity_id: UUID
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    occurred_at: datetime
    correlation_id: str | None

    @staticmethod
    def record(
        *,
        workspace_id: UUID,
        actor_id: str | None,
        entity_type: str,
        entity_id: UUID,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        now: datetime,
        correlation_id: str | None,
    ) -> AuditEvent:
        return AuditEvent(
            id=new_id(),
            workspace_id=workspace_id,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
            occurred_at=ensure_utc(now),
            correlation_id=correlation_id,
        )
