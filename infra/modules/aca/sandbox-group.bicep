metadata name = 'Crucible sandbox group'
metadata description = 'Deploys the customer-scoped sandbox group without exposing ingress or assigning workload identities.'

targetScope = 'resourceGroup'

@minLength(2)
@maxLength(32)
@description('Sandbox group name. The caller must enforce the resource naming pattern.')
param name string

@description('Azure region supporting sandbox groups; keep connected VNets in the same region.')
param location string = resourceGroup().location

@description('Optional existing Container Apps environment ARM ID in this subscription.')
param environmentId string = ''

@description('Tags applied to the sandbox group.')
param tags object = {}

// The environment link is immutable. Preserve it in every subsequent PUT.
resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-07-01' = {
  name: name
  location: location
  tags: tags
  properties: empty(environmentId) ? {} : {
    environmentId: environmentId
  }
}

@description('Fully qualified ARM resource ID of the sandbox group.')
output resourceId string = sandboxGroup.id

@description('Deployed sandbox group name.')
output name string = sandboxGroup.name

@description('Region of the deployed sandbox group.')
output location string = sandboxGroup.location
