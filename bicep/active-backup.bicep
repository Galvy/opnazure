// Manual maintenance only: Primary initially active, Secondary isolated.
// Dedicated fresh-deployment entry point. No enterprise routes or VPN instances.
param location string = resourceGroup().location
@minLength(1)
@maxLength(40)
param clusterName string = 'opn-vpn'
@description('A VM SKU supporting two NICs, available in the selected region.')
param virtualMachineSize string = 'Standard_B2s'
param vnetCIDR string = '10.80.0.0/16'
param wanSubnetCIDR string = '10.80.0.0/24'
param trustedTransitCIDR string = '10.80.1.0/24'
param serversSubnetCIDR string = '10.80.2.0/24'
@description('Public IPv4 CIDR permitted to manage the two firewalls. Required.')
@minLength(9)
param managementSourceCIDR string
@description('Temporary Azure image administrator password; OPNsense uses its initial root credentials after conversion.')
@secure()
@minLength(12)
param bootstrapAdminPassword string
param imageVersion string = 'latest'
@description('Source provenance; this template embeds all fork bootstrap artifacts. No raw GitHub download is needed for these files.')
param scriptURI string = 'https://raw.githubusercontent.com/Galvy/opnazure/feature/active-backup-vpn-site/scripts/'
@minValue(1024)
@maxValue(65535)
param openVpnPort int = 1194
@minValue(1024)
@maxValue(65535)
param wireGuardPort int = 51820

var vnetName = '${clusterName}-VNet'
module vnet 'modules/vnet/vnet.bicep' = {
  name: '${clusterName}-network'
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
module gates 'modules/ha/gates.bicep' = {
  name: '${clusterName}-gates'
  params: {
    location: location
    clusterName: clusterName
    managementSourceCIDR: managementSourceCIDR
    trustedSubnetCIDR: trustedTransitCIDR
  }
}
module publicIP 'modules/vnet/publicip.bicep' = {
  name: '${clusterName}-public-ip'
  params: {
    location: location
    publicipName: '${clusterName}-PublicIP'
    publicipsku: { name: 'Standard', tier: 'Regional' }
    publicipproperties: { publicIPAllocationMethod: 'Static' }
  }
}
module pair 'modules/active-backup.bicep' = {
  name: '${clusterName}-pair'
  params: {
    Location: location
    virtualMachineName: clusterName
    virtualMachineSize: virtualMachineSize
    untrustedSubnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'WAN')
    trustedSubnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, 'Trusted-Transit')
    trustedSubnetName: '${vnetName}/Trusted-Transit'
    publicIPId: publicIP.outputs.publicipId
    publicIPAddress: publicIP.outputs.publicipAddress
    nsgIds: gates.outputs.ids
    TempUsername: 'azureuser'
    TempPassword: bootstrapAdminPassword
    FreeBSDImageVersion: imageVersion
    OpnScriptURI: scriptURI
    OpnVersion: '26.7'
    WALinuxVersion: '2.15.0.1'
    openVpnPort: openVpnPort
    wireGuardPort: wireGuardPort
  }
  dependsOn: [vnet]
}
output publicIPAddress string = publicIP.outputs.publicipAddress
output primaryManagementURL string = 'https://${publicIP.outputs.publicipAddress}:50443'
output secondaryManagementURL string = 'https://${publicIP.outputs.publicipAddress}:50444'
output internalNextHop string = pair.outputs.internalLoadBalancerIP
output primaryTrustedIP string = pair.outputs.primaryTrustedIP
output secondaryTrustedIP string = pair.outputs.secondaryTrustedIP
output setup string = 'Change root passwords; configure VPNs/routing on both nodes; see docs/active-backup.md. No workload routes or VPN instances are installed.'
