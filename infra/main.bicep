targetScope = 'subscription'

import { SandboxDataOwnerAssignment, SandboxNetworkConfig, WorkspaceDefinition } from './types.bicep'

@description('Target resource group. The CLI supplies --resource-group as this parameter.')
param resourceGroupName string = 'rg-crucible'

@description('Create the group only when missing. The CLI checks existence before deployment.')
param createResourceGroup bool = true

@description('Customer-approved Azure region supporting Container Apps Sandboxes.')
param location string

@minLength(2)
@maxLength(32)
@description('Sandbox group name. Use lowercase letters, digits, and hyphens.')
param sandboxGroupName string

@description('Optional existing Container Apps environment. Once linked, it cannot be changed or removed.')
param environmentId string = ''

@description('Named workspaces to provision additively; removal never deletes existing resources.')
param workspaces WorkspaceDefinition[] = []

@minLength(2)
@maxLength(32)
@description('Name of the sandbox group connection to the selected subnet.')
param vnetConnectionName string

@description('Reuse a subnet, create a subnet in an existing VNet, or create a dedicated VNet.')
param network SandboxNetworkConfig

@description('Explicit provisioning principals granted group-scoped data ownership; empty grants no access.')
param dataOwnerAssignments SandboxDataOwnerAssignment[] = []

@minLength(1)
@description('CPU default for new sandbox sessions, in millicores.')
param workspaceCpu string = '4000m'

@minLength(1)
@description('Memory default for new sandbox sessions, such as 8192Mi.')
param workspaceMemory string = '8192Mi'

@minLength(1)
@description('Explicit root disk size; root plus Data Disks must fit the CPU-based storage budget.')
param workspaceRootDiskSize string = '40Gi'

@minValue(1)
@description('Idle interval when auto-suspend is enabled for new workspaces. Auto-delete remains disabled.')
param workspaceIdleTimeoutSeconds int = 300

@description('Enable idle auto-suspend for new workspaces. Disabling keeps compute billable until stopped.')
param workspaceAutoSuspendEnabled bool = true

@description('Install missing pinned user-space tools on new workspaces; existing ones require explicit bootstrap.')
param workspaceBootstrapTools bool = false

@minLength(1)
@description('Microsoft-provided sandbox image name. azure-dev supplies the base developer toolchain.')
param workspaceImage string = 'azure-dev'

@description('Tags applied to new ARM resources.')
param tags object = {
  application: 'crucible'
}

resource targetGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = if (createResourceGroup) {
  name: resourceGroupName
  location: location
  tags: tags
}

module sandboxGroup './modules/aca/sandbox-group.bicep' = {
  name: 'sandbox-group-${uniqueString(resourceGroupName, sandboxGroupName)}'
  scope: targetGroup
  params: {
    name: sandboxGroupName
    location: location
    environmentId: environmentId
    tags: tags
  }
}

module newVnet './modules/network/virtual-network.bicep' = if (network.mode == 'newVnet') {
  name: 'vnet-${uniqueString(resourceGroupName, sandboxGroupName)}'
  scope: targetGroup
  params: {
    name: network.mode == 'newVnet' ? network.virtualNetworkName : ''
    location: location
    addressPrefix: network.mode == 'newVnet' ? network.addressPrefix : ''
    subnetName: network.mode == 'newVnet' ? network.subnetName : ''
    subnetAddressPrefix: network.mode == 'newVnet' ? network.subnetAddressPrefix : ''
    tags: tags
  }
}

module existingVnetSubnet './modules/network/subnet.bicep' = if (network.mode == 'existingVnet') {
  name: 'subnet-${uniqueString(resourceGroupName, sandboxGroupName)}'
  scope: resourceGroup(network.mode == 'existingVnet' ? network.resourceGroupName : resourceGroupName)
  params: {
    virtualNetworkName: network.mode == 'existingVnet' ? network.virtualNetworkName : ''
    name: network.mode == 'existingVnet' ? network.subnetName : ''
    addressPrefix: network.mode == 'existingVnet' ? network.subnetAddressPrefix : ''
  }
}

var selectedSubnetId = network.mode == 'existingSubnet'
  ? network.subnetId
  : (network.mode == 'existingVnet' ? existingVnetSubnet!.outputs.subnetId : newVnet!.outputs.subnetId)

module vnetConnection './modules/aca/sandbox-vnet-connection.bicep' = {
  name: 'sandbox-network-${uniqueString(resourceGroupName, sandboxGroupName)}'
  scope: targetGroup
  params: {
    sandboxGroupName: sandboxGroup.outputs.name
    location: location
    name: vnetConnectionName
    subnetId: selectedSubnetId
  }
}

module sandboxAccess './modules/rbac/sandbox-access.bicep' = {
  name: 'sandbox-access-${uniqueString(resourceGroupName, sandboxGroupName)}'
  scope: targetGroup
  params: {
    sandboxGroupName: sandboxGroup.outputs.name
    assignments: dataOwnerAssignments
  }
}

@description('Sandbox group ARM resource ID for group-scoped access assignments.')
output sandboxGroupId string = sandboxGroup.outputs.resourceId

@description('Deployed sandbox group name.')
output sandboxGroupName string = sandboxGroup.outputs.name

@description('ARM resource ID of the customer VNet connection.')
output vnetConnectionId string = vnetConnection.outputs.resourceId

@description('ARM ID of the reused or provisioned delegated subnet.')
output subnetId string = selectedSubnetId

@description('Group-scoped role assignment IDs for explicit provisioning principals.')
output dataOwnerRoleAssignmentIds array = sandboxAccess.outputs.roleAssignmentIds

@description('Non-secret session defaults consumed by the SDK after deployment.')
output workspaceSettings object = {
  resourceGroup: resourceGroupName
  location: location
  sandboxGroup: sandboxGroup.outputs.name
  vnetConnection: vnetConnectionName
  cpu: workspaceCpu
  memory: workspaceMemory
  rootDiskSize: workspaceRootDiskSize
  idleTimeoutSeconds: workspaceIdleTimeoutSeconds
  autoSuspendEnabled: workspaceAutoSuspendEnabled
  bootstrapTools: workspaceBootstrapTools
  image: workspaceImage
  workspaces: workspaces
}
