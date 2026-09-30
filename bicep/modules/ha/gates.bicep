param location string
param clusterName string
param managementSourceCIDR string
param trustedSubnetCIDR string

// Manual selection: only Primary initially admits workload traffic and LB probes.
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
resource gates 'Microsoft.Network/networkSecurityGroups@2023-05-01' = [for node in ['Primary', 'Secondary']: {
  name: '${clusterName}-${node}-Gate'
  location: location
  properties: {
    securityRules: concat(controlRules, [
      {
        name: 'Role-Probe'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: node == 'Primary' ? 'Allow' : 'Deny'
          protocol: 'Tcp'
          sourceAddressPrefix: 'AzureLoadBalancer'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'Workload-Inbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: node == 'Primary' ? 'Allow' : 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Workload-Outbound'
        properties: {
          priority: 200
          direction: 'Outbound'
          access: node == 'Primary' ? 'Allow' : 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ])
  }
}]
output ids array = [for i in range(0, 2): gates[i].id]
