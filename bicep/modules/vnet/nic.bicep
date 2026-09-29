param subnetId string
param publicIPId string = ''
param enableIPForwarding bool = false
param nicName string
param nsgId string = ''
param loadBalancerBackendAddressPoolId string = ''
param loadBalancerInboundNatRules string = ''
param Location string = resourceGroup().location

resource nic 'Microsoft.Network/networkInterfaces@2023-05-01' = {
  name: nicName
  location: Location
  properties: {
    enableIPForwarding: enableIPForwarding
    networkSecurityGroup:{
      id: nsgId
    }
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: {
            id: subnetId
          }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: !empty(publicIPId) ? {
            id: publicIPId
          }:null
          loadBalancerBackendAddressPools: !empty(loadBalancerBackendAddressPoolId) ? [
            {
              id: loadBalancerBackendAddressPoolId
            }
          ]:null
          loadBalancerInboundNatRules: !empty(loadBalancerInboundNatRules) ? [
            {
              id: loadBalancerInboundNatRules
            }
          ]:null
        }
      }
    ]
  }
}

output nicName string = nic.name
output nicId string = nic.id
output nicIP string = nic.properties.ipConfigurations[0].properties.privateIPAddress
output nicIpConfigurationId string = nic.properties.ipConfigurations[0].id
