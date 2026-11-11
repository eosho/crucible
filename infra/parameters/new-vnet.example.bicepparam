using '../main.bicep'

param location = 'westus2'
param sandboxGroupName = 'crucible-customer-dev'
param workspaces = [
  { name: 'alice-dev', diskSize: '20Gi' }
  { name: 'bob-dev', diskSize: '20Gi' }
]
param vnetConnectionName = 'customer-dev'
param workspaceCpu = '4000m'
param workspaceMemory = '8192Mi'
param workspaceRootDiskSize = '40Gi'
param workspaceImage = 'azure-dev'
param workspaceBootstrapTools = true

param network = {
  mode: 'newVnet'
  virtualNetworkName: 'vnet-crucible-customer-dev'
  addressPrefix: '10.42.0.0/16'
  subnetName: 'crucible'
  subnetAddressPrefix: '10.42.0.0/23'
}

param dataOwnerAssignments = []
