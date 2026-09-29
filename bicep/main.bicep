// Parameters
@sys.description('This test branch supports one OPNsense VM with two NICs.')
@allowed([
  'TwoNics'
])
param scenarioOption string = 'TwoNics'

@sys.description('VM size, please choose a size which allow 2 NICs.')
param virtualMachineSize string = 'Standard_B2s'

@sys.description('OPN NVA Manchine Name')
param virtualMachineName string

@sys.description('Virtual Nework Name. This is a required parameter to build a new VNet or find an existing one.')
param virtualNetworkName string = 'OPN-VNET'

@sys.description('Use a new or existing virtual network in this resource group.')
@allowed(['new', 'existing'])
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

@sys.description('Standard Public IP for explicit outbound connectivity.')
@allowed([
  'Standard'
])
param PublicIPAddressSku string = 'Standard'

@sys.description('URI for Custom OPN Script and Config')
param OpnScriptURI string = 'https://raw.githubusercontent.com/Galvy/opnazure/update/freebsd15-opnsense26.7/scripts/'

@sys.description('Bootstrap entry point for this branch')
@allowed(['configureopnsense.sh'])
param ShellScriptName string = 'configureopnsense.sh'

@sys.description('OPN Version')
@allowed(['26.7'])
param OpnVersion string = '26.7'

@sys.description('Minimum azure-agent package version; installed from the OPNsense repository.')
param WALinuxVersion string = '2.15.0.1'

@description('Marketplace image revision; latest selects the current FreeBSD 15.1 revision. Pin the resolved revision for repeat tests.')
param FreeBSDImageVersion string = 'latest'

@description('Public IPv4 CIDR allowed to access SSH/HTTPS, for example 203.0.113.10/32.')
@minLength(9)
param managementSourceCIDR string

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

// Variables
var TempUsername = 'azureuser'
var TempPassword = guid(subscription().id,resourceGroup().id)
var untrustedSubnetName = 'Untrusted-Subnet'
var trustedSubnetName = 'Trusted-Subnet'
var publicIPAddressName = '${virtualMachineName}-PublicIP'
var networkSecurityGroupName = '${virtualMachineName}-NSG'
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
    securityRules: [
      {
        name: 'Management'
        properties: {
          priority: 4096
          sourceAddressPrefix: managementSourceCIDR
          protocol: 'Tcp'
          destinationPortRanges: ['22', '443']
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
      name: PublicIPAddressSku
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

// Create OPNsense TwoNics
module opnSenseTwoNics 'modules/VM/opnsense.bicep' = if(scenarioOption == 'TwoNics'){
  name: '${virtualMachineName}-TwoNics'
  params: {
    Location: Location
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
    ShellScriptName: ShellScriptName
    TempPassword: TempPassword
    TempUsername: TempUsername
    FreeBSDImageVersion: FreeBSDImageVersion
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
          sourceAddressPrefix: managementSourceCIDR
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
      name: PublicIPAddressSku
      tier: 'Regional'
    }
  }
  dependsOn: [
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
      nextHopIpAddress: opnSenseTwoNics.outputs.trustedNicIP
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
    opnSenseTwoNics
  ]
}

output publicIPAddress string = publicip.outputs.publicipAddress
output managementURL string = 'https://${publicip.outputs.publicipAddress}'
output trustedIPAddress string = opnSenseTwoNics.outputs.trustedNicIP
