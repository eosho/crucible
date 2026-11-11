# Workspaces

[Back to overview](../README.md)

Use [`deploy --apply`](infrastructure.md#deploy) to create infrastructure and
missing workspaces together. The PoC uses `azure-dev` with
[missing-tool bootstrap](tools.md). Run commands from the repository root.

## Provision declared workspaces

Declare stable names and disk sizes in your Bicep parameter file:

```bicep
param workspaces = [
  { name: 'alice-dev', diskSize: '20Gi' }
  { name: 'bob-dev', diskSize: '20Gi' }
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

The CLI checks every inventory entry before creating any missing disk/sandbox pairs:

- Existing workspaces stay as they are, including stopped workspaces. Changing
  defaults doesn't recreate or resize them, or update their policies.
- Removing or renaming an entry doesn't delete its old sandbox or disk.
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

Each workspace gets a dedicated Data Disk named `<workspace-name>-data`, mounted
at `/workspaces`. Keep source, Git data, and project state there. A disk attaches
to one sandbox at a time. Set ownership for the user running your tools instead
of making the directory world-writable.

The root disk and Data Disks must fit the CPU-based storage budget. Set
`workspaceRootDiskSize` explicitly; otherwise Azure allocates the full budget to
the root disk. Push work to source control and back up data before deleting a
group or volume. A persistent disk isn't a backup.

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

For an existing workspace, deploy current Bicep settings first, then review the
policy change:

```bash
uv run crucible workspace auto-suspend --subscription SUBSCRIPTION \
  --resource-group RESOURCE_GROUP --deployment crucible --name alice-dev --disable
# Repeat with --apply. Use --enable to restore idle suspension.
```

The command preserves the current timeout, Disk mode, and auto-delete policy.
The [official SDK lifecycle sample](https://github.com/Azure-Samples/azure-container-apps-sandboxes/blob/main/python/labs/01-getting-started.ipynb)
uses the same lifecycle API. Provisioning and default-setting changes leave
existing workspace policies alone.

## Create with an existing disk

To create a workspace outside the declared inventory, supply an existing,
unattached Data Disk dedicated to that workspace:

```bash
uv run crucible workspace create --subscription SUBSCRIPTION --resource-group RESOURCE_GROUP \
  --deployment crucible --name alice-dev --volume alice-dev-data
# Review, then repeat with --apply.
```

The command rejects an existing workspace name. It attaches the disk and sets
the same lifecycle policy. It doesn't create the disk or label it for inventory
ownership.

## Connect remotely

Run `code tunnel --name alice-dev` in the sandbox's interactive shell.
After bootstrap, open a new shell to use `crucible-tunnel --name alice-dev`,
the [packaged helper](../src/crucible/assets/connect-tunnel.sh).

Get customer approval for Microsoft Dev Tunnels, review the license, and sign in
interactively. Pass `--accept-server-license-terms` only if you agree to the license.
Open the printed `vscode.dev` URL with the same account, or use VS Code Remote - Tunnels.
Keep the outbound tunnel process running in the foreground. Restart it after
Disk suspension. Crucible doesn't configure a public SSH endpoint.

Azure RBAC, repository access, and Copilot entitlement are separate permissions.
Store secrets in protected user configuration or an approved secret store.
Don't put them in command arguments, images, Git, or Bicep parameters.

## Validate customer readiness

Before customer use, check:

- Image startup, tool setup, and disk ownership.
- Private DNS, access to private services, and egress restrictions.
- Editor and agent connections, access boundaries, and persistence after suspend/resume.
- Time to a usable workspace and ongoing costs.
