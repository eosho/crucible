# Infrastructure

[Back to overview](../README.md)

Run commands from the repository root. Bicep creates ARM resources and assigns
RBAC roles. The Python SDK creates individual sandboxes and storage volumes.

## Configure a customer

1. Copy an example from [infra/parameters/](../infra/parameters/) to a
   `*.local.bicepparam` file in that directory. Keep `using` pointed at
   [main.bicep](../infra/main.bicep).
2. Set `location`, the sandbox group and connection names, and the customer's
   network details. The CLI supplies the resource group name and creates the
   group if it doesn't exist.
3. Set defaults for new workspaces and [declare workspaces](workspaces.md#provision-declared-workspaces):

```bicep
param workspaceCpu = '4000m'
param workspaceMemory = '8192Mi'
param workspaceRootDiskSize = '40Gi'
param workspaceIdleTimeoutSeconds = 900
param workspaceAutoSuspendEnabled = true
param workspaceImage = 'azure-dev'
param workspaceBootstrapTools = true
```

The CLI reads the deployment's `workspaceSettings` output. These defaults apply
only to new workspaces. Azure validates the sizing and
[root-plus-data disk budget](workspaces.md#persistent-storage).

Set `workspaceAutoSuspendEnabled = false` to keep new workspaces running while
idle. Compute remains billable until they stop. [Workspace tools](tools.md)
explains missing-tool setup on Microsoft's public image. Crucible doesn't
provision a registry or image-building infrastructure.

## Choose networking

| `network.mode` | Behavior |
|---|---|
| `existingSubnet` | Reuse an empty subnet delegated to `Microsoft.App/environments` |
| `existingVnet` | Create a dedicated delegated subnet in an existing VNet |
| `newVnet` | Create a dedicated VNet and delegated subnet |

Keep both subnet definitions in `network`:

```bicep
param network = {
  mode: 'newVnet'
  virtualNetworkName: 'vnet-crucible-dev'
  addressPrefix: '10.44.0.0/16'
  acaSubnetName: 'sandboxes'
  acaSubnetAddressPrefix: '10.44.0.0/23'
  privateEndpointSubnetName: 'private-endpoints'
  privateEndpointSubnetAddressPrefix: '10.44.2.0/27'
}
```

- Use the sandbox group's subscription and region, with approved, non-overlapping
  subnet ranges inside the VNet.
- BYO Blob needs both private-endpoint fields for a separate, non-delegated subnet.
- For `existingVnet`, specify the VNet's resource group and grant deployment access.

Configure customer routing, DNS forwarding, firewalls, and peering separately.
VNet injection alone doesn't restrict outbound access.

## Grant access

Use Entra object IDs, not client IDs, in `dataOwnerAssignments`. Set
`principalType` to `User`, `Group`, or `ServicePrincipal`. An empty list grants no
access. The deployment identity needs permission to create subscription
deployments, resources, and role assignments at the required scopes.

Container Apps SandboxGroup Data Owner grants access to every workspace in the
group. Use separate groups when workload identities or individual developers
mustn't have access to teammates' workspaces. A provisioning service for a
shared group must enforce its own authorization.

Choosing [BYO Blob storage](workspaces.md#persistent-storage) automatically
enables its infrastructure. Configure the private-endpoint subnet in `network`.
Bicep creates the private account, containers, endpoint, DNS, and group identity,
then grants container-scoped reader or contributor access. The deployment identity
needs permission to create those resources and role assignments.

## Deploy

Preview ARM changes, then apply:

```bash
uv run crucible deploy --subscription SUBSCRIPTION --resource-group RESOURCE_GROUP \
  --deployment crucible --parameters infra/parameters/customer.local.bicepparam --what-if

uv run crucible deploy --subscription SUBSCRIPTION --resource-group RESOURCE_GROUP \
  --deployment crucible --parameters infra/parameters/customer.local.bicepparam --apply
```

With `--apply`, Crucible:

1. Runs a subscription-scoped Bicep deployment to create a missing resource group
   and deploy the sandbox group, networking, and requested RBAC assignments.
2. Uses the SDK to create missing workspaces and their declared volumes, then
   installs missing tools if bootstrap is enabled.
   Existing workspaces are [left unchanged](workspaces.md#provision-declared-workspaces).

| Execution flag | Behavior |
|---|---|
| Neither | Compile parameters and check group existence; make no changes |
| `--what-if` | Preview ARM only; no SDK provisioning or disk/sandbox preview |
| `--apply` | Deploy ARM resources, then provision missing workspace pairs |

Use either `--apply` or `--what-if`, never both. Existing resource groups keep
their location and tags. An ARM failure stops the command before SDK provisioning.
An SDK failure leaves resources already created in place. Inspect partial
workspaces before retrying, and allow time for new role assignments to propagate.

## Redeploy safely

- Review network changes with `--what-if`: `newVnet` and `existingVnet` can remove
  Azure-attached NSG associations absent from the template. Use `existingSubnet`
  to preserve the existing sandbox subnet's configuration.
- Use distinct `--deployment` names per environment within a subscription
  (default: `crucible`). Use the same name for all workspace commands.
- Workspace commands require subscription-level deployment outputs and a
  matching resource group.
- Keep the deployment record's metadata location consistent.
- Preserve a linked `environmentId`; Azure does not allow changing or removing it.
- Removing a principal from the parameters doesn't revoke its role assignment.
  Get approval and revoke access separately.

The [VNet connection module](../infra/modules/aca/sandbox-vnet-connection.bicep)
includes `location`, which the API requires even though the published Bicep
schema omits it.
