using '../main.bicep'

param location = 'westus2'
param sandboxGroupName = 'crucible-customer-dev'
param vnetConnectionName = 'customer-dev'
param workspaces = []
param network = {
  mode: 'existingSubnet'
  subnetId: '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-customer-network/providers/Microsoft.Network/virtualNetworks/customer-dev/subnets/crucible'
}

// Add approved provisioning principals; empty means no new data-owner grants.
param dataOwnerAssignments = []
