# Artifact Hello (example plugin artifact)

This directory is the source of the example artifact described in
[`docs/plugin-artifact-format.md`](../../../docs/plugin-artifact-format.md).

Layout:

```
manifest.json    plugin metadata (validated against plugins/plugin.schema.json)
signature.json   detached Ed25519 signature metadata
plugin.py        entry point module (matches entry_point "plugin:HelloArtifactPlugin")
```

Build the deterministic `.zip` and validate it:

```bash
python -c "from app.services.plugin_artifacts import build_artifact_package; \
build_artifact_package('examples/plugins/artifact_hello', \
'examples/plugins/artifact_hello/hello-artifact-0.1.0.zip')"

python -c "from app.services.plugin_artifacts import validate_artifact_package; \
print(validate_artifact_package('examples/plugins/artifact_hello/hello-artifact-0.1.0.zip'))"
```

The committed `hello-artifact-0.1.0.zip` is built from exactly these files.
