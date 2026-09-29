param untrustedSubnetId string
param trustedSubnetId string
param publicIPId string
param virtualMachineName string
param TempUsername string
@secure()
param TempPassword string
param virtualMachineSize string
param FreeBSDImageVersion string = 'latest'
param ShellScriptName string
param nsgId string
param ShellScriptObj object
param Location string = resourceGroup().location

resource trustedSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-05-01' existing = {
  name: ShellScriptObj.TrustedSubnetName
}
resource windowsvmsubnet 'Microsoft.Network/virtualNetworks/subnets@2023-05-01' existing = if (!empty(ShellScriptObj.WindowsSubnetName)) {
  name: ShellScriptObj.WindowsSubnetName
}

module untrustedNic '../vnet/nic.bicep' = {
  name: '${virtualMachineName}-Untrusted-NIC'
  params: {
    Location: Location
    nicName: '${virtualMachineName}-Untrusted-NIC'
    subnetId: untrustedSubnetId
    publicIPId: publicIPId
    enableIPForwarding: true
    nsgId: nsgId
  }
}
module trustedNic '../vnet/nic.bicep' = {
  name: '${virtualMachineName}-Trusted-NIC'
  params: {
    Location: Location
    nicName: '${virtualMachineName}-Trusted-NIC'
    subnetId: trustedSubnetId
    enableIPForwarding: true
    nsgId: nsgId
  }
}

// Verified against the public Azure Marketplace catalog on 2026-09-29.
var imagePublisher = 'freebsd'
var imageOffer = 'freebsd-15_1'
var imageSku = '15_1-release-amd64-gen2-zfs'
resource OPNsense 'Microsoft.Compute/virtualMachines@2023-07-01' = {
  name: virtualMachineName
  location: Location
  properties: {
    osProfile: {
      computerName: virtualMachineName
      adminUsername: TempUsername
      adminPassword: TempPassword
    }
    hardwareProfile: { vmSize: virtualMachineSize }
    storageProfile: {
      osDisk: { createOption: 'FromImage' }
      imageReference: {
        publisher: imagePublisher
        offer: imageOffer
        sku: imageSku
        version: FreeBSDImageVersion
      }
    }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
    networkProfile: {
      networkInterfaces: [
        { id: untrustedNic.outputs.nicId, properties: { primary: true } }
        { id: trustedNic.outputs.nicId, properties: { primary: false } }
      ]
    }
  }
  plan: { name: imageSku, publisher: imagePublisher, product: imageOffer }
}

var trustedPrefix = contains(trustedSubnet.properties, 'addressPrefixes') ? trustedSubnet.properties.addressPrefixes[0] : trustedSubnet.properties.addressPrefix
var windowsPrefix = !empty(ShellScriptObj.WindowsSubnetName) ? (contains(windowsvmsubnet!.properties, 'addressPrefixes') ? windowsvmsubnet!.properties.addressPrefixes[0] : windowsvmsubnet!.properties.addressPrefix) : ''
// JSON preserves empty optional arguments and avoids interpolation into a shell command.
var bootstrapSettings = {
  scriptURI: ShellScriptObj.OpnScriptURI
  opnVersion: ShellScriptObj.OpnVersion
  agentMinimumVersion: ShellScriptObj.WALinuxVersion
  role: 'TwoNics'
  trustedSubnet: trustedPrefix
  windowsSubnet: windowsPrefix
}
resource vmext 'Microsoft.Compute/virtualMachines/extensions@2023-07-01' = {
  parent: OPNsense
  name: 'CustomScript'
  location: Location
  properties: {
    publisher: 'Microsoft.OSTCExtensions'
    type: 'CustomScriptForLinux'
    typeHandlerVersion: '1.5'
    autoUpgradeMinorVersion: false
    settings: {
      fileUris: [ '${ShellScriptObj.OpnScriptURI}${ShellScriptName}' ]
      commandToExecute: 'sh configureopnsense.sh ${base64(string(bootstrapSettings))}'
    }
  }
}
output untrustedNicIP string = untrustedNic.outputs.nicIP
output trustedNicIP string = trustedNic.outputs.nicIP
