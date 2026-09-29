// Parameters
@sys.description('Select a valid scenario. Active Active: Two OPNSenses deployed in HA mode using SLB and ILB. Active Backup: Two nodes with a Blob lease, VM fencing and OpenVPN-aware probes. Two Nics: Single OPNSense deployed with two Nics.')
@allowed([
  'Active-Active'
  'Active-Backup'
  'TwoNics'
])
param scenarioOption string = 'TwoNics'

@sys.description('VM size, please choose a size which allow 2 NICs.')
param virtualMachineSize string = 'Standard_B2s'

@sys.description('OPN NVA Manchine Name')
param virtualMachineName string

@sys.description('Virtual Nework Name. This is a required parameter to build a new VNet or find an existing one.')
param virtualNetworkName string = 'OPN-VNET'

@sys.description('Use Existing Virtual Nework. The value must be new or existing.')
param existingvirtualNetwork string = 'new'

@sys.description('Virtual Network Address Space. Only required if you want to create a new VNet.')
param VNETAddress array = [
  '10.0.0.0/16'
]

@sys.description('Untrusted-Subnet Address Space. Only required if you want to create a new VNet.')
param UntrustedSubnetCIDR string = '10.0.0.0/24'

@sys.description('Trusted-Subnet Address Space. Only required if you want to create a new VNet.')
param TrustedSubnetCIDR string = '10.0.1.0/24'

@sys.description('Untrusted-Subnet Name. Only required if you want to use an existing VNet and Subnet.')
param existingUntrustedSubnetName string = ''

@sys.description('Trusted-Subnet Name. Only required if you want to use an existing VNet and Subnet.')
param existingTrustedSubnetName string = ''

@sys.description('Specify Public IP SKU either Basic (lowest cost) or Standard (Required for HA LB)"')
@allowed([
  'Basic'
  'Standard'
])
param PublicIPAddressSku string = 'Standard'

@sys.description('URI for Custom OPN Script and Config')
param OpnScriptURI string = 'https://raw.githubusercontent.com/Galvy/opnazure/feature/active-backup-openvpn/scripts/'

@sys.description('Shell Script to be executed')
param ShellScriptName string = 'configureopnsense.sh'

@sys.description('OPN Version')
param OpnVersion string = '26.1'

@sys.description('Azure WALinux agent Version')
param WALinuxVersion string = '2.15.0.1'

@sys.description('Deploy Windows VM Trusted Subnet')
param DeployWindows bool = false

@sys.description('Only required in case of Deploying Windows VM. Windows Admin username (Used to login in Windows VM).')
param WinUsername string = ''

@sys.description('Only required in case of Deploying Windows VM. Windows Password (Used to login in Windows VM).')
@secure()
param WinPassword string = ''

@sys.description('Existing Windows Subnet Name. Only requried in case of deploying Windows in a exising subnet.')
param existingWindowsSubnet string = ''

@sys.description('In case of deploying Windows in a New VNet this will be the Windows VM Subnet Address Space')
param DeployWindowsSubnet string = '10.0.2.0/24'

param Location string = resourceGroup().location

@sys.description('OpenVPN UDP port published by the Active-Backup load balancer.')
@minValue(1)
@maxValue(65535)
param OpenVpnPort int = 1194

@sys.description('Private HTTP probe port for the Active-Backup agent; do not expose publicly.')
@minValue(1024)
@maxValue(65535)
param HaProbePort int = 8080

@sys.description('Administrator public IPv4 CIDR allowed to reach management ports for Active-Backup. Replace the loopback default before deployment.')
param ManagementSourceCIDR string = '127.0.0.1/32'

// Variables
var isHa = scenarioOption != 'TwoNics'
var isActiveBackup = scenarioOption == 'Active-Backup'
var TempUsername = 'azureuser'
var TempPassword = guid(subscription().id,resourceGroup().id)
var untrustedSubnetName = 'Untrusted-Subnet'
var trustedSubnetName = 'Trusted-Subnet'
var VMOPNsensePrimaryName = '${virtualMachineName}-Primary'
var VMOPNsenseSecondaryName = '${virtualMachineName}-Secondary'
var publicIPAddressName = '${virtualMachineName}-PublicIP'
var networkSecurityGroupName = '${virtualMachineName}-NSG'
var externalLoadBalanceName = 'External-LoadBalance'
var externalLoadBalanceFIPConfName = 'FW'
var externalLoadBalanceBAPName = 'OPNSense'
var externalLoadBalanceProbeName = 'HTTPs'
var externalLoadBalancingRuleName = 'RDP'
var externalLoadBalanceOutRuleName = 'OutBound-OPNSense'
var internalLoadBalanceName = 'Internal-LoadBalance'
var internalLoadBalanceFIPConfName = 'FW'
var internalLoadBalanceBAPName = 'OPNSense'
var internalLoadBalanceProbeName = 'HTTPs'
var internalLoadBalancingRuleName = 'Internal-HA-Port-Rule'
var externalLoadBalanceNatRuleName1 = 'primary-nva-mgmt'
var externalLoadBalanceNatRuleName2 = 'scondary-nva-mgmt'
var useexistingvirtualNetwork = existingvirtualNetwork == 'new' ? false : true

var windowsvmsubnetname = 'Windows-VM-Subnet'
var winvmroutetablename = 'winvmroutetable'
var winvmName = 'VM-Win11Client'
var winvmnetworkSecurityGroupName = '${winvmName}-NSG'
var winvmpublicipName = '${winvmName}-PublicIP'

// Resources
// Create NSG
module nsgopnsense 'modules/vnet/nsg.bicep' = {
  name: networkSecurityGroupName
  params: {
    Location: Location
    nsgName: networkSecurityGroupName
    securityRules: concat(isActiveBackup ? [
      {
        name: 'Allow-HA-Management'
        properties: {
          priority: 100
          sourceAddressPrefix: ManagementSourceCIDR
          protocol: 'Tcp'
          destinationPortRanges: ['22', '443']
          access: 'Allow'
          direction: 'Inbound'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Deny-Internet-Management'
        properties: {
          priority: 110
          sourceAddressPrefix: 'Internet'
          protocol: 'Tcp'
          destinationPortRanges: ['22', '443']
          access: 'Deny'
          direction: 'Inbound'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
        }
      }
    ] : [], [
      {
        name: 'In-Any'
        properties: {
          priority: 4096
          sourceAddressPrefix: '*'
          protocol: '*'
          destinationPortRange: '*'
          access: 'Allow'
          direction: 'Inbound'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Out-Any'
        properties: {
          priority: 4096
          sourceAddressPrefix: '*'
          protocol: '*'
          destinationPortRange: '*'
          access: 'Allow'
          direction: 'Outbound'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
        }
      }
    ])
  }
}

// Create VNET
module vnet 'modules/vnet/vnet.bicep' = if(useexistingvirtualNetwork == false) {
  name: virtualNetworkName
  params: {
    location: Location
    vnetAddressSpace: VNETAddress
    vnetName: virtualNetworkName
    subnets: DeployWindows == true ? [
      {
        name: untrustedSubnetName
        properties: {
          addressPrefix: UntrustedSubnetCIDR
        }
      }
      {
        name: trustedSubnetName
        properties: {
          addressPrefix: TrustedSubnetCIDR
        }
      }
      {
        name: windowsvmsubnetname
        properties: {
          addressPrefix: DeployWindowsSubnet
        }
      }
    ]:[
      {
        name: untrustedSubnetName
        properties: {
          addressPrefix: UntrustedSubnetCIDR
        }
      }
      {
        name: trustedSubnetName
        properties: {
          addressPrefix: TrustedSubnetCIDR
        }
      }
    ]
  }
}

// Create OPNsense Public IP
module publicip 'modules/vnet/publicip.bicep' = {
  name: publicIPAddressName
  params: {
    location: Location
    publicipName: publicIPAddressName
    publicipproperties: {
      publicIPAllocationMethod: 'Static'
    }
    publicipsku: {
      name: isHa ? 'Standard' : PublicIPAddressSku
      tier: 'Regional'
    }
  }
}

// Build reference of existing subnets
resource untrustedSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-05-01' existing = {
  name: '${virtualNetworkName}/${useexistingvirtualNetwork ? existingUntrustedSubnetName : untrustedSubnetName}'
}

resource trustedSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-05-01' existing = {
  name: '${virtualNetworkName}/${useexistingvirtualNetwork ? existingTrustedSubnetName : trustedSubnetName}'
}

resource windowsvmsubnet 'Microsoft.Network/virtualNetworks/subnets@2023-05-01' existing = if (DeployWindows) {
  name: '${virtualNetworkName}/${useexistingvirtualNetwork ? existingWindowsSubnet : windowsvmsubnetname}'
}

// External Load Balancer
module elb 'modules/vnet/lb.bicep' = if(isHa){
  name: externalLoadBalanceName
  params: {
    Location: Location
    lbName: externalLoadBalanceName
    frontendIPConfigurations: [
      {
        name: externalLoadBalanceFIPConfName
        properties: {
          publicIPAddress: {
            id: publicip.outputs.publicipId
          }
        }
      }
    ]
    backendAddressPools: [
      {
        name: externalLoadBalanceBAPName
      }
    ]
    loadBalancingRules: [
      {
        name: isActiveBackup ? 'OpenVPN' : externalLoadBalancingRuleName
        properties: {
          frontendPort: isActiveBackup ? OpenVpnPort : 3389
          backendPort: isActiveBackup ? OpenVpnPort : 3389
          enableFloatingIP: !isActiveBackup
          protocol: isActiveBackup ? 'Udp' : 'Tcp'
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', externalLoadBalanceName, externalLoadBalanceFIPConfName)
          }
          disableOutboundSnat: true
          backendAddressPool: {
            id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', externalLoadBalanceName, externalLoadBalanceBAPName)
          }
          backendAddressPools: [
            {
              id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', externalLoadBalanceName, externalLoadBalanceBAPName)
            }
          ]
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
          port: isActiveBackup ? HaProbePort : 443
          protocol: isActiveBackup ? 'Http' : 'Tcp'
          requestPath: isActiveBackup ? '/health' : null
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
module ilb 'modules/vnet/lb.bicep' = if(isHa){
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
            id: trustedSubnet.id
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
          backendAddressPools: [
            {
              id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', internalLoadBalanceName, internalLoadBalanceBAPName)
            }
          ]
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
          port: isActiveBackup ? HaProbePort : 443
          protocol: isActiveBackup ? 'Http' : 'Tcp'
          requestPath: isActiveBackup ? '/health' : null
          intervalInSeconds: 5
          numberOfProbes: 2
        }
      }
    ]
  }
  dependsOn: [
    vnet
    nsgopnsense
    publicip
  ]
}

// Create OPNSense Active-Active
// Create OPNsense Secondary
module opnSenseSecondary 'modules/VM/opnsense.bicep' = if(isHa){
  name: VMOPNsenseSecondaryName
  params: {
    Location: Location
    managedIdentityId: isActiveBackup ? witness!.outputs.secondaryIdentity.id : ''
    availabilitySetId: isActiveBackup ? haAvailabilitySet!.id : ''
    //ShellScriptParameters: '${OpnScriptURI} Secondary ${trustedSubnet.properties.addressPrefix} ${DeployWindows ? windowsvmsubnet.properties.addressPrefix : '1.1.1.1/32'} ${publicip.outputs.publicipAddress}'
    ShellScriptObj: {
      OpnScriptURI: OpnScriptURI
      OpnVersion: OpnVersion
      WALinuxVersion: WALinuxVersion
      OpnType: 'Secondary'
      HaConfig: isActiveBackup ? base64(string({
        node: 'Secondary'
        client_id: witness!.outputs.secondaryIdentity.clientId
        blob_url: witness!.outputs.blobUrl
        peer_vm_id: resourceId('Microsoft.Compute/virtualMachines', VMOPNsensePrimaryName)
        probe_port: HaProbePort
        openvpn_port: OpenVpnPort
      })) : ''
      TrustedSubnetName: '${virtualNetworkName}/${useexistingvirtualNetwork ? existingTrustedSubnetName : trustedSubnetName}'
      WindowsSubnetName: DeployWindows ? '${virtualNetworkName}/${useexistingvirtualNetwork ? existingWindowsSubnet : windowsvmsubnetname}' : ''
      publicIPAddress: publicip.outputs.publicipAddress
      opnSenseSecondarytrustedNicIP: ''
    }
    OPNScriptURI: OpnScriptURI
    ShellScriptName: ShellScriptName
    TempPassword: TempPassword
    TempUsername: TempUsername
    multiNicSupport: true
    trustedSubnetId: trustedSubnet.id
    untrustedSubnetId: untrustedSubnet.id
    virtualMachineName: VMOPNsenseSecondaryName
    virtualMachineSize: virtualMachineSize
    nsgId: nsgopnsense.outputs.nsgID
    ExternalLoadBalancerBackendAddressPoolId: isHa ? elb!.outputs.backendAddressPools[0].id : ''
    InternalLoadBalancerBackendAddressPoolId: isHa ? ilb!.outputs.backendAddressPools[0].id : ''
    ExternalloadBalancerInboundNatRulesId: isHa ? elb!.outputs.inboundNatRules[1].id : ''
  }
  dependsOn: [
    vnet
    untrustedSubnet
    trustedSubnet
    windowsvmsubnet
  ]
}

// Create OPNsense Primary
module opnSensePrimary 'modules/VM/opnsense.bicep' = if(isHa){
  name: VMOPNsensePrimaryName
  params: {
    Location: Location
    managedIdentityId: isActiveBackup ? witness!.outputs.primaryIdentity.id : ''
    availabilitySetId: isActiveBackup ? haAvailabilitySet!.id : ''
    //ShellScriptParameters: '${OpnScriptURI} Primary ${TrustedSubnetCIDR} ${DeployWindows ? windowsvmsubnet.properties.addressPrefix : '1.1.1.1/32'} ${publicip.outputs.publicipAddress} ${opnSenseSecondary!.outputs.trustedNicIP}'
    ShellScriptObj: {
      OpnScriptURI: OpnScriptURI
      OpnVersion: OpnVersion
      WALinuxVersion: WALinuxVersion
      OpnType: 'Primary'
      HaConfig: isActiveBackup ? base64(string({
        node: 'Primary'
        client_id: witness!.outputs.primaryIdentity.clientId
        blob_url: witness!.outputs.blobUrl
        peer_vm_id: resourceId('Microsoft.Compute/virtualMachines', VMOPNsenseSecondaryName)
        probe_port: HaProbePort
        openvpn_port: OpenVpnPort
      })) : ''
      TrustedSubnetName: '${virtualNetworkName}/${useexistingvirtualNetwork ? existingTrustedSubnetName : trustedSubnetName}'
      WindowsSubnetName: DeployWindows ? '${virtualNetworkName}/${useexistingvirtualNetwork ? existingWindowsSubnet : windowsvmsubnetname}' : ''
      publicIPAddress: publicip.outputs.publicipAddress
      opnSenseSecondarytrustedNicIP: isHa ? opnSenseSecondary!.outputs.trustedNicIP : ''
    }
    OPNScriptURI: OpnScriptURI
    ShellScriptName: ShellScriptName
    TempPassword: TempPassword
    TempUsername: TempUsername
    multiNicSupport: true
    trustedSubnetId: trustedSubnet.id
    untrustedSubnetId: untrustedSubnet.id
    virtualMachineName: VMOPNsensePrimaryName
    virtualMachineSize: virtualMachineSize
    nsgId: nsgopnsense.outputs.nsgID
    ExternalLoadBalancerBackendAddressPoolId: isHa ? elb!.outputs.backendAddressPools[0].id : ''
    InternalLoadBalancerBackendAddressPoolId: isHa ? ilb!.outputs.backendAddressPools[0].id : ''
    ExternalloadBalancerInboundNatRulesId: isHa ? elb!.outputs.inboundNatRules[0].id : ''
  }
  dependsOn: [
    vnet
  ]
}

// Create OPNsense TwoNics
module opnSenseTwoNics 'modules/VM/opnsense.bicep' = if(scenarioOption == 'TwoNics'){
  name: '${virtualMachineName}-TwoNics'
  params: {
    Location: Location
    //ShellScriptParameters: '${OpnScriptURI} TwoNics ${trustedSubnet.properties.addressPrefix} ${DeployWindows ? windowsvmsubnet.properties.addressPrefix: '1.1.1.1/32'}'
    ShellScriptObj: {
      OpnScriptURI: OpnScriptURI
      OpnVersion: OpnVersion
      WALinuxVersion: WALinuxVersion
      OpnType: 'TwoNics'
      TrustedSubnetName: '${virtualNetworkName}/${useexistingvirtualNetwork ? existingTrustedSubnetName : trustedSubnetName}'
      WindowsSubnetName: DeployWindows ? '${virtualNetworkName}/${useexistingvirtualNetwork ? existingWindowsSubnet : windowsvmsubnetname}' : ''
      publicIPAddress: ''
      opnSenseSecondarytrustedNicIP: ''
    }
    OPNScriptURI: OpnScriptURI
    ShellScriptName: ShellScriptName
    TempPassword: TempPassword
    TempUsername: TempUsername
    multiNicSupport: true
    trustedSubnetId: trustedSubnet.id
    untrustedSubnetId: untrustedSubnet.id
    virtualMachineName: virtualMachineName
    virtualMachineSize: virtualMachineSize
    publicIPId: publicip.outputs.publicipId
    nsgId: nsgopnsense.outputs.nsgID
  }
  dependsOn: [
    vnet
    trustedSubnet
  ]
}

// Windows11 Client Resources
module nsgwinvm 'modules/vnet/nsg.bicep' = if (DeployWindows) {
  name: winvmnetworkSecurityGroupName
  params: {
    Location: Location
    nsgName: winvmnetworkSecurityGroupName
    securityRules: [
      {
        name: 'RDP'
        properties: {
          priority: 4096
          sourceAddressPrefix: '*'
          protocol: 'Tcp'
          destinationPortRange: '3389'
          access: 'Allow'
          direction: 'Inbound'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Out-Any'
        properties: {
          priority: 4096
          sourceAddressPrefix: '*'
          protocol: '*'
          destinationPortRange: '*'
          access: 'Allow'
          direction: 'Outbound'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
  dependsOn: [
    opnSenseSecondary
    opnSensePrimary
    opnSenseTwoNics
  ]
}

module winvmpublicip 'modules/vnet/publicip.bicep' = if (DeployWindows) {
  name: winvmpublicipName
  params: {
    location: Location
    publicipName: winvmpublicipName
    publicipproperties: {
      publicIPAllocationMethod: 'Static'
    }
    publicipsku: {
      name: isHa ? 'Standard' : PublicIPAddressSku
      tier: 'Regional'
    }
  }
  dependsOn: [
    opnSenseSecondary
    opnSensePrimary
    opnSenseTwoNics
  ]
}

module winvmroutetable 'modules/vnet/routetable.bicep' = if (DeployWindows) {
  name: winvmroutetablename
  params: {
    location: Location
    rtName: winvmroutetablename
  }
  dependsOn: [
    opnSenseSecondary
    opnSensePrimary
    opnSenseTwoNics
  ]
}

module winvmroutetableroutes 'modules/vnet/routetableroutes.bicep' = if (DeployWindows) {
  name: '${winvmroutetablename}-default'
  params: {
    routetableName: winvmroutetablename
    routeName: 'default'
    properties: {
      nextHopType: 'VirtualAppliance'
      nextHopIpAddress: isHa ? ilb!.outputs.frontendIP.privateIPAddress : scenarioOption == 'TwoNics' ? opnSenseTwoNics!.outputs.trustedNicIP : ''
      addressPrefix: '0.0.0.0/0'
    }
  }
  dependsOn: [
    winvmroutetable
  ]
}

module winvm 'modules/VM/windows11-vm.bicep' = if (DeployWindows) {
  name: winvmName
  params: {
    Location: Location
    nsgId: DeployWindows ? any(nsgwinvm).outputs.nsgID : ''
    publicIPId: DeployWindows ? any(winvmpublicip).outputs.publicipId : ''
    TempUsername: WinUsername
    TempPassword: WinPassword
    trustedSubnetId: windowsvmsubnet.id
    virtualMachineName: winvmName
    virtualMachineSize: 'Standard_B4ms'
  }
  dependsOn: [
    opnSenseSecondary
    opnSensePrimary
    opnSenseTwoNics
  ]
}

// HA witness is deployed before VM bootstrap; peer-scoped permissions follow VM creation.
module witness 'modules/ha/witness.bicep' = if (isActiveBackup) {
  name: '${virtualMachineName}-ha-witness'
  params: {
    location: Location
    clusterName: virtualMachineName
  }
}
module primaryFenceAccess 'modules/ha/fence-access.bicep' = if (isActiveBackup) {
  name: '${virtualMachineName}-primary-fence-access'
  params: {
    peerVmName: VMOPNsenseSecondaryName
    principalId: witness!.outputs.primaryIdentity.principalId
    roleDefinitionId: witness!.outputs.fenceRoleId
  }
  dependsOn: [opnSenseSecondary]
}
module secondaryFenceAccess 'modules/ha/fence-access.bicep' = if (isActiveBackup) {
  name: '${virtualMachineName}-secondary-fence-access'
  params: {
    peerVmName: VMOPNsensePrimaryName
    principalId: witness!.outputs.secondaryIdentity.principalId
    roleDefinitionId: witness!.outputs.fenceRoleId
  }
  dependsOn: [opnSensePrimary]
}
output publicIpAddress string = publicip.outputs.publicipAddress
output internalNextHop string = isHa ? ilb!.outputs.frontendIP.privateIPAddress : opnSenseTwoNics!.outputs.trustedNicIP
output activeBackupSetup string = isActiveBackup ? 'Configure OpenVPN on both nodes, then follow docs/active-backup.md to arm HA. Probes remain down until armed.' : ''

resource haAvailabilitySet 'Microsoft.Compute/availabilitySets@2023-07-01' = if (isActiveBackup) {
  name: '${virtualMachineName}-HA'
  location: Location
  sku: { name: 'Aligned' }
  properties: {
    platformFaultDomainCount: 2
    platformUpdateDomainCount: 2
  }
}
