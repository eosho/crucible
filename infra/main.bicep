targetScope = 'subscription'

import { SandboxDataOwnerAssignment, SandboxNetworkConfig, WorkspaceBlobStorageConfig, WorkspaceDefinition } from './types.bicep'

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

@description('Install missing tools in the execution-user home directory on new workspaces, regardless of volume type; existing ones require explicit bootstrap.')
param workspaceBootstrapTools bool = false

@description('Optional DNS settings for private Blob storage. BYO workspaces enable storage automatically; retain {} while existing BYO volumes still need it.')
param workspaceBlobStorage WorkspaceBlobStorageConfig?

@minLength(1)
@description('Microsoft-provided sandbox image name. azure-dev supplies the base developer toolchain.')
param workspaceImage string = 'azure-dev'

@description('Tags applied to new ARM resources.')
param tags object = {
  application: 'crucible'
}

var customerBlobWorkspaces = filter(workspaces, workspace => workspace.storage.type == 'AzureBlobByo')
var workspaceBlobEnabled = !empty(customerBlobWorkspaces) || workspaceBlobStorage != null
var privateEndpointSubnetName = network.?privateEndpointSubnetName ?? ''
var privateEndpointSubnetAddressPrefix = network.?privateEndpointSubnetAddressPrefix ?? ''

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
    storageIdentityEnabled: workspaceBlobEnabled
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
    acaSubnetName: network.mode == 'newVnet' ? network.acaSubnetName : ''
    acaSubnetAddressPrefix: network.mode == 'newVnet' ? network.acaSubnetAddressPrefix : ''
    privateEndpointSubnetName: privateEndpointSubnetName
    privateEndpointSubnetAddressPrefix: privateEndpointSubnetAddressPrefix
    tags: tags
  }
}

module existingVnetSubnet './modules/network/subnet.bicep' = if (network.mode == 'existingVnet') {
  name: 'subnet-${uniqueString(resourceGroupName, sandboxGroupName)}'
  scope: resourceGroup(network.mode == 'existingVnet' ? network.resourceGroupName : resourceGroupName)
  params: {
    virtualNetworkName: network.mode == 'existingVnet' ? network.virtualNetworkName : ''
    name: network.mode == 'existingVnet' ? network.acaSubnetName : ''
    addressPrefix: network.mode == 'existingVnet' ? network.acaSubnetAddressPrefix : ''
  }
}

var selectedSubnetId = network.mode == 'existingSubnet'
  ? network.subnetId
  : (network.mode == 'existingVnet' ? existingVnetSubnet!.outputs.subnetId : newVnet!.outputs.subnetId)

var privateEndpointNetworkSubscription = network.mode == 'existingSubnet' ? split(network.subnetId, '/')[2] : subscription().subscriptionId
var privateEndpointNetworkResourceGroup = network.mode == 'existingSubnet'
  ? split(network.subnetId, '/')[4]
  : (network.mode == 'existingVnet' ? network.resourceGroupName : resourceGroupName)
var privateEndpointVirtualNetworkName = network.mode == 'existingSubnet' ? split(network.subnetId, '/')[8] : network.virtualNetworkName

module existingVnetPrivateEndpointSubnet './modules/network/private-endpoint-subnet.bicep' = if (network.mode != 'newVnet' && !empty(privateEndpointSubnetName)) {
  name: 'private-endpoint-subnet-${uniqueString(privateEndpointVirtualNetworkName, privateEndpointSubnetName)}'
  scope: resourceGroup(privateEndpointNetworkSubscription, privateEndpointNetworkResourceGroup)
  params: {
    virtualNetworkName: network.mode == 'existingVnet' ? split(existingVnetSubnet!.outputs.subnetId, '/')[8] : privateEndpointVirtualNetworkName
    name: privateEndpointSubnetName
    addressPrefix: privateEndpointSubnetAddressPrefix
  }
}

var selectedPrivateEndpointSubnetId = empty(privateEndpointSubnetName)
  ? ''
  : (network.mode == 'newVnet' ? newVnet!.outputs.privateEndpointSubnetId : existingVnetPrivateEndpointSubnet!.outputs.subnetId)

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

module workspaceBlob './modules/storage/workspace-blob.bicep' = if (workspaceBlobEnabled) {
  name: 'workspace-blob-${uniqueString(resourceGroupName, sandboxGroupName)}'
  scope: targetGroup
  params: {
    name: 'stcrucible${uniqueString(subscription().id, resourceGroupName, sandboxGroupName)}'
    sandboxGroupName: sandboxGroupName
    location: location
    privateEndpointSubnetId: selectedPrivateEndpointSubnetId
    privateDnsZoneResourceId: workspaceBlobStorage.?privateDnsZoneResourceId ?? ''
    principalId: sandboxGroup.outputs.storagePrincipalId
    workspaces: map(customerBlobWorkspaces, workspace => {
      name: workspace.name
      readOnly: workspace.storage.type == 'AzureBlobByo' ? workspace.storage.readOnly : true
    })
    tags: tags
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

@description('ARM ID of the optional non-delegated private-endpoint subnet.')
output privateEndpointSubnetId string = selectedPrivateEndpointSubnetId

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
  storageIdentityEnabled: workspaceBlobEnabled
  image: workspaceImage
  workspaces: map(workspaces, workspace => {
    name: workspace.name
    storage: workspace.storage.type == 'AzureBlobByo'
      ? {
          type: 'AzureBlobByo'
          readOnly: workspace.storage.readOnly
          storageContainerResourceId: '${workspaceBlob!.outputs.storageAccountId}/blobServices/default/containers/ws-${workspace.name}'
        }
      : workspace.storage
  })
}
