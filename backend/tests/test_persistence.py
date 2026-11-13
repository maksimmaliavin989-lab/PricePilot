"""Repository + service integration on every configured engine (SQLite; PostgreSQL in CI)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import IntegrityError

from pricepilot.api.deps import Services
from pricepilot.application.errors import (
    DuplicateExtractionError,
    InvalidStateError,
    NotFoundError,
    RevisionConflictError,
)
from pricepilot.application.ports import DocumentFilter, LinePageRequest, PageRequest
from pricepilot.domain.audit import LOCAL_ACTOR
from pricepilot.domain.documents import DocumentStatus, ImportSource, RunStatus
from pricepilot.domain.extraction import ReviewDecision, ReviewStatus
from pricepilot.infrastructure.db.tables import (
    audit_events,
    extracted_fields,
    extracted_lines,
    source_documents,
)
from tests.factories import CONFIG, PIPELINE, price_line, seed, sha


def test_full_extraction_flow_and_document_status(svc: Services) -> None:
    s = seed(svc, lines=3)
    assert s.document.status is DocumentStatus.REVIEW_REQUIRED
    view = svc.processing.get(s.run.id)
    assert (view.counts.lines, view.counts.fields) == (3, 9)
    page = svc.processing.lines(s.document.id, None, LinePageRequest(limit=10))
    assert [i.line.ordinal for i in page.items] == [1, 2, 3]
    price = next(f for f in page.items[0].fields if f.field_key == "price")
    assert price.normalized_value == {"amount": "1234.50", "currency": "RUB"}
    assert price.provenance["cell"] == "D5"
    assert price.provenance["localization"] == "exact"
    assert price.provenance["source_document_id"] == str(s.document.id)
    pack = next(f for f in page.items[0].fields if f.field_key == "pack")
    assert pack.provenance["region"] == {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}


def test_money_precision_survives_storage(svc: Services) -> None:
    s = seed(svc, lines=0, finish=None)
    run, _ = svc.processing.start(s.document.id, "dip-test-2", CONFIG)
    for status in (RunStatus.VALIDATING, RunStatus.PARSING, RunStatus.EXTRACTING):
        svc.processing.advance(run.id, status)
    huge = "999999999999999999.99999999"
    svc.processing.record_lines(
        run.id, [price_line(1, amount=huge)], extractor="t", extractor_version="1"
    )
    lines = svc.processing.lines(s.document.id, run.id, LinePageRequest()).items
    price = next(f for f in lines[0].fields if f.field_key == "price")
    assert price.normalized_value == {"amount": huge, "currency": "RUB"}


def test_line_pagination_by_ordinal(svc: Services) -> None:
    s = seed(svc, lines=7)
    seen: list[int] = []
    after = None
    while True:
        page = svc.processing.lines(
            s.document.id, None, LinePageRequest(limit=3, after_ordinal=after)
        )
        seen += [i.line.ordinal for i in page.items]
        if page.next_ordinal is None:
            break
        after = page.next_ordinal
    assert seen == list(range(1, 8))


def test_registration_dedups_by_hash(svc: Services) -> None:
    s = seed(svc, lines=1)
    again = svc.documents.register(
        workspace_id=s.workspace_id,
        supplier_id=None,
        original_name="другое имя.xlsx",
        detected_mime="application/octet-stream",
        extension="xlsx",
        size_bytes=1,
        sha256=s.document.sha256.upper(),
        storage_key="originals/other",
        import_source=ImportSource.DRAG_DROP,
        supported=True,
    )
    assert again.duplicate
    assert again.document.id == s.document.id
    page = svc.documents.list(s.workspace_id, PageRequest(), DocumentFilter())
    assert len(page.items) == 1


def test_supplier_must_belong_to_workspace(svc: Services) -> None:
    s = seed(svc, lines=0, finish=None)
    other_ws = svc.workspaces.create(
        name="Другое", locale="en", base_currency="USD", country_code="US"
    )
    with pytest.raises(NotFoundError):
        svc.documents.register(
            workspace_id=other_ws.id,
            supplier_id=s.supplier_id,
            original_name="x.csv",
            detected_mime="text/csv",
            extension="csv",
            size_bytes=1,
            sha256=sha("x"),
            storage_key="k",
            import_source=ImportSource.FILE_PICKER,
            supported=True,
        )


def test_run_start_is_idempotent_and_replay_does_not_duplicate_lines(svc: Services) -> None:
    s = seed(svc, lines=2, finish=None)
    again, created = svc.processing.start(s.document.id, PIPELINE, CONFIG)
    assert (again.id, created) == (s.run.id, False)
    with pytest.raises(DuplicateExtractionError):
        svc.processing.record_lines(s.run.id, [price_line(1)], extractor="t", extractor_version="1")
    assert svc.processing.get(s.run.id).counts.lines == 2


def test_lines_only_while_extracting(svc: Services) -> None:
    s = seed(svc, lines=1)
    with pytest.raises(InvalidStateError):
        svc.processing.record_lines(s.run.id, [price_line(9)], extractor="t", extractor_version="1")


def test_unsupported_document_cannot_be_processed(svc: Services) -> None:
    s = seed(svc, lines=0, finish=None)
    reg = svc.documents.register(
        workspace_id=s.workspace_id,
        supplier_id=None,
        original_name="old.doc",
        detected_mime="application/msword",
        extension="doc",
        size_bytes=10,
        sha256=sha("doc"),
        storage_key="k",
        import_source=ImportSource.FILE_PICKER,
        supported=False,
    )
    with pytest.raises(InvalidStateError):
        svc.processing.start(reg.document.id, PIPELINE, CONFIG)
    assert svc.documents.get(reg.document.id).status is DocumentStatus.UNSUPPORTED


def test_failure_retry_and_cancel_update_document(svc: Services) -> None:
    s = seed(svc, lines=0, finish=None)
    run, _ = svc.processing.start(s.document.id, "dip-test-9", CONFIG)
    svc.processing.advance(run.id, RunStatus.VALIDATING)
    svc.processing.fail(run.id, "STORAGE_ERROR", retryable=True)
    assert svc.documents.get(s.document.id).status is DocumentStatus.FAILED
    retried = svc.processing.retry(run.id)
    assert (retried.status, retried.attempt) == (RunStatus.QUEUED, 2)
    assert svc.documents.get(s.document.id).status is DocumentStatus.PROCESSING
    svc.processing.cancel(run.id)
    assert svc.documents.get(s.document.id).status is DocumentStatus.STORED
    resumed = svc.processing.retry(run.id)
    assert (resumed.status, resumed.attempt) == (RunStatus.QUEUED, 3)
    assert svc.documents.get(s.document.id).status is DocumentStatus.PROCESSING


def test_review_correction_history_and_audit(svc: Services, engine: Engine) -> None:
    s = seed(svc, lines=1)
    item = svc.processing.lines(s.document.id, None, LinePageRequest()).items[0]
    price = next(f for f in item.fields if f.field_key == "price")
    corrected = svc.review.correct(
        price.id,
        value={"amount": "1199.00", "currency": "RUB"},
        reason="Цена из акции",
        expected_revision=1,
    )
    assert corrected.raw_value == price.raw_value
    accepted = svc.review.decide(
        price.id, decision=ReviewDecision.ACCEPT, reason=None, expected_revision=2
    )
    assert (accepted.review_status, accepted.revision) == (ReviewStatus.ACCEPTED, 3)
    with pytest.raises(RevisionConflictError):
        svc.review.correct(
            price.id, value={"amount": "1", "currency": "RUB"}, reason=None, expected_revision=2
        )
    history = svc.review.revisions(price.id)
    assert [(r.revision, r.action) for r in history] == [(2, "correct"), (3, "accept")]
    assert history[0].previous_value == {"amount": "1234.50", "currency": "RUB"}
    assert history[0].reason == "Цена из акции"
    with engine.connect() as c:
        events = c.execute(
            select(audit_events.c.action, audit_events.c.actor_id).where(
                audit_events.c.entity_id == price.id
            )
        ).all()
    assert [tuple(e) for e in events] == [("correct", LOCAL_ACTOR), ("accept", LOCAL_ACTOR)]


def test_stale_document_update_is_rejected(engine: Engine, svc: Services) -> None:
    from pricepilot.infrastructure.db.uow import SqlUnitOfWork

    s = seed(svc, lines=0, finish=None)
    with SqlUnitOfWork(engine) as uow:
        doc = uow.documents.get(s.document.id)
        assert doc is not None
        stale = doc.with_status(DocumentStatus.FAILED, doc.stamp.updated_at)
        assert not uow.documents.update(stale, doc.stamp.version - 1)


def test_database_enforces_unique_hash_and_foreign_keys(engine: Engine, svc: Services) -> None:
    s = seed(svc, lines=1)
    row = {
        "id": uuid4(),
        "workspace_id": s.workspace_id,
        "supplier_id": None,
        "original_name": "dup.xlsx",
        "detected_mime": "x/y",
        "extension": "xlsx",
        "size_bytes": 1,
        "sha256": s.document.sha256,
        "storage_key": "k",
        "import_source": "file_picker",
        "status": "stored",
        "retention_state": "active",
        "created_at": s.document.stamp.created_at,
        "updated_at": s.document.stamp.created_at,
        "version": 1,
    }
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(insert(source_documents).values(**row))
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(
            insert(extracted_lines).values(
                id=uuid4(), run_id=uuid4(), ordinal=1, raw_text="x", line_status="pending"
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(insert(source_documents).values(**{**row, "sha256": sha("z"), "status": "done"}))
    with engine.connect() as c:
        line_id = c.execute(select(extracted_lines.c.id)).scalars().first()
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(
            insert(extracted_fields).values(
                id=uuid4(),
                line_id=line_id,
                field_key="price",  # (line_id, field_key) is unique
                value_type="money",
                provenance_json={},
                review_status="unreviewed",
                revision=1,
            )
        )


def test_catalog_products_and_identifiers(svc: Services) -> None:
    from pricepilot.application.catalog import IdentifierInput
    from pricepilot.domain.catalog import IdentifierScheme

    ws = svc.workspaces.create(name="WS", locale="ru", base_currency="RUB", country_code="RU")
    view = svc.catalog.create(
        workspace_id=ws.id,
        canonical_name="Цемент М500 Д0",
        base_unit_code="bag",
        brand="Евроцемент",
        attributes={"масса": "50 кг"},
        identifiers=[IdentifierInput(IdentifierScheme.GTIN, "4006381333931")],
    )
    got = svc.catalog.get(view.product.id)
    assert got.product.attributes == {"масса": "50 кг"}
    assert [i.normalized_value for i in got.identifiers] == ["04006381333931"]
    found = svc.catalog.list(ws.id, PageRequest(), query="ЦЕМЕНТ")
    assert [v.product.id for v in found.items] == [view.product.id]
