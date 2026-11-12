# Workspaces

[Back to overview](../README.md)

Use [`deploy --apply`](infrastructure.md#deploy) to create infrastructure and
missing workspaces together. The PoC uses `azure-dev` with
[missing-tool bootstrap](tools.md). Run commands from the repository root.

## Provision declared workspaces

Declare stable names and storage in your Bicep parameter file:

```bicep
param workspaces = [
  { name: 'alice-dev', storage: { type: 'DataDisk', size: '20Gi' } }
  { name: 'bob-dev', storage: { type: 'DataDisk', size: '20Gi' } }
]
```

To check or retry workspace provisioning without redeploying infrastructure:

```bash
uv run crucible workspace provision --subscription SUBSCRIPTION \
  --resource-group RESOURCE_GROUP --deployment crucible
# Review, then repeat with --apply.
```

Workspace commands read settings from the selected subscription deployment.
Use its `--deployment` name. Without `--apply`, commands read Azure state without
changing it.

The CLI checks every inventory entry before creating any missing volume/sandbox pairs:

- Existing workspaces and storage stay unchanged, including stopped workspaces.
- Removing or renaming an entry doesn't delete the sandbox or volume.
  The inventory doesn't autoscale workspaces, cap the group, or limit costs.
- Duplicate names, ownership conflicts, missing or orphaned disks, and incomplete
  or transitional pairs stop provisioning. The CLI doesn't adopt manual resources.
- A failure stops the batch. Nothing is rolled back, deleted, or repaired
  automatically. Inspect and recover partial workspaces before retrying.

When tool setup is enabled, it runs only for newly created workspaces. Changing
defaults doesn't bootstrap or rebuild an existing workspace.

Use one provisioning operator at a time. The checks don't lock the inventory.
Ownership labels don't restrict access; see [group access](infrastructure.md#grant-access).

## Persistent storage

Each workspace mounts `<workspace-name>-data` at `/workspaces`:

| `storage.type` | Required fields | Use |
|---|---|---|
| `DataDisk` | `size`, such as `'20Gi'` | Repositories, dependencies, and build caches |
| `AzureBlob` | `readOnly` | Platform-managed Blob storage |
| `AzureBlobByo` | `readOnly` | Private Blob storage created by Bicep |

See the [three-type example](../infra/parameters/storage.example.bicepparam).
Data Disk provides full POSIX semantics and attaches to one sandbox at a time.
Use Blob for read-heavy data, not concurrent writers or workloads requiring
hardlinks and atomic renames. Bootstrap installs in the user's home with any type.

BYO requires the [private-endpoint subnet settings](infrastructure.md#choose-networking).
Bicep creates the account, containers, endpoint, DNS, and group identity. Public,
anonymous, and account-key access are disabled; private endpoints incur charges.
Read-only containers get Blob Data Reader; writable ones get Contributor.
The identity is shared across the group, so read-only mounts aren't user isolation.

For existing private DNS, set `workspaceBlobStorage.privateDnsZoneResourceId` to
an already-linked zone. Keep `workspaceBlobStorage = {}` and the subnet settings
if you remove all BYO entries before retiring their workspaces.

Set `workspaceRootDiskSize` so root plus Data Disks fit the CPU storage budget.
Back up data before deleting a group or volume.

## Stop and resume

```bash
uv run crucible workspace stop --subscription SUBSCRIPTION --resource-group RESOURCE_GROUP \
  --deployment crucible --name alice-dev --apply
uv run crucible workspace resume --subscription SUBSCRIPTION --resource-group RESOURCE_GROUP \
  --deployment crucible --name alice-dev --apply
```

Commands reject missing or ambiguous names. New workspaces use Disk suspension,
as required for Data Disks, with auto-delete disabled. Set
`workspaceIdleTimeoutSeconds` for new workspaces (default: `300`).
Idle suspension is on by default. `workspaceAutoSuspendEnabled = false`
turns it off for newly created workspaces only.

Wait for Running after a resume, then restart tunnels and development servers,
reconnect agent sessions, and recheck credentials. An active tunnel may prevent
idling. Storage and networking can still incur charges while compute is stopped.
Crucible doesn't automatically delete workspaces or disks.

### Keep a workspace running

Disable idle auto-suspend to keep a workspace running without heartbeat requests.
Compute stays billable while it runs. This won't start a stopped workspace or
prevent manual stops or service interruptions.

To change idle suspension on an existing workspace, review the policy change:

```bash
uv run crucible workspace auto-suspend --subscription SUBSCRIPTION \
  --resource-group RESOURCE_GROUP --deployment crucible --name alice-dev --disable
# Repeat with --apply. Use --enable to restore idle suspension.
```

The command preserves the current timeout, Disk mode, and auto-delete policy.
The [official SDK lifecycle sample](https://github.com/Azure-Samples/azure-container-apps-sandboxes/blob/main/python/labs/01-getting-started.ipynb)
uses the same lifecycle API. Provisioning and default-setting changes leave
existing workspace policies alone.

## Create with an existing volume

To create a workspace outside the inventory, supply an existing volume.
Data Disks must be unattached and dedicated to that workspace. Blob volumes
may already be mounted elsewhere; use `--read-only` for read sharing.

```bash
uv run crucible workspace create --subscription SUBSCRIPTION --resource-group RESOURCE_GROUP \
  --deployment crucible --name alice-dev --volume alice-dev-data
# Review, then repeat with --apply.
```

The command rejects an existing workspace name. It mounts the volume and sets
the same lifecycle policy. It doesn't create the volume or label it for inventory
ownership.

## Connect remotely

Follow the [README connection guide](../README.md#connect-to-your-workspace)
for local VS Code (recommended) or browser access.

Azure RBAC, repository access, and Copilot entitlement are separate permissions.
Store secrets in protected user configuration or an approved secret store.
Don't put them in command arguments, images, Git, or Bicep parameters.

For private-access tests and customer-readiness checks, see [Validation](validation.md).
