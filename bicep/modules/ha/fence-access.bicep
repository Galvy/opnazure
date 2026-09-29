param peerVmName string
param principalId string
param roleDefinitionId string
resource peer 'Microsoft.Compute/virtualMachines@2023-07-01' existing = {
  name: peerVmName
}
resource permission 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(peer.id, principalId, roleDefinitionId)
  scope: peer
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
