<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/crucible-mark-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/crucible-mark-light.svg">
    <img src="docs/assets/crucible-mark-light.svg" alt="Crucible logo" width="144" height="144">
  </picture>
  <br>
  Crucible
</h1>

<p align="center">
  <a href="#status"><img src="https://img.shields.io/badge/status-experimental-f59e0b" alt="Status: experimental"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.12%2B-3776ab" alt="Python 3.12+"></a>
  <a href="https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview"><img src="https://img.shields.io/badge/Azure-Container%20Apps%20Sandboxes-0078d4" alt="Azure Container Apps Sandboxes"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-16a34a" alt="License: MIT"></a>
</p>

<a id="status"></a>

> [!WARNING]
> Crucible is a proof of concept and isn't ready for production use.
> Deployment settings and CLI behavior may change. Test in an approved
> subscription and review each plan before applying it.

Crucible creates development workspaces in a customer's Azure subscription using
Azure Container Apps Sandboxes. Each workspace has a shared toolchain and
authenticated remote access.

Deploy it separately into each customer's approved subscription.

Bicep creates the infrastructure and assigns RBAC roles. The Python CLI manages
workspaces and persistent disks through the Azure Container Apps Sandbox SDK.
Microsoft's `azure-dev` image supplies the base tools; bootstrap can install
missing tools.

## Get started

With Python 3.12+ and uv installed, run:

```bash
uv sync
uv run crucible --help
```

Review the plan before adding `--apply`. Workspace plans read Azure deployment
outputs; the guides below cover deployment and authentication.

## CLI

Cloud commands require Azure CLI and `az login`. Pass `--subscription` and
`--resource-group` explicitly. `--deployment` defaults to `crucible`.

### Deploy

Configure a [parameter file](infra/parameters/), then plan the deployment:

```bash
uv run crucible deploy --subscription SUBSCRIPTION --resource-group RESOURCE_GROUP \
  --parameters infra/parameters/customer.local.bicepparam
```

| Execution flag | Behavior |
|---|---|
| None | Compile parameters and check resource-group existence; no changes |
| `--what-if` | Preview ARM changes only |
| `--apply` | Deploy infrastructure and provision missing workspaces/disks |

Use either `--what-if` or `--apply`, never both. Provisioning leaves existing
workspaces alone, without rebuilding or deleting them.

### Workspaces

Workspace commands need settings from a successful subscription deployment.
Replace `ACTION` and add the required arguments:

```bash
uv run crucible workspace ACTION --subscription SUBSCRIPTION \
  --resource-group RESOURCE_GROUP --deployment crucible
```

| Action | Additional required arguments | Purpose |
|---|---|---|
| `provision` | None | Create missing declared workspaces and dedicated disks |
| `create` | `--name NAME --volume VOLUME` | Create a workspace using an existing unattached Data Disk |
| `bootstrap` | `--name NAME` | Install missing tools on a running workspace |
| `auto-suspend` | `--name NAME` and `--enable` or `--disable` | Toggle idle suspension without restarting |
| `stop` | `--name NAME` | Suspend a workspace |
| `resume` | `--name NAME` | Resume a stopped workspace |

Add `--apply` to execute a workspace command. Without it, the command only plans.
You pay for compute while a workspace runs, including when auto-suspend is
disabled. See [workspaces](docs/workspaces.md) for persistence, bootstrap, and
remote access.

For command-specific help:

```bash
uv run crucible deploy --help
uv run crucible workspace --help
uv run crucible workspace auto-suspend --help
```

## Contributing

Use the optional [Dev Container](docs/development.md#dev-container) to work on Crucible.

## Guides

- [Infrastructure](docs/infrastructure.md): deployment, networking, RBAC, workspace inventory.
- [Workspaces](docs/workspaces.md): persistent disks, lifecycle, remote access, verification status.
- [Tools](docs/tools.md): Bootstrapped tools and default image.
- [Development](docs/development.md): prerequisites, local checks, code conventions.

## License

Released under the [MIT license](LICENSE).
