param location string
param clusterName string
param managementSourceCIDR string
param trustedSubnetCIDR string
param identities array

// An operator can manage both nodes. Workload traffic is closed until election.
// Do not use these control-plane ports for VPN listeners.
var controlRules = [
  {
    name: 'Deny-Public-Management'
    properties: {
      priority: 130
      direction: 'Inbound'
      access: 'Deny'
      protocol: 'Tcp'
      sourceAddressPrefix: 'Internet'
      sourcePortRange: '*'
      destinationAddressPrefix: '*'
      destinationPortRanges: ['22', '443']
    }
  }
  {
    name: 'Management'
    properties: {
      priority: 100
      direction: 'Inbound'
      access: 'Allow'
      protocol: 'Tcp'
      sourceAddressPrefix: managementSourceCIDR
      sourcePortRange: '*'
      destinationAddressPrefix: '*'
      destinationPortRanges: ['22', '443']
    }
  }
  {
    name: 'Peer-Management'
    properties: {
      priority: 110
      direction: 'Inbound'
      access: 'Allow'
      protocol: 'Tcp'
      sourceAddressPrefix: trustedSubnetCIDR
      sourcePortRange: '*'
      destinationAddressPrefix: '*'
      destinationPortRange: '443'
    }
  }
  {
    name: 'Role-Probe'
    properties: {
      priority: 120
      direction: 'Inbound'
      access: 'Allow'
      protocol: 'Tcp'
      sourceAddressPrefix: 'AzureLoadBalancer'
      sourcePortRange: '*'
      destinationAddressPrefix: '*'
      destinationPortRange: '8080'
    }
  }
  {
    name: 'Control-HTTPS'
    properties: {
      priority: 100
      direction: 'Outbound'
      access: 'Allow'
      protocol: 'Tcp'
      sourceAddressPrefix: '*'
      sourcePortRange: '*'
      destinationAddressPrefix: '*'
      destinationPortRanges: ['80', '443']
    }
  }
  {
    name: 'Azure-Platform'
    properties: {
      priority: 110
      direction: 'Outbound'
      access: 'Allow'
      protocol: '*'
      sourceAddressPrefix: '*'
      sourcePortRange: '*'
      destinationAddressPrefixes: ['168.63.129.16', '169.254.169.254']
      destinationPortRange: '*'
    }
  }
  {
    name: 'Time'
    properties: {
      priority: 120
      direction: 'Outbound'
      access: 'Allow'
      protocol: 'Udp'
      sourceAddressPrefix: '*'
      sourcePortRange: '*'
      destinationAddressPrefix: '*'
      destinationPortRange: '123'
    }
  }
]
var workloadRules = [for direction in ['Inbound', 'Outbound']: {
      name: 'Workload-${direction}'
      properties: {
        priority: 200
        direction: direction
        access: 'Deny'
        protocol: '*'
        sourceAddressPrefix: '*'
        sourcePortRange: '*'
        destinationAddressPrefix: '*'
        destinationPortRange: '*'
      }
    }]
resource gates 'Microsoft.Network/networkSecurityGroups@2023-05-01' = [for node in ['Primary', 'Secondary']: {
  name: '${clusterName}-${node}-Gate'
  location: location
  properties: {
    securityRules: concat(controlRules, workloadRules)
  }
}]
resource gateRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, clusterName, 'ha-gates')
  properties: {
    roleName: 'OPNazure gates ${uniqueString(resourceGroup().id, clusterName)}'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: [
        'Microsoft.Network/networkSecurityGroups/read'
        'Microsoft.Network/networkSecurityGroups/securityRules/read'
        'Microsoft.Network/networkSecurityGroups/securityRules/write'
      ]
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}
resource primaryAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (node, i) in ['Primary', 'Secondary']: {
  name: guid(gates[i].id, identities[0].principalId, gateRole.id)
  scope: gates[i]
  properties: {
    roleDefinitionId: gateRole.id
    principalId: identities[0].principalId
    principalType: 'ServicePrincipal'
  }
}]
resource secondaryAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (node, i) in ['Primary', 'Secondary']: {
  name: guid(gates[i].id, identities[1].principalId, gateRole.id)
  scope: gates[i]
  properties: {
    roleDefinitionId: gateRole.id
    principalId: identities[1].principalId
    principalType: 'ServicePrincipal'
  }
}]
output ids array = [for i in range(0, 2): gates[i].id]
