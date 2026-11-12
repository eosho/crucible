using '../main.bicep'

param location = 'eastus'
param sandboxGroupName = 'crucible-storage-dev'
param vnetConnectionName = 'storage-network'
param network = {
  mode: 'newVnet'
  virtualNetworkName: 'vnet-crucible-storage-dev'
  addressPrefix: '10.44.0.0/16'
  acaSubnetName: 'sandboxes'
  acaSubnetAddressPrefix: '10.44.0.0/23'
  privateEndpointSubnetName: 'private-endpoints'
  privateEndpointSubnetAddressPrefix: '10.44.2.0/27'
}

param workspaceBootstrapTools = true
param workspaces = [
  {
    name: 'alice-dev'
    storage: { type: 'DataDisk', size: '20Gi' }
  }
  {
    name: 'ernest-dev'
    storage: { type: 'AzureBlob', readOnly: false }
  }
  {
    name: 'crucible-dev'
    storage: {
      type: 'AzureBlobByo'
      readOnly: false
    }
  }
]

param dataOwnerAssignments = []
