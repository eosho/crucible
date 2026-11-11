metadata name = 'Crucible sandbox access'
metadata description = 'Grants explicit principals data ownership at sandbox-group scope only.'

targetScope = 'resourceGroup'

import { SandboxDataOwnerAssignment } from '../../types.bicep'

@description('Name of the sandbox group in this resource group.')
param sandboxGroupName string

@description('Provisioning principals only; group data ownership includes every workspace in the group.')
param assignments SandboxDataOwnerAssignment[] = []

var dataOwnerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'c24cf47c-5077-412d-a19c-45202126392c'
)

resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-07-01' existing = {
  name: sandboxGroupName
}

resource dataOwners 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for assignment in assignments: {
  name: guid(sandboxGroup.id, toLower(assignment.principalId), dataOwnerRoleId)
  scope: sandboxGroup
  properties: {
    principalId: assignment.principalId
    principalType: assignment.principalType
    roleDefinitionId: dataOwnerRoleId
  }
}]

@description('ARM IDs of the group-scoped role assignments.')
output roleAssignmentIds array = [for (assignment, index) in assignments: dataOwners[index].id]
