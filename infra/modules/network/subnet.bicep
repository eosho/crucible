metadata name = 'Crucible subnet'
metadata description = 'Creates a dedicated delegated subnet without redeploying its existing parent VNet.'

targetScope = 'resourceGroup'

@minLength(1)
@description('Existing VNet name; deploy this module in its resource group.')
param virtualNetworkName string

@minLength(1)
@description('Dedicated subnet name, not an unrelated subnet to overwrite.')
param name string

@minLength(1)
@description('Unused IPv4 CIDR inside the existing VNet address space.')
param addressPrefix string

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2026-01-01' existing = {
  name: virtualNetworkName
}

// Deploy only the child so unrelated existing subnets are not replaced.
resource subnet 'Microsoft.Network/virtualNetworks/subnets@2026-01-01' = {
  parent: virtualNetwork
  name: name
  properties: {
    addressPrefix: addressPrefix
    delegations: [
      {
        name: 'container-apps'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

@description('ARM resource ID of the dedicated delegated subnet.')
output subnetId string = subnet.id
