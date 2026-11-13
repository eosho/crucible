using '../main.bicep'

param location = 'eastus'
param sandboxGroupName = 'crucible-copilot-dev'
param workspaces = [
  { name: 'alice-dev', storage: { type: 'DataDisk', size: '20Gi' } }
  { name: 'bob-dev', storage: { type: 'DataDisk', size: '20Gi' } }
]
param vnetConnectionName = 'copilot-network'
param network = {
  mode: 'newVnet'
  virtualNetworkName: 'vnet-crucible-copilot-dev'
  addressPrefix: '10.43.0.0/16'
  acaSubnetName: 'sandboxes'
  acaSubnetAddressPrefix: '10.43.0.0/23'
}

param workspaceCpu = '4000m'
param workspaceMemory = '8192Mi'
param workspaceRootDiskSize = '40Gi'
param workspaceIdleTimeoutSeconds = 900
param workspaceImage = 'copilot'

// Grant approved provisioning principals separately from GitHub/Copilot sign-in.
param dataOwnerAssignments = []
