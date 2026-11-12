targetScope = 'resourceGroup'

@description('Existing VNet used by the sandbox. Only a new child subnet is deployed.')
param virtualNetworkName string

@description('Unused, non-delegated subnet for the test private endpoint.')
param privateEndpointSubnetName string = 'connectivity-test-endpoints'

@description('Unused range inside the existing VNet address space.')
param privateEndpointSubnetPrefix string = '10.42.2.0/27'

@description('Entra user object ID receiving read access to the test container.')
param readerPrincipalId string

@description('Region of the existing sandbox VNet.')
param location string = resourceGroup().location

var storageName = 'stcructest${uniqueString(resourceGroup().id)}'
var containerName = 'connectivity-test'
var tags = {
  application: 'crucible'
  purpose: 'private-connectivity-test'
}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2026-01-01' existing = {
  name: virtualNetworkName
}

resource endpointSubnet 'Microsoft.Network/virtualNetworks/subnets@2026-01-01' = {
  parent: virtualNetwork
  name: privateEndpointSubnetName
  properties: {
    addressPrefix: privateEndpointSubnetPrefix
    privateEndpointNetworkPolicies: 'Disabled'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2026-06-01' = {
  name: storageName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    publicNetworkAccess: 'Disabled'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2026-06-01' = {
  parent: storage
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-06-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

resource readerAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(container.id, readerPrincipalId, 'blob-reader')
  scope: container
  properties: {
    principalId: readerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
    )
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource dnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: privateDnsZone
  name: 'crucible-connectivity-test'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2026-01-01' = {
  name: 'crucible-connectivity-test'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: endpointSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: 'blob'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2026-01-01' = {
  parent: privateEndpoint
  name: 'blob'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

output storageAccountName string = storage.name
output storageAccountId string = storage.id
output blobHostname string = '${storage.name}.blob.${environment().suffixes.storage}'
output containerName string = container.name
output privateEndpointId string = privateEndpoint.id
output privateEndpointSubnetId string = endpointSubnet.id
output privateDnsZoneId string = privateDnsZone.id
output readerRoleAssignmentId string = readerAccess.id
