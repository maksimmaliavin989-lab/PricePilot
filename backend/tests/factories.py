"""Helpers that seed documents/runs/lines through the application services (TASK_003 will do the
same from real parsers)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from pricepilot.api.deps import Services
from pricepilot.domain.documents import ImportSource, ProcessingRun, RunStatus, SourceDocument
from pricepilot.domain.extraction import NewField, NewLine, Region, SourceLocation, ValueType

CONFIG = "c" * 64
PIPELINE = "dip-test-1"


def sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


@dataclass
class Seeded:
    workspace_id: UUID
    supplier_id: UUID
    document: SourceDocument
    run: ProcessingRun


def price_line(n: int, *, amount: str = "1234.50", review: bool = False) -> NewLine:
    row = n + 4
    return NewLine(
        raw_text=f"Цемент М500 Д0, мешок 50 кг | шт. | {amount}",
        location=SourceLocation(sheet="Прайс", row=row),
        needs_review=review,
        fields=(
            NewField(
                key="name",
                value_type=ValueType.TEXT,
                raw_value=f"Цемент М500 Д0 #{n}",
                normalized={"value": f"Цемент М500 Д0 #{n}"},
                confidence=0.98,
                location=SourceLocation(sheet="Прайс", row=row, column=2, cell=f"B{row}"),
                method="spreadsheet_cell",
            ),
            NewField(
                key="price",
                value_type=ValueType.MONEY,
                raw_value=amount.replace(".", ","),
                normalized={"amount": amount, "currency": "RUB"},
                confidence=0.9,
                location=SourceLocation(sheet="Прайс", row=row, column=4, cell=f"D{row}"),
                method="spreadsheet_cell",
            ),
            NewField(
                key="pack",
                value_type=ValueType.QUANTITY,
                raw_value="мешок 50 кг",
                normalized={"value": "50", "unit": "kg"},
                confidence=0.6,
                location=SourceLocation(
                    page=1, region=Region(x=0.1, y=0.2, width=0.3, height=0.02)
                ),
                method="ocr",
            ),
        ),
    )


def seed(
    svc: Services,
    *,
    lines: int = 3,
    finish: RunStatus | None = RunStatus.REVIEW_REQUIRED,
    name: str = "Прайс_1.xlsx",
) -> Seeded:
    ws = svc.workspaces.create(name="WS", locale="ru-RU", base_currency="RUB", country_code="RU")
    supplier = svc.suppliers.create(
        workspace_id=ws.id, name="ООО Ромашка", country_code="RU", default_currency="RUB"
    )
    reg = svc.documents.register(
        workspace_id=ws.id,
        supplier_id=supplier.id,
        original_name=name,
        detected_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extension="xlsx",
        size_bytes=8312,
        sha256=sha(name),
        storage_key=f"originals/{sha(name)}",
        import_source=ImportSource.FILE_PICKER,
        supported=True,
    )
    run, _ = svc.processing.start(reg.document.id, PIPELINE, CONFIG)
    for status in (RunStatus.VALIDATING, RunStatus.PARSING, RunStatus.EXTRACTING):
        run = svc.processing.advance(run.id, status)
    svc.processing.record_lines(
        run.id,
        [price_line(i) for i in range(1, lines + 1)],
        extractor="xlsx-test",
        extractor_version="0.0.1",
    )
    if finish is not None:
        run = svc.processing.advance(run.id, RunStatus.VALIDATING_RESULT)
        run = svc.processing.advance(run.id, finish)
    return Seeded(ws.id, supplier.id, svc.documents.get(reg.document.id), run)
