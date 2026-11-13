metadata name = 'Crucible private-endpoint subnet'
metadata description = 'Adds a non-delegated private-endpoint subnet without replacing its existing parent VNet.'

targetScope = 'resourceGroup'

param virtualNetworkName string
param name string
param addressPrefix string

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2026-01-01' existing = {
  name: virtualNetworkName
}

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2026-01-01' = {
  parent: virtualNetwork
  name: name
  properties: {
    addressPrefix: addressPrefix
    privateEndpointNetworkPolicies: 'Disabled'
  }
}

output subnetId string = subnet.id
