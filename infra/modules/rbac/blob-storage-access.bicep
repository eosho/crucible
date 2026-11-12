metadata name = 'Crucible Blob storage access'
metadata description = 'Grants the sandbox group identity access to one existing customer-owned Blob container.'

targetScope = 'resourceGroup'

param storageAccountName string
param containerName string
param principalId string
param readOnly bool

var roleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  readOnly ? '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1' : 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

resource storageAccount 'Microsoft.Storage/storageAccounts@2026-06-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2026-06-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-06-01' existing = {
  parent: blobService
  name: containerName
}

resource storageAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(container.id, principalId, roleId)
  scope: container
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleId
  }
}
