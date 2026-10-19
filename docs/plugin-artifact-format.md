# Plugin artifact package format

This document specifies the deterministic `.zip` artifact format used by the
plugin marketplace (#382). Publishers produce it; the installer validates it
before registering a plugin.

## Layout

An artifact is a plain `.zip` with **`manifest.json` and `signature.json` at the
archive root** (never inside a wrapper directory), plus the plugin's source
files:

```
hello-artifact-0.1.0.zip
├── manifest.json        plugin metadata (plugins/plugin.schema.json)
├── signature.json       detached Ed25519 signature metadata
├── plugin.py            entry point module named by manifest.entry_point
└── README.md            optional documentation
```

### `manifest.json`

The same manifest accepted by local installs. It must validate against
`plugins/plugin.schema.json`. The `entry_point` is `<module>:<Class>`; the
module's source file (`<module with dots as slashes>.py`) **must** be present in
the archive.

### `signature.json`

Detached Ed25519 signature metadata over the canonical manifest body:

```json
{
  "publisher": "example-publisher",
  "algorithm": "ed25519",
  "value": "<base64 signature>"
}
```

The installer verifies the signature against the operator-configured trusted
publisher keys and the active `PLUGIN_TRUST_POLICY`:

| Policy        | Unsigned artifact                     | Invalid/untrusted signature |
| ------------- | ------------------------------------- | --------------------------- |
| `if-present`  | accepted as `Unverified` (local dev)  | **always refused**          |
| `required`    | **refused before registration**       | **always refused**          |

## Rules

1. `manifest.json` and `signature.json` are at the archive root.
2. Member paths are relative, unique and contain no `..`, backslashes or
   leading `/` (no traversal).
3. The entry point module ships in the archive.
4. The archive is byte-stable: members sorted, fixed timestamps/modes. The
   catalog's `sha256` is the digest of the whole `.zip`.
5. No artifact URL is ever accepted from a user or an imported project; the
   installer only uses the operator-controlled catalog entry.

## Worked example

`examples/plugins/artifact_hello/` is the source of a valid artifact:

```
examples/plugins/artifact_hello/
├── manifest.json
├── signature.json
├── plugin.py
├── README.md
└── hello-artifact-0.1.0.zip   built, validated, committed
```

Build and validate it:

```bash
python -c "from app.services.plugin_artifacts import build_artifact_package; \
build_artifact_package('examples/plugins/artifact_hello', \
'examples/plugins/artifact_hello/hello-artifact-0.1.0.zip')"

python -c "from app.services.plugin_artifacts import validate_artifact_package; \
print(validate_artifact_package('examples/plugins/artifact_hello/hello-artifact-0.1.0.zip'))"
```

`validate_artifact_package` returns the plugin id/version/entry point, the
publisher id, the sorted member list and the archive sha256. It raises
`ArtifactError` for a missing manifest/signature, an invalid manifest, a
missing entry-point module, duplicate members or unsafe paths.

## Related

- Manifest schema: `plugins/plugin.schema.json`
- Trust policy and signatures: `app/services/plugin_signatures.py`
- Integrity verification: `app/services/plugin_artifacts.py`
- Remote install: `flask plugins install <plugin-id> --remote [--version V]`
