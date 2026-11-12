---
applyTo: "**"
---

# Crucible

Crucible provisions customer-hosted development workspaces on Azure Container
Apps Sandboxes. Use Python 3.12+, uv, argparse, and Bicep.

## Code

- Keep parsing, logging, and dispatch in `src/crucible/cli.py`, ARM operations in
  `deployment.py`, and sandbox SDK operations in `workspaces.py`.
- Bicep owns Azure resources and RBAC. The native sandbox SDK owns individual
  sandboxes and volumes.
- Keep changes small. Reuse existing helpers and Bicep modules; avoid new
  frameworks, duplicated deployment templates, or unnecessary abstractions.
- Preserve additive provisioning: preflight the inventory before mutations.
  Never automatically rebuild, resize, restart, remount, or delete existing
  workspaces or volumes.
- Require `--apply` for mutations. Keep plans read-only and report failures
  explicitly; don't add automatic repair or rollback.
- Use action-based names, proper types, and Google-style Python docstrings.
  Comment only intent or constraints that aren't clear from the code.
- Log `timestamp | level | message` to stderr. Keep Azure SDK logs at ERROR;
  preserve child-process stdout.
- Group Bicep modules by service. Prefer applicable stable resource APIs and
  symbolic references for dependencies.
- Keep credentials out of source, logs, images, and Bicep parameters. Group
  identities and data-owner roles grant access across the sandbox group.

## Checks and documentation

- Run focused tests for changed behavior. Don't duplicate Azure resource-schema
  validation in tests.
- Use `uv run --locked ruff check .`, `uv run --locked ruff format --check .`,
  `uv run --locked basedpyright src/crucible`, and `uv run --locked pytest`.
- Compile changed Bicep and affected parameter examples with Azure CLI. Don't
  apply live deployments unless requested.
- Keep documentation brief and practical. Put instructions in one guide and
  link to them elsewhere. Update docs when behavior or configuration changes.
- Describe current behavior. Omit compatibility guarantees, migration notes,
  and implementation history from user guides unless requested.
- Documentation-only edits don't need a code test run. The full manual prek
  checks and 100% branch-coverage requirement aren't assumed to pass.
