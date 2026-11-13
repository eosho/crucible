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
param acaSubnetName string

@minLength(1)
@description('Unused IPv4 CIDR inside the VNet address space.')
param acaSubnetAddressPrefix string

@description('Optional shared, non-delegated private-endpoint subnet name; empty omits it.')
param privateEndpointSubnetName string = ''

@description('CIDR for the private-endpoint subnet.')
param privateEndpointSubnetAddressPrefix string = ''

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
    subnets: concat([
      {
        name: acaSubnetName
        properties: {
          addressPrefix: acaSubnetAddressPrefix
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
    ], empty(privateEndpointSubnetName) ? [] : [
      {
        name: privateEndpointSubnetName
        properties: {
          addressPrefix: privateEndpointSubnetAddressPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ])
  }
}

@description('ARM resource ID of the new delegated subnet.')
output subnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetwork.name, acaSubnetName)

@description('ARM ID of the optional non-delegated private-endpoint subnet.')
output privateEndpointSubnetId string = empty(privateEndpointSubnetName)
  ? ''
  : resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetwork.name, privateEndpointSubnetName)
