"""Plugin artifact integrity verification (#384).

A marketplace artifact is only trustworthy if the bytes downloaded match the
sha256 digest recorded in the catalog. This module provides the single
verification path used before an artifact is registered:

1. normalize the catalog digest (accepting ``sha256:`` / ``sha256-`` prefixes
   and bare hex);
2. hash the artifact (streaming for files, never loading a whole package into
   memory);
3. compare the two in constant time with :func:`hmac.compare_digest`;
4. on mismatch, record a bounded audit event (when a workspace context is
   supplied) and raise :class:`ArtifactIntegrityError`.

:func:`verify_and_register_artifact` makes the ordering explicit: the
registration callback is invoked **only after** verification succeeds, so a
tampered artifact can never reach the registry.

This module does not download anything and does not execute plugin code.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from app.services.plugins import PluginError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.user import User

logger = logging.getLogger(__name__)

#: Read size used when hashing a file or stream.
CHUNK_SIZE = 1024 * 1024

#: Root file names required by the plugin artifact format (#382).
ARTIFACT_MANIFEST = "manifest.json"
ARTIFACT_SIGNATURE = "signature.json"

#: Accepted sha256 spellings: ``sha256:<hex>``, ``sha256-<hex>``, and bare hex.
_SHA256_RE = re.compile(r"^(?:sha256[:=-])?([0-9a-fA-F]{64})$", re.IGNORECASE)

#: A filesystem path, in-memory bytes, or a binary file-like object.
ArtifactSource = str | os.PathLike[str] | bytes | bytearray | memoryview | BinaryIO


class ArtifactError(PluginError):
    """A plugin artifact could not be read or validated."""


class ArtifactIntegrityError(ArtifactError):
    """An artifact's sha256 digest does not match the catalog value."""

    def __init__(self, expected: str, actual: str, *, plugin_id: str | None = None) -> None:
        label = f" for plugin {plugin_id}" if plugin_id else ""
        super().__init__(f"Artifact sha256 mismatch{label}: expected {expected}, got {actual}")
        self.expected = expected
        self.actual = actual
        self.plugin_id = plugin_id


def normalize_sha256(value: str) -> str:
    """Normalize a sha256 digest to lowercase 64-character hex.

    Raises:
        ArtifactError: If ``value`` is not a valid sha256 hex digest.
    """
    if not isinstance(value, str):
        raise ArtifactError(f"Invalid sha256 digest: {value!r}")

    text = value.strip()
    match = _SHA256_RE.match(text)
    if match is None:
        algorithm = text.split(":", 1)[0].lower() if ":" in text else ""
        if algorithm and algorithm != "sha256":
            raise ArtifactError(f"Unsupported artifact integrity algorithm: {algorithm}")
        raise ArtifactError(f"Invalid sha256 digest: {value!r}")
    return match.group(1).lower()


def _update_from_stream(digest: Any, stream: BinaryIO, chunk_size: int) -> None:
    """Feed a binary stream into ``digest`` without reading it all at once."""
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ArtifactError("Artifact stream must be opened in binary mode")
        digest.update(chunk)


def compute_sha256(source: ArtifactSource, *, chunk_size: int = CHUNK_SIZE) -> str:
    """Return the lowercase hex sha256 digest of ``source``.

    ``source`` may be a path (``str``/``os.PathLike``), in-memory bytes, or a
    readable binary stream. Files are streamed in ``chunk_size`` blocks.

    Raises:
        ArtifactError: If the artifact cannot be read or ``chunk_size`` is not
            positive.
    """
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ArtifactError("Artifact chunk size must be a positive integer")

    digest = hashlib.sha256()

    if isinstance(source, (bytes, bytearray, memoryview)):
        digest.update(source)
        return digest.hexdigest()

    if hasattr(source, "read"):
        try:
            _update_from_stream(digest, source, chunk_size)  # type: ignore[arg-type]
        except OSError as exc:
            raise ArtifactError(f"Cannot read artifact stream: {exc}") from exc
        return digest.hexdigest()

    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        try:
            with path.open("rb") as handle:
                _update_from_stream(digest, handle, chunk_size)
        except OSError as exc:
            raise ArtifactError(f"Cannot read artifact file {path}: {exc}") from exc
        return digest.hexdigest()

    raise ArtifactError(f"Unsupported artifact source: {type(source).__name__}")


def _record_mismatch(
    *,
    plugin_id: str | None,
    workspace_id: int | None,
    actor: User | None,
) -> None:
    """Best-effort bounded audit event for a refused artifact.

    The integrity error is the security-relevant outcome; a failure to write the
    audit row must not mask it, so recording failures are logged and swallowed.
    Outside an application context (for example a unit test or CLI helper with
    no workspace) there is nowhere to persist the event and recording is a
    no-op.
    """
    if workspace_id is None:
        return

    try:
        from flask import has_app_context

        if not has_app_context():
            return

        from app.services.plugin_audit import record_plugin_audit

        record_plugin_audit(
            workspace_id,
            "denied",
            actor=actor,
            plugin_id=plugin_id,
            outcome="denied",
            reason="artifact integrity mismatch",
        )
    except Exception:  # pragma: no cover - audit is best effort
        logger.warning("Could not record artifact integrity audit event", exc_info=True)


def verify_artifact_integrity(
    source: ArtifactSource,
    expected_sha256: str,
    *,
    plugin_id: str | None = None,
    workspace_id: int | None = None,
    actor: User | None = None,
) -> str:
    """Verify ``source`` against ``expected_sha256`` in constant time.

    Returns the artifact's computed (normalized) digest on success. On mismatch
    the catalog value is recorded as a plugin audit denial for ``workspace_id``
    (when provided) and :class:`ArtifactIntegrityError` is raised, so the caller
    must not register the artifact.

    Args:
        source: Artifact path, bytes, or binary stream.
        expected_sha256: Catalog digest (``sha256:<hex>``, ``sha256-<hex>``, or
            bare hex).
        plugin_id: Plugin being installed, for the error/audit facts.
        workspace_id: Workspace performing the install, for the audit event.
        actor: User performing the install, for the audit event.

    Raises:
        ArtifactIntegrityError: If the hashes do not match.
        ArtifactError: If the artifact or expected digest cannot be read/parsed.
    """
    expected = normalize_sha256(expected_sha256)
    actual = compute_sha256(source)

    if not hmac.compare_digest(actual, expected):
        _record_mismatch(plugin_id=plugin_id, workspace_id=workspace_id, actor=actor)
        raise ArtifactIntegrityError(expected, actual, plugin_id=plugin_id)

    return actual


def verify_and_register_artifact[T](
    source: ArtifactSource,
    expected_sha256: str,
    register: Callable[[str], T],
    *,
    plugin_id: str | None = None,
    workspace_id: int | None = None,
    actor: User | None = None,
) -> T:
    """Verify an artifact and only then invoke ``register``.

    ``register`` receives the verified digest and is never called for a hash
    mismatch, which keeps a tampered artifact out of the registry.

    Returns:
        The value returned by ``register``.
    """
    digest = verify_artifact_integrity(
        source,
        expected_sha256,
        plugin_id=plugin_id,
        workspace_id=workspace_id,
        actor=actor,
    )
    return register(digest)


def _artifact_names(archive: zipfile.ZipFile) -> list[str]:
    """Return validated member names, refusing duplicates and unsafe paths."""
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ArtifactError("Artifact contains duplicate entries")
    for name in names:
        parts = name.split("/")
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or ".." in parts
            or (parts and parts[0] in ("", "."))
        ):
            raise ArtifactError(f"Unsafe artifact path: {name!r}")
    return names


def validate_artifact_package(
    source: ArtifactSource,
) -> dict[str, Any]:
    """Validate a plugin artifact ``.zip`` against the published format (#382).

    Contract (see ``docs/plugin-artifact-format.md``):

    * ``manifest.json`` and a detached ``signature.json`` sit at the archive
      root (never nested in a wrapper directory);
    * ``signature.json`` is Ed25519 metadata (``publisher``/``algorithm``/``value``);
    * the manifest validates against ``plugins/plugin.schema.json``;
    * the module named by ``entry_point`` ships inside the archive;
    * member paths are relative, unique and traversal-free.

    The detached signature's **shape** is checked here; cryptographic
    verification against a trusted publisher key happens at install time
    (``enforce_publisher_trust``), so a format-valid example needs no key.

    Args:
        source: A ``.zip`` path, in-memory bytes, or a binary stream.

    Returns:
        A dict with ``id``, ``version``, ``entry_point``, ``publisher``,
        ``files`` (sorted names) and ``sha256`` of the archive.

    Raises:
        ArtifactError: If the archive is unreadable or violates the format.
    """
    from app.services.plugin_signatures import validate_signature_metadata
    from app.services.plugins import PluginManifest

    if isinstance(source, (bytes, bytearray, memoryview)):
        payload = bytes(source)
        archive_source: Any = io.BytesIO(payload)
    elif hasattr(source, "read"):
        payload = source.read()
        archive_source = io.BytesIO(payload)
    else:
        path = Path(source)
        archived = path.read_bytes()
        payload = archived
        archive_source = io.BytesIO(archived)

    try:
        archive = zipfile.ZipFile(archive_source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArtifactError(f"Artifact is not a readable zip: {exc}") from exc

    with archive:
        names = _artifact_names(archive)
        if ARTIFACT_MANIFEST not in names:
            raise ArtifactError("Artifact is missing manifest.json at the archive root")
        if ARTIFACT_SIGNATURE not in names:
            raise ArtifactError("Artifact is missing signature.json at the archive root")

        try:
            manifest = json.loads(archive.read(ARTIFACT_MANIFEST).decode("utf-8"))
            signature = json.loads(archive.read(ARTIFACT_SIGNATURE).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ArtifactError(f"Artifact JSON is invalid: {exc}") from exc

        if not isinstance(manifest, dict):
            raise ArtifactError("Artifact manifest.json must be a JSON object")
        if not isinstance(signature, dict):
            raise ArtifactError("Artifact signature.json must be a JSON object")

        signature_error = validate_signature_metadata(signature)
        if signature_error:
            raise ArtifactError(f"Artifact signature.json is invalid: {signature_error}")

        # Validate the manifest schema with any embedded signature removed: the
        # detached signature is what this format carries, and its cryptographic
        # check happens at install time.
        body = {key: value for key, value in manifest.items() if key != "signature"}
        try:
            validated = PluginManifest.from_dict(body)
        except Exception as exc:  # ManifestValidationError/PluginError
            raise ArtifactError(f"Artifact manifest is invalid: {exc}") from exc

        module = validated.entry_point.split(":", 1)[0]
        module_path = module.replace(".", "/") + ".py"
        if module_path not in names:
            raise ArtifactError(f"Artifact is missing entry point module: {module_path}")

    return {
        "id": validated.id,
        "version": validated.version,
        "entry_point": validated.entry_point,
        "publisher": signature.get("publisher"),
        "files": sorted(names),
        "sha256": compute_sha256(payload),
    }


def build_artifact_package(
    directory: str | os.PathLike[str],
    destination: str | os.PathLike[str],
) -> Path:
    """Build a deterministic plugin artifact ``.zip`` from ``directory``.

    Members are sorted and written with a fixed timestamp and mode so the same
    source tree always produces byte-identical archives (safe for the sha256 in
    a catalog). ``manifest.json`` and ``signature.json`` must be at the root of
    ``directory``.
    """
    root = Path(directory)
    if not root.is_dir():
        raise ArtifactError(f"Artifact source is not a directory: {root}")

    files = sorted(path for path in root.rglob("*") if path.is_file())
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    resolved_target = target.resolve()
    # Previously built artifacts at the source root are never repackaged.
    existing_archives = {
        path.resolve()
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() == ".zip"
    }

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            resolved = path.resolve()
            if resolved == resolved_target or resolved in existing_archives:
                continue
            arcname = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return target


__all__ = [
    "ARTIFACT_MANIFEST",
    "ARTIFACT_SIGNATURE",
    "CHUNK_SIZE",
    "ArtifactError",
    "ArtifactIntegrityError",
    "ArtifactSource",
    "build_artifact_package",
    "compute_sha256",
    "normalize_sha256",
    "validate_artifact_package",
    "verify_and_register_artifact",
    "verify_artifact_integrity",
]
