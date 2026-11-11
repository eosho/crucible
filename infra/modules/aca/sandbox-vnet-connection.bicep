metadata name = 'Crucible sandbox VNet connection'
metadata description = 'Attaches a sandbox group to an existing customer-owned delegated subnet.'

targetScope = 'resourceGroup'

@description('Name of the sandbox group in this resource group.')
param sandboxGroupName string

@description('Region matching the parent sandbox group; required by the live connection API.')
param location string = resourceGroup().location

@minLength(2)
@maxLength(32)
@description('Connection name used when creating individual sandboxes.')
param name string

@description('Existing subnet ARM ID; the customer owns delegation, routing, and DNS.')
param subnetId string

resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-07-01' existing = {
  name: sandboxGroupName
}

resource connection 'Microsoft.App/sandboxGroups/vnetConnections@2026-07-01' = {
  parent: sandboxGroup
  name: name
  // The live API requires location although its published proxy-resource schema omits it.
  #disable-next-line BCP187
  location: location
  properties: {
    subnetId: subnetId
  }
}

@description('ARM resource ID of the deployed VNet connection.')
output resourceId string = connection.id
