from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from pricepilot.domain.catalog import (
    IdentifierScheme,
    ProductIdentifier,
    gtin_check_digit_ok,
    normalize_identifier,
)
from pricepilot.domain.common import DomainValidationError
from pricepilot.domain.documents import (
    DocumentStatus,
    ImportSource,
    InvalidTransitionError,
    ProcessingRun,
    RunStatus,
    SourceDocument,
    document_status_for_run,
    run_transition_allowed,
)
from pricepilot.domain.extraction import (
    ExtractedField,
    Localization,
    Provenance,
    Region,
    ReviewDecision,
    ReviewStatus,
    SourceLocation,
    StaleRevisionError,
    ValueType,
    normalize_value,
)
from pricepilot.domain.values import Money, Quantity, decimal_text, parse_decimal

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

# --- Decimals, money, quantities --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", "0"),
        ("1234.50", "1234.50"),  # scale is preserved
        ("0.00000001", "0.00000001"),
        ("999999999999999999.99999999", "999999999999999999.99999999"),
    ],
)
def test_decimal_round_trip_without_precision_loss(text: str, expected: str) -> None:
    assert decimal_text(parse_decimal(text)) == expected


@pytest.mark.parametrize(
    "bad",
    [
        1234.5,  # JSON number: may already have lost precision
        True,
        None,
        "1,234",  # ambiguous: thousands or decimal separator → review, not a guess
        "1 234.50",
        "1.234,50",
        "+1",
        "01",
        "1e3",
        "NaN",
        "1.",
        ".5",
        "1.123456789",  # > 8 fraction digits
        "1" * 19,  # > 18 integer digits
    ],
)
def test_decimal_rejects_floats_locale_formats_and_overflow(bad: object) -> None:
    with pytest.raises(DomainValidationError):
        parse_decimal(bad)


def test_negative_only_when_allowed() -> None:
    with pytest.raises(DomainValidationError):
        parse_decimal("-1")
    assert parse_decimal("-1.5", allow_negative=True) == Decimal("-1.5")


def test_money_requires_currency_and_serializes_strings() -> None:
    money = Money.parse({"amount": "12345678901234.12345678", "currency": "rub"})
    assert money.to_json() == {"amount": "12345678901234.12345678", "currency": "RUB"}
    with pytest.raises(DomainValidationError):
        Money.parse({"amount": "1"})


def test_quantity_requires_known_unit() -> None:
    assert Quantity.parse({"value": "0.001", "unit": "t"}).to_json() == {
        "value": "0.001",
        "unit": "t",
    }
    with pytest.raises(DomainValidationError):
        Quantity.parse({"value": "1", "unit": "мешок"})


# --- Run state machine ------------------------------------------------------------------


def _run() -> ProcessingRun:
    return ProcessingRun.queue(document_id=uuid4(), pipeline="dip-1", config_hash="a" * 64, now=NOW)


def test_run_happy_path_sets_timestamps() -> None:
    run = _run()
    for target in (
        RunStatus.VALIDATING,
        RunStatus.PARSING,
        RunStatus.OCR,
        RunStatus.EXTRACTING,
        RunStatus.VALIDATING_RESULT,
        RunStatus.COMPLETED,
    ):
        run = run.advance(target, now=NOW)
    assert run.started_at == NOW
    assert run.finished_at == NOW
    assert run.stamp.version == 7


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunStatus.QUEUED, RunStatus.COMPLETED),  # cannot skip to "processed"
        (RunStatus.PARSING, RunStatus.REVIEW_REQUIRED),
        (RunStatus.COMPLETED, RunStatus.QUEUED),
        (RunStatus.CANCELLED, RunStatus.VALIDATING),
        (RunStatus.FAILED, RunStatus.COMPLETED),
    ],
)
def test_forbidden_transitions(current: RunStatus, target: RunStatus) -> None:
    assert not run_transition_allowed(current, target)


def test_fail_cancel_and_retry_rules() -> None:
    run = _run().advance(RunStatus.VALIDATING, now=NOW)
    permanent = run.fail(code="UNSUPPORTED_FORMAT", retryable=False, now=NOW)
    with pytest.raises(InvalidTransitionError):
        permanent.retry(now=NOW)
    transient = run.fail(code="STORAGE_ERROR", retryable=True, now=NOW)
    retried = transient.retry(now=NOW)
    assert (retried.status, retried.attempt, retried.error) == (RunStatus.QUEUED, 2, None)
    cancelled = run.cancel(now=NOW)
    assert cancelled.error is not None
    assert cancelled.error.code == "CANCELLED"
    with pytest.raises(InvalidTransitionError):
        cancelled.cancel(now=NOW)
    resumed = cancelled.retry(now=NOW)  # the user's own cancellation is not a dead end
    assert (resumed.status, resumed.attempt, resumed.error) == (RunStatus.QUEUED, 2, None)
    with pytest.raises(DomainValidationError):
        run.fail(code="bad code", retryable=True, now=NOW)


def test_document_status_never_claims_processed_without_finished_run() -> None:
    assert document_status_for_run(RunStatus.EXTRACTING) is DocumentStatus.PROCESSING
    assert document_status_for_run(RunStatus.FAILED) is DocumentStatus.FAILED
    assert document_status_for_run(RunStatus.CANCELLED) is DocumentStatus.STORED
    assert document_status_for_run(RunStatus.COMPLETED) is DocumentStatus.COMPLETED


def test_unsupported_document_stays_unsupported() -> None:
    doc = SourceDocument.register(
        workspace_id=uuid4(),
        supplier_id=None,
        original_name="old.doc",
        detected_mime="application/msword",
        extension=".DOC",
        size_bytes=10,
        sha256="A" * 64,
        storage_key="originals/x",
        import_source=ImportSource.DRAG_DROP,
        supported=False,
        now=NOW,
    )
    assert (doc.status, doc.extension, doc.sha256) == (DocumentStatus.UNSUPPORTED, "doc", "a" * 64)
    with pytest.raises(InvalidTransitionError):
        doc.with_status(DocumentStatus.COMPLETED, NOW)


@pytest.mark.parametrize("name", ["", "../etc/passwd", "dir\\file.xlsx"])
def test_document_name_must_be_plain_file_name(name: str) -> None:
    with pytest.raises(DomainValidationError):
        SourceDocument.register(
            workspace_id=uuid4(),
            supplier_id=None,
            original_name=name,
            detected_mime="text/csv",
            extension="csv",
            size_bytes=1,
            sha256="a" * 64,
            storage_key="k",
            import_source=ImportSource.FILE_PICKER,
            supported=True,
            now=NOW,
        )


# --- Extraction ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value_type", "payload", "expected"),
    [
        (ValueType.TEXT, {"value": "  Цемент  "}, {"value": "Цемент"}),
        (ValueType.DECIMAL, {"value": "-0.5"}, {"value": "-0.5"}),
        (ValueType.INTEGER, {"value": "007"}, {"value": "7"}),
        (ValueType.UNIT, {"value": "m3"}, {"value": "m3"}),
        (ValueType.CURRENCY, {"value": "usd"}, {"value": "USD"}),
        (ValueType.DOCUMENT_DATE, {"value": "2026-11-13"}, {"value": "2026-11-13"}),
        (
            ValueType.MONEY,
            {"amount": "10.10", "currency": "EUR"},
            {"amount": "10.10", "currency": "EUR"},
        ),
    ],
)
def test_normalize_value(value_type: ValueType, payload: object, expected: object) -> None:
    assert normalize_value(value_type, payload) == expected


@pytest.mark.parametrize(
    ("value_type", "payload"),
    [
        (ValueType.TEXT, {"value": ""}),
        (ValueType.TEXT, "plain string"),
        (ValueType.INTEGER, {"value": 5}),
        (ValueType.UNIT, {"value": "мешок"}),
        (ValueType.DOCUMENT_DATE, {"value": "13.11.2026"}),
        (ValueType.DECIMAL, {"value": "1", "extra": "x"}),
    ],
)
def test_normalize_value_rejects(value_type: ValueType, payload: object) -> None:
    with pytest.raises(DomainValidationError):
        normalize_value(value_type, payload)


def test_location_localization_and_region_bounds() -> None:
    assert SourceLocation(sheet="S", row=3, cell="B3").localization is Localization.EXACT
    assert SourceLocation(page=2).localization is Localization.APPROXIMATE
    assert SourceLocation(sheet="S").localization is Localization.NONE
    with pytest.raises(DomainValidationError):
        Region(x=0.9, y=0, width=0.2, height=0.1)
    with pytest.raises(DomainValidationError):
        SourceLocation(row=0)
    with pytest.raises(DomainValidationError):
        SourceLocation(cell="b3")


def _field(normalized: object | None = None) -> ExtractedField:
    return ExtractedField.extracted(
        line_id=uuid4(),
        key="price",
        value_type=ValueType.MONEY,
        raw_value="1 234,5 руб.",
        normalized=normalized,
        confidence=0.4,
        provenance=Provenance(
            source_document_id=uuid4(),
            location=SourceLocation(sheet="S", row=5, cell="D5"),
            extractor="xlsx",
            extractor_version="1",
            pipeline_version="dip-1",
            method="spreadsheet_cell",
        ),
    )


def test_correction_keeps_raw_value_and_creates_revision() -> None:
    field = _field()
    updated, revision = field.corrected(
        value={"amount": "1234.50", "currency": "RUB"},
        reason="Разделитель тысяч",
        actor="local:owner",
        expected_revision=1,
        now=NOW,
    )
    assert updated.raw_value == "1 234,5 руб."
    assert updated.normalized_value == {"amount": "1234.50", "currency": "RUB"}
    assert (updated.review_status, updated.revision) == (ReviewStatus.CORRECTED, 2)
    assert (revision.revision, revision.previous_value, revision.action) == (2, None, "correct")
    assert revision.new_value == updated.normalized_value
    with pytest.raises(StaleRevisionError):
        updated.corrected(
            value={"amount": "1", "currency": "RUB"},
            reason=None,
            actor="a",
            expected_revision=1,
            now=NOW,
        )


def test_accept_requires_normalized_value() -> None:
    with pytest.raises(DomainValidationError):
        _field().decided(
            decision=ReviewDecision.ACCEPT,
            reason=None,
            actor="a",
            expected_revision=1,
            now=NOW,
        )
    rejected, _ = _field().decided(
        decision=ReviewDecision.REJECT, reason=None, actor="a", expected_revision=1, now=NOW
    )
    assert rejected.review_status is ReviewStatus.REJECTED


def test_confidence_bounds() -> None:
    with pytest.raises(DomainValidationError):
        ExtractedField.extracted(
            line_id=uuid4(),
            key="name",
            value_type=ValueType.TEXT,
            raw_value="x",
            normalized=None,
            confidence=1.5,
            provenance=_provenance(),
        )


def _provenance() -> Provenance:
    return Provenance(
        source_document_id=uuid4(),
        location=SourceLocation(),
        extractor="x",
        extractor_version="1",
        pipeline_version="dip-1",
        method="user",
    )


# --- Catalog ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("4006381333931", "04006381333931"),  # EAN-13
        ("4006-3813 33931", "04006381333931"),
        ("96385074", "00000096385074"),  # EAN-8
        ("036000291452", "00036000291452"),  # UPC-A
    ],
)
def test_gtin_normalized_to_14_digits(raw: str, normalized: str) -> None:
    assert normalize_identifier(IdentifierScheme.GTIN, raw) == normalized


@pytest.mark.parametrize("raw", ["4006381333932", "12345", "abc4006381333931"])
def test_gtin_invalid(raw: str) -> None:
    with pytest.raises(DomainValidationError):
        normalize_identifier(IdentifierScheme.GTIN, raw)


def test_gtin_check_digit() -> None:
    assert gtin_check_digit_ok("4006381333931")
    assert not gtin_check_digit_ok("4006381333930")


def test_supplier_sku_needs_issuer_and_is_case_insensitive() -> None:
    with pytest.raises(DomainValidationError):
        ProductIdentifier.create(
            product_id=uuid4(), scheme=IdentifierScheme.SUPPLIER_SKU, value="СМ-101", issuer=None
        )
    ident = ProductIdentifier.create(
        product_id=uuid4(),
        scheme=IdentifierScheme.SUPPLIER_SKU,
        value=" СМ - 101 ",
        issuer="ООО Ромашка",
    )
    assert ident.normalized_value == "см-101"
