using '../main.bicep'

param location = 'westus2'
param sandboxGroupName = 'crucible-customer-dev'
param vnetConnectionName = 'customer-dev'
param network = {
  mode: 'existingVnet'
  resourceGroupName: 'rg-customer-network'
  virtualNetworkName: 'customer-dev'
  acaSubnetName: 'crucible'
  acaSubnetAddressPrefix: '10.42.2.0/23'
}

param dataOwnerAssignments = []
