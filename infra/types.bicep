@export()
@sealed()
@description('An explicit principal receiving sandbox-group data ownership across all workspaces in that group.')
type SandboxDataOwnerAssignment = {
  @description('Entra object ID, not an application/client ID.')
  @minLength(36)
  @maxLength(36)
  principalId: string
  @description('Explicit principal type avoids role-assignment replication ambiguity.')
  principalType: 'User' | 'Group' | 'ServicePrincipal'
}

@sealed()
@description('Reuse a customer-managed subnet already delegated to Microsoft.App/environments.')
type ExistingSandboxSubnet = {
  @description('Select existingSubnet to reference a subnet without changing its configuration.')
  mode: 'existingSubnet'
  @description('Existing empty, delegated subnet ARM ID in this subscription and region.')
  @minLength(1)
  subnetId: string
}

@sealed()
@description('Create a dedicated delegated sandbox subnet in an existing customer VNet.')
type ExistingSandboxVirtualNetwork = {
  @description('Select existingVnet to create only the sandbox subnet, not the parent VNet.')
  mode: 'existingVnet'
  @description('Existing VNet resource group in the deployment subscription.')
  @minLength(1)
  resourceGroupName: string
  @description('Existing VNet resource name, not a resource ID.')
  @minLength(1)
  virtualNetworkName: string
  @description('Dedicated subnet name; must not identify an unrelated existing subnet.')
  @minLength(1)
  subnetName: string
  @description('Unused IPv4 CIDR inside the existing VNet address space.')
  @minLength(1)
  subnetAddressPrefix: string
}

@sealed()
@description('Create a customer-scoped VNet and dedicated delegated sandbox subnet.')
type NewSandboxVirtualNetwork = {
  @description('Select newVnet to manage the new VNet and its declared subnets.')
  mode: 'newVnet'
  @description('Resource name for the new VNet.')
  @minLength(1)
  virtualNetworkName: string
  @description('Customer-approved VNet IPv4 CIDR; avoid overlap with connected networks.')
  @minLength(1)
  addressPrefix: string
  @description('Resource name for the delegated sandbox subnet.')
  @minLength(1)
  subnetName: string
  @description('Dedicated IPv4 CIDR inside addressPrefix; /23 is a recommended starting point.')
  @minLength(1)
  subnetAddressPrefix: string
}

@export()
@discriminator('mode')
@description('Sandbox network configuration; all referenced VNets must use the sandbox group subscription and region.')
type SandboxNetworkConfig = ExistingSandboxSubnet | ExistingSandboxVirtualNetwork | NewSandboxVirtualNetwork

@export()
@sealed()
@description('Additive workspace inventory entry: a stable developer workspace and its dedicated persistent disk.')
type WorkspaceDefinition = {
  @description('Stable workspace name. Use 2-32 lowercase letters, digits, or hyphens.')
  @minLength(2)
  @maxLength(32)
  name: string
  @description('Persistent Data Disk size, such as 20Gi. Existing disks are never resized.')
  @minLength(1)
  diskSize: string
}
