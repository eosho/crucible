metadata name = 'Crucible virtual network'
metadata description = 'Creates a dedicated VNet and delegated sandbox subnet with explicit address ranges.'

targetScope = 'resourceGroup'

@minLength(1)
@description('Name of the new, dedicated virtual network.')
param name string

@description('Customer-approved region, matching the sandbox group.')
param location string = resourceGroup().location

@minLength(1)
@description('Explicit customer-approved IPv4 address space.')
param addressPrefix string

@minLength(1)
@description('Name of the dedicated sandbox subnet.')
param subnetName string

@minLength(1)
@description('Unused IPv4 CIDR inside the VNet address space.')
param subnetAddressPrefix string

@description('Tags applied to the dedicated virtual network.')
param tags object = {}

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2026-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [addressPrefix]
    }
    subnets: [
      {
        name: subnetName
        properties: {
          addressPrefix: subnetAddressPrefix
          delegations: [
            {
              name: 'container-apps'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

@description('ARM resource ID of the new delegated subnet.')
output subnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetwork.name, subnetName)
