// Active-backup topology. NSG workload gates start CLOSED on both nodes.
param Location string = resourceGroup().location
param virtualMachineName string
param virtualMachineSize string
param untrustedSubnetId string
param trustedSubnetId string
param trustedSubnetName string
param windowsSubnetName string = ''
param publicIPId string
param publicIPAddress string
param nsgIds array
param identities array
param witnessBlobUrl string
@minValue(1024)
@maxValue(65535)
param openVpnPort int = 1194
@minValue(1024)
@maxValue(65535)
param wireGuardPort int = 51820
param TempUsername string
@secure()
param TempPassword string
param FreeBSDImageVersion string
param OpnScriptURI string
param OpnVersion string
param WALinuxVersion string

var externalLoadBalanceName = '${virtualMachineName}-External-LB'
var externalLoadBalanceFIPConfName = 'FW'
var externalLoadBalanceBAPName = 'OPNSense'
var externalLoadBalanceProbeName = 'HTTPs'
var vpnPorts = [500, 4500, openVpnPort, wireGuardPort]
var externalLoadBalanceOutRuleName = 'OutBound-OPNSense'
var internalLoadBalanceName = '${virtualMachineName}-Internal-LB'
var internalLoadBalanceFIPConfName = 'FW'
var internalLoadBalanceBAPName = 'OPNSense'
var internalLoadBalanceProbeName = 'HTTPs'
var internalLoadBalancingRuleName = 'Internal-HA-Port-Rule'
var externalLoadBalanceNatRuleName1 = 'primary-nva-mgmt'
var externalLoadBalanceNatRuleName2 = 'scondary-nva-mgmt'

// External Load Balancer
module elb 'vnet/lb.bicep' = {
  name: externalLoadBalanceName
  params: {
    Location: Location
    lbName: externalLoadBalanceName
    frontendIPConfigurations: [
      {
        name: externalLoadBalanceFIPConfName
        properties: {
          publicIPAddress: {
            id: publicIPId
          }
        }
      }
    ]
    backendAddressPools: [
      {
        name: externalLoadBalanceBAPName
      }
    ]
    loadBalancingRules: [for port in vpnPorts: {
        name: 'VPN-UDP-${port}'
        properties: {
          frontendPort: port
          backendPort: port
          enableFloatingIP: false
          protocol: 'Udp'
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', externalLoadBalanceName, externalLoadBalanceFIPConfName)
          }
          disableOutboundSnat: true
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', externalLoadBalanceName, externalLoadBalanceBAPName)
          }
          probe: {
            id: resourceId('Microsoft.Network/loadBalancers/probes', externalLoadBalanceName, externalLoadBalanceProbeName)
          }
        }
      }
    ]
    inboundNatRules: [
      {
        name: externalLoadBalanceNatRuleName1
        properties: {
          frontendPort: 50443
          backendPort: 443
          protocol: 'Tcp'
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', externalLoadBalanceName, externalLoadBalanceFIPConfName)
          }
        }
      }
      {
        name: externalLoadBalanceNatRuleName2
        properties: {
          frontendPort: 50444
          backendPort: 443
          protocol: 'Tcp'
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', externalLoadBalanceName, externalLoadBalanceFIPConfName)
          }
        }
      }
    ]
    probe: [
      {
        name: externalLoadBalanceProbeName
        properties: {
          port: 8080
          protocol: 'Http'
          requestPath: '/health'
          intervalInSeconds: 5
          numberOfProbes: 2
        }
      }
    ]
    outboundRules: [
      {
        name: externalLoadBalanceOutRuleName
        properties: {
          allocatedOutboundPorts: 0
          idleTimeoutInMinutes: 4
          enableTcpReset: true
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', externalLoadBalanceName, externalLoadBalanceBAPName)
          }
          frontendIPConfigurations: [
            {
              id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', externalLoadBalanceName, externalLoadBalanceFIPConfName)
            }
          ]
          protocol: 'All'
        }
      }
    ]
  }
}

// Internal Load Balancer
module ilb 'vnet/lb.bicep' = {
  name: internalLoadBalanceName
  params: {
    Location: Location
    lbName: internalLoadBalanceName
    frontendIPConfigurations: [
      {
        name: internalLoadBalanceFIPConfName
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: trustedSubnetId
          }
          privateIPAddressVersion: 'IPv4'
        }
      }
    ]
    backendAddressPools: [
      {
        name: internalLoadBalanceBAPName
      }
    ]
    loadBalancingRules: [
      {
        name: internalLoadBalancingRuleName
        properties: {
          frontendPort: 0
          backendPort: 0
          protocol: 'All'
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', internalLoadBalanceName, internalLoadBalanceFIPConfName)
          }
          disableOutboundSnat: true
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', internalLoadBalanceName, internalLoadBalanceBAPName)
          }
          probe: {
            id: resourceId('Microsoft.Network/loadBalancers/probes', internalLoadBalanceName, internalLoadBalanceProbeName)
          }
        }
      }
    ]
    probe: [
      {
        name: internalLoadBalanceProbeName
        properties: {
          port: 8080
          protocol: 'Http'
          requestPath: '/health'
          intervalInSeconds: 5
          numberOfProbes: 2
        }
      }
    ]
  }
}


// Create both nodes' NICs before either guest. This supplies reciprocal pfsync
// peers without making one VM deployment depend on the other VM deployment.
var roles = ['Primary', 'Secondary']
module wanNics 'vnet/nic.bicep' = [for (role, i) in roles: {
  name: '${virtualMachineName}-${role}-Untrusted-NIC'
  params: {
    Location: Location
    nicName: '${virtualMachineName}-${role}-Untrusted-NIC'
    subnetId: untrustedSubnetId
    enableIPForwarding: true
    nsgId: nsgIds[i]
    loadBalancerBackendAddressPoolId: elb.outputs.backendAddressPools[0].id
    loadBalancerInboundNatRules: elb.outputs.inboundNatRules[i].id
  }
}]
module lanNics 'vnet/nic.bicep' = [for (role, i) in roles: {
  name: '${virtualMachineName}-${role}-Trusted-NIC'
  params: {
    Location: Location
    nicName: '${virtualMachineName}-${role}-Trusted-NIC'
    subnetId: trustedSubnetId
    enableIPForwarding: true
    nsgId: nsgIds[i]
    loadBalancerBackendAddressPoolId: ilb.outputs.backendAddressPools[0].id
  }
}]
resource availability 'Microsoft.Compute/availabilitySets@2023-07-01' = {
  name: '${virtualMachineName}-AvailabilitySet'
  location: Location
  sku: { name: 'Aligned' }
  properties: {
    platformFaultDomainCount: 2
    platformUpdateDomainCount: 2
  }
}
module nodes 'VM/opnsense-ha.bicep' = [for (role, i) in roles: {
  name: '${virtualMachineName}-${role}'
  params: {
    Location: Location
    virtualMachineName: '${virtualMachineName}-${role}'
    virtualMachineSize: virtualMachineSize
    untrustedSubnetId: untrustedSubnetId
    trustedSubnetId: trustedSubnetId
    nsgId: nsgIds[i]
    TempUsername: TempUsername
    TempPassword: TempPassword
    FreeBSDImageVersion: FreeBSDImageVersion
    availabilitySetId: availability.id
    managedIdentityId: identities[i].id
    haConfig: {
      node: role
      client_id: identities[i].clientId
      blob_url: witnessBlobUrl
      own_nsg_id: nsgIds[i]
      peer_nsg_id: nsgIds[1 - i]
      peer_vm_id: resourceId('Microsoft.Compute/virtualMachines', '${virtualMachineName}-${roles[1 - i]}')
      probe_port: 8080
    }
    providedNics: {
      wanId: wanNics[i].outputs.nicId
      lanId: lanNics[i].outputs.nicId
      wanIP: wanNics[i].outputs.nicIP
      lanIP: lanNics[i].outputs.nicIP
    }
    ShellScriptObj: {
      OpnScriptURI: OpnScriptURI
      OpnVersion: OpnVersion
      WALinuxVersion: WALinuxVersion
      OpnType: role
      TrustedSubnetName: trustedSubnetName
      WindowsSubnetName: windowsSubnetName
      publicIPAddress: publicIPAddress
      localTrustedIP: lanNics[i].outputs.nicIP
      peerTrustedIP: lanNics[1 - i].outputs.nicIP
    }
  }
}]
output primaryTrustedIP string = lanNics[0].outputs.nicIP
output secondaryTrustedIP string = lanNics[1].outputs.nicIP
output internalLoadBalancerIP string = ilb.outputs.frontendIP.privateIPAddress
