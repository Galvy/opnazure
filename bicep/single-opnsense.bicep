// One firewall, two NICs, direct public IP. Manual VPN/routing configuration.
param location string = resourceGroup().location
@minLength(1)
@maxLength(40)
param virtualMachineName string = 'opn-cloud'
@description('An x64 VM size supporting at least two NICs.')
param virtualMachineSize string = 'Standard_B2s'
param vnetCIDR string = '10.80.0.0/16'
param wanSubnetCIDR string = '10.80.0.0/24'
param trustedTransitCIDR string = '10.80.1.0/24'
param serversSubnetCIDR string = '10.80.2.0/24'
@description('Your public IPv4 CIDR allowed to manage OPNsense over HTTPS.')
@minLength(9)
param managementSourceCIDR string
@description('Temporary FreeBSD administrator password. Change OPNsense root credentials after conversion.')
@secure()
@minLength(12)
param bootstrapAdminPassword string
param imageVersion string = 'latest'
@description('Source provenance only; fork bootstrap files are embedded in the ARM template.')
param scriptURI string = 'https://raw.githubusercontent.com/Galvy/opnazure/feature/single-opnsense-site/scripts/'
@minValue(1024)
@maxValue(65535)
param openVpnPort int = 1194
@minValue(1024)
@maxValue(65535)
param wireGuardPort int = 51820

var vnetName = '${virtualMachineName}-VNet'
module vnet 'modules/vnet/vnet.bicep' = {
  name: '${virtualMachineName}-network'
  params: {
    location: location
    vnetName: vnetName
    vnetAddressSpace: [vnetCIDR]
    subnets: [
      { name: 'WAN', properties: { addressPrefix: wanSubnetCIDR } }
      { name: 'Trusted-Transit', properties: { addressPrefix: trustedTransitCIDR } }
      { name: 'Trusted-Servers', properties: { addressPrefix: serversSubnetCIDR } }
    ]
  }
}
module publicIP 'modules/vnet/publicip.bicep' = {
  name: '${virtualMachineName}-public-ip'
  params: {
    location: location
    publicipName: '${virtualMachineName}-PublicIP'
    publicipsku: { name: 'Standard', tier: 'Regional' }
    publicipproperties: { publicIPAllocationMethod: 'Static' }
  }
}
module nsg 'modules/vnet/nsg.bicep' = {
  name: '${virtualMachineName}-security'
  params: {
    Location: location
    nsgName: '${virtualMachineName}-NSG'
    securityRules: [
      {
        name: 'Management-HTTPS'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: managementSourceCIDR
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '50443'
        }
      }
      {
        name: 'Deny-Public-Management'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Deny'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRanges: ['22', '443', '50443']
        }
      }
      {
        name: 'VPN-UDP'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Udp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRanges: union(['500', '4500'], [string(openVpnPort), string(wireGuardPort)])
        }
      }
    ]
  }
}
module firewall 'modules/VM/opnsense-embedded.bicep' = {
  name: '${virtualMachineName}-firewall'
  params: {
    Location: location
    virtualMachineName: virtualMachineName
    virtualMachineSize: virtualMachineSize
    TempUsername: 'azureuser'
    TempPassword: bootstrapAdminPassword
    FreeBSDImageVersion: imageVersion
    untrustedSubnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'WAN')
    trustedSubnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'Trusted-Transit')
    publicIPId: publicIP.outputs.publicipId
    nsgId: nsg.outputs.nsgID
    ShellScriptObj: {
      OpnScriptURI: scriptURI
      managementPort: 50443
      OpnVersion: '26.7'
      WALinuxVersion: '2.15.0.1'
      OpnType: 'TwoNics'
      TrustedSubnetName: '${vnetName}/Trusted-Transit'
      WindowsSubnetName: ''
      publicIPAddress: ''
    }
  }
  dependsOn: [vnet]
}
output publicIPAddress string = publicIP.outputs.publicipAddress
output managementURL string = 'https://${publicIP.outputs.publicipAddress}:50443'
output trustedNextHop string = firewall.outputs.trustedNicIP
output wanPrivateIPAddress string = firewall.outputs.untrustedNicIP
output serversSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'Trusted-Servers')
output setup string = 'One firewall, no HA. Change root password and configure VPNs, firewall rules, NAT and Azure/guest routes manually. See docs/single-opnsense.md.'
