param untrustedSubnetId string
param trustedSubnetId string
param publicIPId string = ''
param providedNics object = {}
param availabilitySetId string = ''
param haConfig object = {}
param virtualMachineName string
param TempUsername string
@secure()
param TempPassword string
param virtualMachineSize string
param FreeBSDImageVersion string = 'latest'
param nsgId string
param ShellScriptObj object
param Location string = resourceGroup().location

resource trustedSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-05-01' existing = {
  name: ShellScriptObj.TrustedSubnetName
}
resource windowsvmsubnet 'Microsoft.Network/virtualNetworks/subnets@2023-05-01' existing = if (!empty(ShellScriptObj.WindowsSubnetName)) {
  name: ShellScriptObj.WindowsSubnetName
}

module untrustedNic '../vnet/nic.bicep' = if (empty(providedNics)) {
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
module trustedNic '../vnet/nic.bicep' = if (empty(providedNics)) {
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
    availabilitySet: empty(availabilitySetId) ? null : { id: availabilitySetId }
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
        { id: empty(providedNics) ? untrustedNic!.outputs.nicId : providedNics.wanId, properties: { primary: true } }
        { id: empty(providedNics) ? trustedNic!.outputs.nicId : providedNics.lanId, properties: { primary: false } }
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
  managementPort: ShellScriptObj.?managementPort ?? 443
  role: ShellScriptObj.OpnType
  publicIPAddress: ShellScriptObj.publicIPAddress
  localTrustedIP: ShellScriptObj.?localTrustedIP ?? ''
  peerTrustedIP: ShellScriptObj.?peerTrustedIP ?? ''
  trustedSubnet: trustedPrefix
  windowsSubnet: windowsPrefix
  ha: haConfig
}
// Avoid the 1.5 handler's Python rU/dos2unix preprocessing on Python >= 3.11.
// The launcher extracts embedded sources; no legacy download preprocessing.
// It contains no single quotes, so it is safe inside the quoted sh -c argument.
var embeddedSources = {
  'configureopnsense.sh': loadFileAsBase64('../../../scripts/configureopnsense.sh')
  'config.xml': loadFileAsBase64('../../../scripts/config.xml')
  'get_nic_gw.py': loadFileAsBase64('../../../scripts/get_nic_gw.py')
  'prepare_config.py': loadFileAsBase64('../../../scripts/prepare_config.py')
  'actions_waagent.conf': loadFileAsBase64('../../../scripts/actions_waagent.conf')
  'verify_opnsense.sh': loadFileAsBase64('../../../scripts/verify_opnsense.sh')
}
var bootstrapLauncher = loadTextContent('../../../scripts/embedded-bootstrap.sh')
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
      fileUris: []
      commandToExecute: '/bin/sh -c \'${bootstrapLauncher}\' opnazure ${base64(string(bootstrapSettings))} ${base64(string(embeddedSources))}'
    }
  }
}
output untrustedNicIP string = empty(providedNics) ? untrustedNic!.outputs.nicIP : providedNics.wanIP
output trustedNicIP string = empty(providedNics) ? trustedNic!.outputs.nicIP : providedNics.lanIP
