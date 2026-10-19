"""Tests for #387, #390, #382 and #444.

- #387 trust policy enforced consistently on remote installs (both policies).
- #390 remote install through the ``flask plugins install`` CLI.
- #382 plugin artifact package format + shipped example validation.
- #444 deterministic cost rounding and aggregate precision.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from app.models import Plugin
from app.services import pricing, usage_ledger
from app.services.plugin_artifacts import (
    ArtifactError,
    build_artifact_package,
    validate_artifact_package,
)
from app.services.plugin_ops import install_from_catalog
from app.services.plugin_signatures import (
    InvalidPublisherSignatureError,
    MissingPublisherSignatureError,
)

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "plugins" / "artifact_hello"
EXAMPLE_ZIP = EXAMPLE_DIR / "hello-artifact-0.1.0.zip"

MANIFEST = {
    "id": "tap-plugin",
    "name": "Tap Plugin",
    "version": "0.1.0",
    "description": "A catalog plugin",
    "author": "tester",
    "entry_point": "plugins.tap:TapPlugin",
    "capabilities": ["PROJECT_READ"],
}


def _catalog_file(tmp_path, *, signature=None, manifest=None):
    entry = {
        "id": "tap-plugin",
        "version": "0.1.0",
        "source": "https://plugins.example.com/tap-0.1.0.zip",
        "integrity": "a" * 64,
        "manifest": manifest or MANIFEST,
    }
    if signature is not None:
        entry["signature"] = signature
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"version": 1, "plugins": [entry]}), encoding="utf-8")
    return path


def _config(catalog_path, policy="if-present"):
    return {
        "PLUGIN_CATALOG_FILE": str(catalog_path),
        "PLUGIN_TRUST_POLICY": policy,
        "PLUGIN_TRUSTED_KEYS": {},
    }


# --------------------------------------------------------------------------
# #387 trust policy on remote install
# --------------------------------------------------------------------------


class TestRemoteInstallTrustPolicy:
    def test_required_refuses_unsigned_before_registration(self, db, tmp_path):
        config = _config(_catalog_file(tmp_path), policy="required")

        with pytest.raises(MissingPublisherSignatureError):
            install_from_catalog("tap-plugin", config=config)

        assert db.session.get(Plugin, "tap-plugin") is None

    def test_if_present_accepts_unsigned_as_unverified(self, db, tmp_path):
        config = _config(_catalog_file(tmp_path), policy="if-present")

        plugin, entry = install_from_catalog("tap-plugin", config=config)

        assert plugin.id == "tap-plugin"
        assert plugin.trust_state == "Unverified"
        assert entry.version == "0.1.0"
        assert db.session.get(Plugin, "tap-plugin") is not None

    def test_untrusted_signature_refused_under_both_policies(self, db, tmp_path):
        signature = {
            "publisher": "acme",
            "algorithm": "ed25519",
            "value": "AAAA",
        }
        for policy in ("if-present", "required"):
            config = _config(_catalog_file(tmp_path, signature=signature), policy=policy)
            with pytest.raises(InvalidPublisherSignatureError):
                install_from_catalog("tap-plugin", config=config)
            assert db.session.get(Plugin, "tap-plugin") is None
            db.session.rollback()

    def test_local_install_still_works_under_default_policy(self, db, tmp_path):
        from app.services.plugin_ops import install_from_local_dir

        directory = tmp_path / "local"
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")

        plugin = install_from_local_dir(str(directory))

        assert plugin.id == "tap-plugin"


# --------------------------------------------------------------------------
# #390 remote install CLI
# --------------------------------------------------------------------------


class TestRemoteInstallCli:
    def _invoke(self, app, *args):
        return app.test_cli_runner().invoke(args=["plugins", "install", *args])

    def test_remote_install_success_json(self, app, db, tmp_path):
        app.config["PLUGIN_CATALOG_FILE"] = str(_catalog_file(tmp_path))
        app.config["PLUGIN_TRUST_POLICY"] = "if-present"
        app.config["PLUGIN_TRUSTED_KEYS"] = {}

        result = self._invoke(app, "tap-plugin", "--remote", "--json")

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["id"] == "tap-plugin"
        assert payload["source"] == "https://plugins.example.com/tap-0.1.0.zip"
        assert payload["integrity"] == "sha256:" + "a" * 64

    def test_remote_install_trust_refusal(self, app, db, tmp_path):
        app.config["PLUGIN_CATALOG_FILE"] = str(_catalog_file(tmp_path))
        app.config["PLUGIN_TRUST_POLICY"] = "required"
        app.config["PLUGIN_TRUSTED_KEYS"] = {}

        result = self._invoke(app, "tap-plugin", "--remote")

        assert result.exit_code == 2
        assert "Trust policy" in result.output
        assert db.session.get(Plugin, "tap-plugin") is None

    def test_remote_install_unknown_plugin(self, app, db, tmp_path):
        app.config["PLUGIN_CATALOG_FILE"] = str(_catalog_file(tmp_path))
        app.config["PLUGIN_TRUST_POLICY"] = "if-present"

        result = self._invoke(app, "ghost-plugin", "--remote")

        assert result.exit_code == 1
        assert "not in the plugin catalog" in result.output

    def test_remote_install_invalid_version(self, app, db, tmp_path):
        app.config["PLUGIN_CATALOG_FILE"] = str(_catalog_file(tmp_path))
        app.config["PLUGIN_TRUST_POLICY"] = "if-present"

        result = self._invoke(app, "tap-plugin", "--remote", "--version", "not-a-version")

        assert result.exit_code == 2

    def test_remote_install_catalog_unavailable(self, app, db):
        app.config["PLUGIN_CATALOG_FILE"] = ""
        result = self._invoke(app, "tap-plugin", "--remote")
        assert result.exit_code == 2


# --------------------------------------------------------------------------
# #382 artifact package format
# --------------------------------------------------------------------------


class TestArtifactFormat:
    def test_shipped_example_artifact_validates(self):
        result = validate_artifact_package(EXAMPLE_ZIP)

        assert result["id"] == "artifact-hello"
        assert result["version"] == "0.1.0"
        assert result["entry_point"] == "plugin:HelloArtifactPlugin"
        assert result["publisher"] == "example-publisher"
        assert "manifest.json" in result["files"]
        assert "signature.json" in result["files"]
        assert "plugin.py" in result["files"]
        assert len(result["sha256"]) == 64

    def test_rebuild_is_byte_deterministic(self, tmp_path):
        rebuilt = tmp_path / "rebuilt.zip"
        build_artifact_package(EXAMPLE_DIR, rebuilt)

        assert rebuilt.read_bytes() == EXAMPLE_ZIP.read_bytes()

    def _zip(self, tmp_path, members: dict[str, bytes], name="bad.zip"):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            for member_name, data in members.items():
                archive.writestr(member_name, data)
        return path

    def test_missing_manifest_is_refused(self, tmp_path):
        path = self._zip(tmp_path, {"signature.json": b"{}", "plugin.py": b""})
        with pytest.raises(ArtifactError, match=r"manifest\.json"):
            validate_artifact_package(path)

    def test_missing_signature_is_refused(self, tmp_path):
        path = self._zip(tmp_path, {"manifest.json": b"{}", "plugin.py": b""})
        with pytest.raises(ArtifactError, match=r"signature\.json"):
            validate_artifact_package(path)

    def test_path_traversal_is_refused(self, tmp_path):
        manifest = json.dumps(MANIFEST).encode()
        signature = json.dumps({"publisher": "p", "algorithm": "ed25519", "value": "AAAA"}).encode()
        path = self._zip(
            tmp_path,
            {
                "manifest.json": manifest,
                "signature.json": signature,
                "../escape.py": b"x",
            },
        )
        with pytest.raises(ArtifactError, match="Unsafe artifact path"):
            validate_artifact_package(path)

    def test_missing_entry_module_is_refused(self, tmp_path):
        manifest = dict(MANIFEST, id="artifact-hello", entry_point="plugin:Hello")
        signature = {"publisher": "p", "algorithm": "ed25519", "value": "AAAA"}
        path = self._zip(
            tmp_path,
            {
                "manifest.json": json.dumps(manifest).encode(),
                "signature.json": json.dumps(signature).encode(),
            },
        )
        with pytest.raises(ArtifactError, match="entry point module"):
            validate_artifact_package(path)

    def test_invalid_signature_shape_is_refused(self, tmp_path):
        manifest = json.dumps(MANIFEST).encode()
        signature = json.dumps({"publisher": "p", "algorithm": "rsa", "value": "AAAA"}).encode()
        path = self._zip(
            tmp_path,
            {
                "manifest.json": manifest,
                "signature.json": signature,
                "plugins/tap.py": b"",
            },
        )
        with pytest.raises(ArtifactError, match=r"signature\.json"):
            validate_artifact_package(path)


# --------------------------------------------------------------------------
# #444 cost precision and rounding
# --------------------------------------------------------------------------


class TestCostRounding:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.0000004, 0.0),
            (0.0000005, 0.000001),
            (0.0000015, 0.000002),
            (1.2345674, 1.234567),
            (1.2345675, 1.234568),
            (30.0, 30.0),
        ],
    )
    def test_round_cost_half_up(self, value, expected):
        assert pricing.round_cost(value) == expected

    def test_round_cost_preserves_none(self):
        assert pricing.round_cost(None) is None

    def test_estimate_cost_is_rounded(self):
        # 1 prompt token at 0.0025/1k = 0.0000025 -> half-up 0.000003.
        estimate = pricing.estimate_cost_usd("openai", "gpt-4o", prompt_tokens=1)
        assert estimate == 0.000003
        assert estimate == pricing.round_cost(estimate)

    def test_record_usage_rounds_explicit_cost(self, db):
        record = usage_ledger.record_usage(source="chat", cost_estimate="1.2345675")
        assert record.cost_estimate == 1.234568

    def test_summarize_records_rounds_aggregate_half_up(self):
        class Row:
            def __init__(self, cost):
                self.prompt_tokens = 0
                self.completion_tokens = 0
                self.total_tokens = 0
                self.tokens_estimated = False
                self.cost_estimate = cost

        # 0.0000004 + 0.0000001 = 0.0000005 -> half-up 0.000001
        # (built-in round() would give 0.0 under banker's rounding).
        summary = usage_ledger.summarize_records([Row(0.0000004), Row(0.0000001)])
        assert summary["cost_estimate"] == 0.000001

    def test_workspace_cost_estimate_uses_same_precision(self, db, make_user):
        from app.models import Workspace

        user = make_user()
        workspace = Workspace(user_id=user.id, name="W")
        db.session.add(workspace)
        db.session.commit()
        pricing.set_workspace_price_override(
            workspace,
            "openai",
            "gpt-4o",
            input_per_1k_usd=10.0,
            output_per_1k_usd=20.0,
            user=user,
        )

        estimate = pricing.estimate_cost_usd(
            "openai", "gpt-4o", prompt_tokens=1, workspace=workspace
        )

        assert estimate == pricing.round_cost(estimate)
        assert estimate == 0.01
