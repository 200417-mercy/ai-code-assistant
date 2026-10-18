"""Provider-reported billing records for cost reconciliation (Phase 10).

Operators export the amount a provider actually charged for a billing period
(usually a month) and import it here. A record only stores the *reported*
figure; the application's own token-based estimate is recomputed on read and is
never overwritten by an import (see ``app.services.billing_reconciliation``).
"""

from datetime import UTC, datetime

from app.extensions import db


class ProviderBillingRecord(db.Model):
    """A single provider-reported cost for one provider and billing period.

    ``period`` is a ``YYYY-MM`` string so a month can be compared directly
    against the app's per-month token estimate. Re-importing the same
    ``(user_id, provider, period)`` updates the amount in place rather than
    appending a duplicate.
    """

    __tablename__ = "provider_billing_records"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "provider",
            "period",
            name="uq_provider_billing_user_provider_period",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    #: Owner of the import. ``None`` after the user is deleted so billing
    #: history survives account removal.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: Lower-cased provider slug, e.g. ``openai`` or ``anthropic``.
    provider = db.Column(db.String(50), nullable=False, index=True)
    #: Billing period in ``YYYY-MM`` form.
    period = db.Column(db.String(7), nullable=False, index=True)
    #: The provider-reported cost for the period.
    amount_usd = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    #: Name of the uploaded file the row was parsed from (for provenance).
    source_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user = db.relationship("User")

    def to_dict(self) -> dict:
        """Serialize the record for the JSON API."""
        return {
            "id": self.id,
            "provider": self.provider,
            "period": self.period,
            "amount_usd": round(self.amount_usd, 6),
            "currency": self.currency,
            "source_filename": self.source_filename,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ProviderBillingRecord id={self.id} provider={self.provider!r} "
            f"period={self.period!r} amount_usd={self.amount_usd}>"
        )
