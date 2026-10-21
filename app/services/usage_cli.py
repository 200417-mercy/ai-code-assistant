"""Usage-ledger maintenance commands.

Usage::

    flask usage backfill [--batch-size N] [--limit N] [--dry-run] [--json]
    flask usage recompute [--batch-size N] [--limit N] [--user-id N]
                          [--workspace-id N] [--dry-run] [--json]
    flask usage purge [--days N] [--dry-run] [--json]

The backfill copies historical ``Message`` token columns into the append-only
usage ledger. It never updates message rows, is safe to re-run, and processes
messages in bounded keyset pages.

The purge removes usage rows older than the retention window (default from
``USAGE_RETENTION_DAYS``); rows newer than the cutoff are never touched.
"""

from __future__ import annotations

import json

import click
from flask import Flask

from app.services.usage_ledger import (
    BACKFILL_DEFAULT_BATCH_SIZE,
    BACKFILL_MAX_BATCH_SIZE,
    RECOMPUTE_DEFAULT_BATCH_SIZE,
    RECOMPUTE_DEFAULT_MAX_ROWS,
    RECOMPUTE_MAX_BATCH_SIZE,
    RECOMPUTE_MAX_ROWS,
)

EXIT_OK = 0
EXIT_ERROR = 2


def _write_json(data) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


def _fail(message: str, as_json: bool) -> None:
    if as_json:
        _write_json({"error": message})
    else:
        click.echo(message, err=True)
    raise click.exceptions.Exit(EXIT_ERROR)


def register_usage_cli(app: Flask) -> None:
    """Register the ``usage`` CLI command group on ``app``."""

    @app.cli.group("usage")
    def usage_group():
        """Usage-ledger maintenance commands."""

    @usage_group.command("backfill")
    @click.option(
        "--batch-size",
        type=click.IntRange(1, BACKFILL_MAX_BATCH_SIZE),
        default=BACKFILL_DEFAULT_BATCH_SIZE,
        show_default=True,
        help="Messages processed per database batch.",
    )
    @click.option(
        "--limit",
        type=click.IntRange(min=0),
        default=None,
        help="Maximum messages to scan in this run (default: all).",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Report what would be written without committing ledger rows.",
    )
    @click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
    def backfill(batch_size: int, limit: int | None, dry_run: bool, as_json: bool) -> int:
        """Backfill ledger rows from historical chat-message token counts."""
        from app.services.usage_ledger import backfill_from_messages

        try:
            result = backfill_from_messages(
                batch_size=batch_size,
                limit=limit,
                dry_run=dry_run,
            )
        except ValueError as exc:
            _fail(str(exc), as_json=as_json)

        if as_json:
            _write_json(result)
            return EXIT_OK

        action = "Dry run" if dry_run else "Backfill"
        click.echo(
            f"{action} complete: scanned={result['scanned']} "
            f"created={result['created']} "
            f"skipped_existing={result['skipped_existing']} "
            f"skipped_incomplete={result['skipped_incomplete']} "
            f"skipped_orphan={result['skipped_orphan']} "
            f"batches={result['batches']}"
        )
        return EXIT_OK

    @usage_group.command("recompute")
    @click.option(
        "--batch-size",
        type=click.IntRange(1, RECOMPUTE_MAX_BATCH_SIZE),
        default=RECOMPUTE_DEFAULT_BATCH_SIZE,
        show_default=True,
        help="Usage rows processed per database batch.",
    )
    @click.option(
        "--limit",
        type=click.IntRange(min=0, max=RECOMPUTE_MAX_ROWS),
        default=RECOMPUTE_DEFAULT_MAX_ROWS,
        show_default=True,
        help="Maximum rows to scan in this run.",
    )
    @click.option("--user-id", type=int, default=None, help="Only this user's rows.")
    @click.option("--workspace-id", type=int, default=None, help="Only this workspace's rows.")
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Report changes without writing new estimates.",
    )
    @click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
    def recompute(
        batch_size: int,
        limit: int,
        user_id: int | None,
        workspace_id: int | None,
        dry_run: bool,
        as_json: bool,
    ) -> int:
        """Recompute historical cost estimates from the current price table."""
        from app.services.usage_ledger import recompute_cost_estimates

        try:
            result = recompute_cost_estimates(
                user_id=user_id,
                workspace_id=workspace_id,
                batch_size=batch_size,
                max_rows=limit,
                dry_run=dry_run,
            )
        except ValueError as exc:
            _fail(str(exc), as_json=as_json)

        if as_json:
            _write_json(result)
            return EXIT_OK

        action = "Dry run" if dry_run else "Recompute"
        click.echo(
            f"{action} complete: scanned={result['scanned']} "
            f"updated={result['updated']} unchanged={result['unchanged']} "
            f"unpriced={result['unpriced']} batches={result['batches']}"
        )
        return EXIT_OK

    @usage_group.command("purge")
    @click.option(
        "--days",
        type=click.IntRange(min=1),
        default=None,
        help="Retention window in days (default: USAGE_RETENTION_DAYS).",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Report what would be deleted without removing rows.",
    )
    @click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
    def purge(days: int | None, dry_run: bool, as_json: bool) -> int:
        """Delete usage rows older than the retention window."""
        from app.services.usage_ledger import purge_records

        try:
            result = purge_records(retention_days=days, dry_run=dry_run)
        except ValueError as exc:
            _fail(str(exc), as_json=as_json)

        if as_json:
            _write_json(result)
            return EXIT_OK

        action = "Dry run" if dry_run else "Purge"
        click.echo(
            f"{action} complete: deleted={result['deleted']} "
            f"retention_days={result['retention_days']} "
            f"cutoff={result['cutoff']}"
        )
        return EXIT_OK


__all__ = ["EXIT_ERROR", "EXIT_OK", "register_usage_cli"]
