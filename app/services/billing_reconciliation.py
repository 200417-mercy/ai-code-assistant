"""Provider billing reconciliation (Phase 10).

Operators import the amount each LLM provider actually charged for a billing
period from a bounded CSV. That reported figure is stored as a
:class:`~app.models.provider_billing_record.ProviderBillingRecord` and compared
against the app's own estimate, which is derived from the token usage recorded
on assistant messages and the configured per-1K-token rate.

Two invariants matter and are covered by tests:

* The estimate is recomputed on read; importing provider data never overwrites
  it (imports only ever touch ``provider_billing_records``).
* An import is bounded: the CSV is parsed from a size- and row-limited string,
  and every field is validated, so a malformed or hostile file fails cleanly
  with a :class:`BillingImportError` instead of raising deep inside the ORM.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass

from flask import current_app
from sqlalchemy import extract, func

from app.extensions import db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.provider_billing_record import ProviderBillingRecord

#: A billing period is a calendar month: ``YYYY-MM``.
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
#: Provider identifiers are short slugs; keep them bounded before storage.
MAX_PROVIDER_LEN = 50
#: Column headers accepted in addition to the canonical names.
_PROVIDER_HEADERS = {"provider", "vendor"}
_AMOUNT_HEADERS = {"amount_usd", "cost", "cost_usd", "amount", "usd"}


class BillingImportError(ValueError):
    """Raised when an uploaded billing CSV cannot be parsed or validated."""

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ParsedBillingRow:
    """A single validated row parsed from a billing CSV."""

    provider: str
    period: str
    amount_usd: float

    def __iter__(self):
        # Allow ``provider, period, amount = row`` unpacking.
        return iter((self.provider, self.period, self.amount_usd))


def normalize_period(value: object) -> str:
    """Validate and return a ``YYYY-MM`` period string.

    Accepts ``YYYY-MM`` and ``YYYY-MM-DD`` (the day is discarded) and raises
    :class:`BillingImportError` for anything else.
    """
    text = str(value or "").strip()
    if len(text) >= 7:
        text = text[:7]
    if not PERIOD_RE.match(text):
        raise BillingImportError(
            f"Invalid billing period {value!r}; expected YYYY-MM.", kind="period"
        )
    return text


def _parse_amount(value: object) -> float:
    try:
        amount = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise BillingImportError(f"Invalid amount {value!r}.", kind="amount") from exc
    if not math.isfinite(amount) or amount < 0:
        raise BillingImportError(
            f"Amount must be a finite, non-negative number (got {value!r}).", kind="amount"
        )
    return amount


def parse_billing_csv(text: str, *, max_rows: int | None = None) -> list[ParsedBillingRow]:
    """Parse a bounded billing CSV into validated rows.

    The header must contain a provider column and a period column plus one
    amount column (``amount_usd`` or an accepted alias). Rows are returned in
    file order; at most ``max_rows`` (defaulting to the ``BILLING_IMPORT_MAX_ROWS``
    config value) are accepted, and an empty file is an error.
    """
    if max_rows is None:
        max_rows = current_app.config.get("BILLING_IMPORT_MAX_ROWS", 5000)
    if not isinstance(text, str) or not text.strip():
        raise BillingImportError("The uploaded CSV is empty.", kind="empty")

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [name.strip().lower() for name in (reader.fieldnames or []) if name]
    if not fieldnames:
        raise BillingImportError("The CSV has no header row.", kind="header")

    provider_col = next((name for name in fieldnames if name in _PROVIDER_HEADERS), None)
    period_col = next((name for name in fieldnames if name == "period"), None)
    amount_col = next((name for name in fieldnames if name in _AMOUNT_HEADERS), None)
    if provider_col is None or period_col is None or amount_col is None:
        raise BillingImportError(
            "The CSV must have 'provider', 'period', and an amount column " "(e.g. 'amount_usd').",
            kind="header",
        )

    def _value(row: dict, key: str) -> object:
        for raw_key, raw_value in row.items():
            if raw_key and raw_key.strip().lower() == key:
                return raw_value
        return None

    rows: list[ParsedBillingRow] = []
    for line_number, row in enumerate(reader, start=2):
        if row is None:
            continue
        provider = str(_value(row, provider_col) or "").strip().lower()
        period_value = _value(row, period_col)
        amount_value = _value(row, amount_col)
        if not provider and not period_value and not amount_value:
            continue  # skip fully blank lines
        if not provider:
            raise BillingImportError(f"Row {line_number}: provider is required.", kind="provider")
        if len(provider) > MAX_PROVIDER_LEN:
            raise BillingImportError(
                f"Row {line_number}: provider exceeds {MAX_PROVIDER_LEN} characters.",
                kind="provider",
            )
        rows.append(
            ParsedBillingRow(
                provider=provider,
                period=normalize_period(period_value),
                amount_usd=_parse_amount(amount_value),
            )
        )
        if len(rows) > max_rows:
            raise BillingImportError(f"Too many rows; the limit is {max_rows}.", kind="rows")

    if not rows:
        raise BillingImportError("The CSV contained no data rows.", kind="empty")
    return rows


def estimate_cost_usd(total_tokens: int | None) -> float:
    """Turn a token count into an estimated USD figure at the configured rate."""
    rate = current_app.config.get("LLM_ESTIMATE_USD_PER_1K_TOKENS", 0.0)
    return round((total_tokens or 0) / 1000 * float(rate), 6)


def estimate_by_provider_period(*, user_id: int | None = None) -> dict[tuple[str, str], float]:
    """Estimate cost per ``(provider, period)`` from recorded token usage.

    Only assistant messages that carry token usage are counted, grouped by the
    month they were created and their conversation's provider (``"unknown"``
    when a conversation has no provider set). The result is computed fresh on
    every call and never persisted, so imports cannot overwrite it.
    """
    provider_label = func.coalesce(Conversation.provider, "unknown")
    # ``extract`` compiles to the right month function for each backend
    # (``strftime`` on SQLite, ``EXTRACT`` on PostgreSQL).
    year_label = extract("year", Message.created_at)
    month_label = extract("month", Message.created_at)

    query = (
        db.session.query(
            provider_label.label("provider"),
            year_label.label("year"),
            month_label.label("month"),
            func.sum(Message.total_tokens).label("total_tokens"),
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(Message.role == "assistant", Message.total_tokens.isnot(None))
    )
    if user_id is not None:
        query = query.filter(Conversation.user_id == user_id)
    query = query.group_by(provider_label, year_label, month_label)

    estimates: dict[tuple[str, str], float] = {}
    for provider, year, month, total_tokens in query.all():
        period = f"{int(year):04d}-{int(month):02d}"
        key = (str(provider), period)
        estimates[key] = round(estimates.get(key, 0.0) + estimate_cost_usd(total_tokens), 6)
    return estimates


def reconcile(records, *, user_id: int | None = None) -> list[dict]:
    """Compare provider-reported records against fresh estimates.

    Returns one variance dict per record (and one per estimate that has no
    matching record), sorted by period then provider. ``variance_usd`` is
    ``reported - estimated`` and ``variance_pct`` is that difference as a
    percentage of the estimate (``None`` when the estimate is zero).
    """
    estimates = estimate_by_provider_period(user_id=user_id)

    reported: dict[tuple[str, str], float] = {}
    order: list[tuple[str, str]] = []
    for record in records:
        key = (record.provider, record.period)
        if key not in reported:
            order.append(key)
        reported[key] = (reported.get(key, 0.0) or 0.0) + float(record.amount_usd)

    for key in estimates:
        if key not in reported:
            order.append(key)

    results = []
    for provider, period in sorted(order, key=lambda item: (item[1], item[0])):
        key = (provider, period)
        estimated = round(estimates.get(key, 0.0), 6)
        actual = round(reported.get(key, 0.0), 6)
        variance = round(actual - estimated, 6)
        variance_pct = round(variance / estimated * 100, 4) if estimated else None
        results.append(
            {
                "provider": provider,
                "period": period,
                "reported_usd": actual,
                "estimated_usd": estimated,
                "variance_usd": variance,
                "variance_pct": variance_pct,
                "has_record": key in reported,
            }
        )
    return results


def _upsert(rows, *, user_id: int | None, source_filename: str | None) -> tuple[int, int]:
    """Insert or update one record per parsed row. Returns ``(created, updated)``."""
    created = updated = 0
    for row in rows:
        existing = ProviderBillingRecord.query.filter_by(
            user_id=user_id, provider=row.provider, period=row.period
        ).first()
        if existing is None:
            db.session.add(
                ProviderBillingRecord(
                    user_id=user_id,
                    provider=row.provider,
                    period=row.period,
                    amount_usd=row.amount_usd,
                    source_filename=source_filename,
                )
            )
            created += 1
        else:
            existing.amount_usd = row.amount_usd
            existing.source_filename = source_filename
            updated += 1
    return created, updated


def import_billing_csv(
    text: str,
    *,
    user_id: int | None,
    source_filename: str | None = None,
) -> dict:
    """Parse, persist, and reconcile a provider billing CSV.

    The import is idempotent per ``(user_id, provider, period)``: re-uploading
    the same file updates amounts in place. Committing is left to the caller so
    the whole import can be wrapped in one transaction.
    """
    max_bytes = current_app.config.get("BILLING_IMPORT_MAX_BYTES", 1024 * 1024)
    if len(text.encode("utf-8")) > max_bytes:
        raise BillingImportError(f"The CSV exceeds the {max_bytes}-byte limit.", kind="size")

    rows = parse_billing_csv(text)
    created, updated = _upsert(rows, user_id=user_id, source_filename=source_filename)
    db.session.flush()

    records = (
        ProviderBillingRecord.query.filter_by(user_id=user_id)
        .order_by(ProviderBillingRecord.period, ProviderBillingRecord.provider)
        .all()
    )
    return {
        "created": created,
        "updated": updated,
        "rows": len(rows),
        "records": [record.to_dict() for record in records],
        "reconciliation": reconcile(records, user_id=user_id),
    }


def reconciliation_report(*, user_id: int | None, period: str | None = None) -> dict:
    """Build the full reconciliation report for a user (optionally one period)."""
    query = ProviderBillingRecord.query.filter_by(user_id=user_id)
    if period:
        query = query.filter(ProviderBillingRecord.period == normalize_period(period))
    records = query.order_by(ProviderBillingRecord.period, ProviderBillingRecord.provider).all()

    rows = reconcile(records, user_id=user_id)
    if period:
        rows = [row for row in rows if row["period"] == normalize_period(period)]

    reported_total = round(sum(row["reported_usd"] for row in rows), 6)
    estimated_total = round(sum(row["estimated_usd"] for row in rows), 6)
    return {
        "rows": rows,
        "reported_total_usd": reported_total,
        "estimated_total_usd": estimated_total,
        "variance_usd": round(reported_total - estimated_total, 6),
        "records": [record.to_dict() for record in records],
    }
