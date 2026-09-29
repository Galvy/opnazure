param location string
param clusterName string

var accountName = 'opnha${uniqueString(resourceGroup().id, clusterName)}'
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: accountName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}
resource service 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}
resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: service
  name: 'witness'
  properties: { publicAccess: 'None' }
}
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for node in ['Primary', 'Secondary']: {
  name: '${clusterName}-${node}-HA'
  location: location
}]
resource storageRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (node, i) in ['Primary', 'Secondary']: {
  name: guid(container.id, identities[i].id, 'blob-data-contributor')
  scope: container
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: identities[i].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]
// Only power/read operations. Assign separately at each peer VM scope, not the RG.
resource fenceRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, clusterName, 'opnazure-fence')
  properties: {
    roleName: 'OPNazure fencing ${uniqueString(resourceGroup().id, clusterName)}'
    description: 'Read power state, hard-stop and restart only the assigned peer VM.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: [
        'Microsoft.Compute/virtualMachines/read'
        'Microsoft.Compute/virtualMachines/instanceView/read'
        'Microsoft.Compute/virtualMachines/powerOff/action'
        'Microsoft.Compute/virtualMachines/start/action'
      ]
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}
output blobUrl string = '${storage.properties.primaryEndpoints.blob}witness/leader'
output primaryIdentity object = {
  id: identities[0].id
  clientId: identities[0].properties.clientId
  principalId: identities[0].properties.principalId
}
output secondaryIdentity object = {
  id: identities[1].id
  clientId: identities[1].properties.clientId
  principalId: identities[1].properties.principalId
}
output fenceRoleId string = fenceRole.id
