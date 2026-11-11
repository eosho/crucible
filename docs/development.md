# Development

[Back to overview](../README.md)

## Dev Container

Open the repository in VS Code and choose **Dev Containers: Reopen in Container**.
The [configuration](../.devcontainer/devcontainer.json) installs Python 3.12, uv,
Azure CLI, Bicep, and editor extensions for Python, Ruff, and Bicep. It runs
`uv sync --locked` when the container is created.

The container keeps `.venv` in a separate Docker volume, so it leaves the host
environment alone. Host Azure credentials aren't mounted. Run
`az login --use-device-code` inside the container before using Azure.
The full prek run also needs the external scanners listed below.

This container is for developing Crucible. Customer sandboxes use a separate
image; opening the Dev Container doesn't build or publish workspace images or
deploy Azure resources.

## Prerequisites

- Git, Python 3.12+, and uv to run the CLI and Python checks.
- Azure CLI with Bicep to deploy infrastructure and sign in to Azure.
- Bash to check the shell scripts locally. Tool bootstrap runs inside the sandbox and needs
  `jq`, `curl`, `sha256sum`, GNU tar, and standard filesystem utilities.
- External `gitleaks` and `osv-scanner` executables on `PATH` for the manual gate;
  `uv sync` does not install them.

Run `az login` before using Azure. Workspace commands use Azure Identity's
`AzureCliCredential` for the supplied `--subscription`. The
`azure-containerapps-sandbox==0.1.0b4` SDK calls use the
`2026-02-01-preview` data-plane API. Bicep uses ARM APIs chosen for each resource.
Review SDK release notes before upgrading.

## Install and check

Run checks from the repository root. [pyproject.toml](../pyproject.toml) declares
the dependencies; [uv.lock](../uv.lock) pins their resolved versions.

```bash
uv sync
uv run crucible --help
uv run pytest

az bicep build --file infra/main.bicep --stdout
bash -n src/crucible/assets/*.sh

uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest --cov=crucible
```

`uv sync` installs Crucible and its development tools into `.venv`. They aren't
added to the global shell `PATH`. Use `uv run crucible COMMAND --help` to see a
command's arguments. `uv run python -m crucible` runs the same CLI.

Run the [prek hooks](../.pre-commit-config.yaml) for formatting, lint, strict type
checks, coverage, secret scanning, and a dependency audit:

```bash
uv run prek run --all-files --hook-stage manual
```

These hooks run only when requested, not on ordinary commits. They require 100%
branch coverage. The full set of checks hasn't been verified as passing.
Ruff excludes tests from linting and formatting, even when passed explicit paths.
Basedpyright and pytest still include them.

## Conventions

- Keep CLI code in [src/crucible](../src/crucible/) and tests in
  [tests](../tests/). Don't duplicate Azure resource-schema validation in tests.
- Use action-based names and Google-style Python docstrings with `Args`, `Returns`,
  `Raises`, and `Attributes` sections where applicable.
- Log `timestamp | level | message` to stderr with standard-library logging.
  Azure SDK logs are errors-only; child-process stdout is unchanged.
- Use comments to explain intent or constraints that the code doesn't make clear.
- Keep provisioning and RBAC in Bicep. The CLI coordinates deployment and
  sandbox operations. See the [infrastructure guide](infrastructure.md).
- Group Bicep modules by service, prefer applicable stable APIs, and use symbolic
  references for inferred dependencies rather than redundant `dependsOn` entries.
- Run the checks that cover your changes. Documentation-only changes don't need
  a code test run.
