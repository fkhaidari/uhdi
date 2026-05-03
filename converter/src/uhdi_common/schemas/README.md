# uhdi JSON Schemas

Draft 2020-12 schemas extracted from [`docs/uhdi-spec.md`](../docs/uhdi-spec.md). The converter CLIs (`uhdi-to-hgldd`, `uhdi-to-hgdb`) accept `--validate` to run an input document against these schemas before conversion; the validation logic lives in [`uhdi_common.validate`](../src/uhdi_common/validate.py).

| File | Covers |
|------|--------|
| `document.schema.json` | Top-level document shape (sec.3). Entry point for validation. |
| `types.schema.json` | Types pool (sec.4). |
| `expressions.schema.json` | Expressions pool (sec.5). |
| `variables.schema.json` | Variables pool (sec.6). |
| `scopes.schema.json` | Scopes pool + scope body statements + breakpoint metadata (sec.7, sec.9). |
| `dataflow.schema.json` | Optional dataflow graph (sec.10). |
| `temporal.schema.json` | Optional temporal layer (sec.11). |
| `provenance.schema.json` | Optional provenance layer (sec.12). |

`$id` URLs use the non-routable `https://uhdi/...` prefix; cross-schema `$ref`s are resolved locally by pre-populating a schema store (see `uhdi_common.validate`).
