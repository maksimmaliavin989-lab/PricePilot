"""Review remarks of an extracted line, tied to fields and actions.

The extraction stores *why* a line was flagged (``review_reasons``) — that list is history
and never changes. What the user must still do is derived here from the reasons **and the
current fields**: every remark names the field it concerns, the action that resolves it and
whether it is resolved now. Correcting or confirming the right field resolves the remark;
confirming an unrelated field (e.g. the name when the unit is missing) does not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pricepilot.domain.extraction import ExtractedField, ReviewStatus

_CONFIRMED = {ReviewStatus.ACCEPTED, ReviewStatus.CORRECTED}


class IssueAction(StrEnum):
    ADD_VALUE = "add_value"  # the field is missing: enter it
    FIX_VALUE = "fix_value"  # the value could not be read: correct it
    CONFIRM_OR_FIX = "confirm_or_fix"  # read with a guess: confirm or replace
    CHOOSE_PRICE = "choose_price"  # several untyped prices: accept the one that applies
    CONFIRM = "confirm"  # check against the original and confirm


@dataclass(frozen=True, slots=True)
class LineIssue:
    code: str  # the extraction reason
    field_key: str  # "unit", "price", "name" … — the field to act on
    field_ids: tuple[UUID, ...]  # existing fields concerned (empty when the field is missing)
    action: IssueAction
    hint: str  # what to do, in plain words
    resolved: bool


def _confirmed(field: ExtractedField) -> bool:
    return field.review_status in _CONFIRMED and field.normalized_value is not None


def _is_price(field: ExtractedField) -> bool:
    return field.field_key == "price" or field.field_key.startswith("price_")


def _ids(fields: Sequence[ExtractedField]) -> tuple[UUID, ...]:
    return tuple(f.id for f in fields)


def _unit_issue(code: str, unit: ExtractedField | None) -> LineIssue:
    if code == "MISSING_UNIT" and unit is None:
        return LineIssue(
            code,
            "unit",
            (),
            IssueAction.ADD_VALUE,
            "В строке нет единицы измерения: выберите единицу.",
            False,
        )
    ids = (unit.id,) if unit else ()
    resolved = unit is not None and _confirmed(unit)
    if code == "MISSING_UNIT":
        return LineIssue(
            code,
            "unit",
            ids,
            IssueAction.ADD_VALUE,
            "Единица добавлена вручную.",
            resolved,
        )
    if code == "UNKNOWN_UNIT":
        return LineIssue(
            code,
            "unit",
            ids,
            IssueAction.FIX_VALUE,
            "Единица в файле не распознана: исправьте поле «Единица» — выберите единицу.",
            resolved,
        )
    hint = (
        "«м» со скана может означать м, м² или м³: подтвердите единицу или выберите другую."
        if code == "UNIT_AMBIGUOUS"
        else "Единица прочитана с исправлением символов: подтвердите её или выберите другую."
    )
    return LineIssue(code, "unit", ids, IssueAction.CONFIRM_OR_FIX, hint, resolved)


def line_issues(reasons: Sequence[str], fields: Sequence[ExtractedField]) -> list[LineIssue]:
    by_key = {f.field_key: f for f in fields}
    unit = by_key.get("unit")
    prices = [f for f in fields if _is_price(f)]
    live_prices = [f for f in prices if f.review_status is not ReviewStatus.REJECTED]
    any_price_confirmed = any(_confirmed(f) for f in prices)
    issues: list[LineIssue] = []
    for code in dict.fromkeys(reasons):
        if code in {"MISSING_UNIT", "UNKNOWN_UNIT", "UNIT_CORRECTED", "UNIT_AMBIGUOUS"}:
            issues.append(_unit_issue(code, unit))
        elif code in {"INVALID_NUMBER", "AMBIGUOUS_NUMBER"}:
            unread = [f for f in live_prices if f.normalized_value is None]
            fixed = [f for f in prices if f.review_status is ReviewStatus.CORRECTED]
            hint = (
                "Число вида 1,234 / 1.234 неоднозначно: исправьте цену, указав её однозначно."
                if code == "AMBIGUOUS_NUMBER"
                else "Цена не прочиталась как число: исправьте значение цены."
            )
            issues.append(
                LineIssue(
                    code,
                    "price",
                    _ids(unread or fixed),
                    IssueAction.FIX_VALUE,
                    hint,
                    not unread and any_price_confirmed,
                )
            )
        elif code == "MISSING_PRICE":
            if not prices:
                issues.append(
                    LineIssue(
                        code,
                        "price",
                        (),
                        IssueAction.ADD_VALUE,
                        "В строке нет цены: укажите цену или отклоните строку.",
                        False,
                    )
                )
            else:
                issues.append(
                    LineIssue(
                        code,
                        "price",
                        _ids(prices),
                        IssueAction.CONFIRM,
                        "Одна из колонок цен пуста: подтвердите цену, которая есть.",
                        any_price_confirmed,
                    )
                )
        elif code == "MULTIPLE_PRICE_COLUMNS":
            issues.append(
                LineIssue(
                    code,
                    "price",
                    _ids(prices),
                    IssueAction.CHOOSE_PRICE,
                    "Несколько цен без названия колонок: подтвердите ту, что применяется, "
                    "остальные отклоните.",
                    any_price_confirmed,
                )
            )
        elif code == "ASSUMED_CURRENCY":
            issues.append(
                LineIssue(
                    code,
                    "price",
                    _ids(live_prices),
                    IssueAction.CONFIRM,
                    "Валюта не указана в файле и взята из настроек: проверьте её и подтвердите "
                    "цену или исправьте валюту.",
                    bool(live_prices) and all(_confirmed(f) for f in live_prices),
                )
            )
        elif code == "COLUMNS_INFERRED":
            key_fields = [f for f in (by_key.get("name"), *live_prices) if f is not None]
            issues.append(
                LineIssue(
                    code,
                    "name",
                    _ids(key_fields),
                    IssueAction.CONFIRM,
                    "Заголовок таблицы не прочитан, столбцы определены по содержимому: сверьте "
                    "наименование и цены с оригиналом и подтвердите их.",
                    bool(key_fields) and all(_confirmed(f) for f in key_fields),
                )
            )
        else:  # a reason this version does not know: check the whole line
            issues.append(
                LineIssue(
                    code,
                    "name",
                    _ids(fields),
                    IssueAction.CONFIRM,
                    "Проверьте строку по оригиналу и подтвердите поля.",
                    all(f.review_status is not ReviewStatus.UNREVIEWED for f in fields),
                )
            )
    return issues
