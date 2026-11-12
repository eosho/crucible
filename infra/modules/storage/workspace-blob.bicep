metadata name = 'Crucible private workspace Blob storage'
metadata description = 'Creates one private Storage account and a container per BYO-backed workspace.'

targetScope = 'resourceGroup'

param name string
param sandboxGroupName string
param location string
param privateEndpointSubnetId string
param privateDnsZoneResourceId string = ''
param principalId string
param workspaces {
  name: string
  readOnly: bool
}[]
param tags object = {}

var subnetParts = split(privateEndpointSubnetId, '/')
var virtualNetworkId = join(take(subnetParts, 9), '/')
var createDnsZone = empty(privateDnsZoneResourceId)

resource storage 'Microsoft.Storage/storageAccounts@2026-06-01' = {
  name: name
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

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-06-01' = [for workspace in workspaces: {
  parent: blobService
  name: 'ws-${workspace.name}'
  properties: {
    publicAccess: 'None'
  }
}]

module storageAccess '../rbac/blob-storage-access.bicep' = [for (workspace, index) in workspaces: {
  name: 'blob-access-${uniqueString(storage.id, workspace.name)}'
  params: {
    storageAccountName: storage.name
    containerName: containers[index].name
    principalId: principalId
    readOnly: workspace.readOnly
  }
}]

resource dnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (createDnsZone) {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource dnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (createDnsZone) {
  parent: dnsZone
  name: 'workspace-blob-${uniqueString(virtualNetworkId)}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetworkId
    }
  }
}

resource endpoint 'Microsoft.Network/privateEndpoints@2026-01-01' = {
  name: '${sandboxGroupName}-workspace-blob'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
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
  parent: endpoint
  name: 'blob'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: {
          privateDnsZoneId: createDnsZone ? dnsZone!.id : privateDnsZoneResourceId
        }
      }
    ]
  }
}

output storageAccountId string = storage.id
