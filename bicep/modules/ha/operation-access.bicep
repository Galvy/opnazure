targetScope = 'subscription'
param principalIds array
param clusterId string
// Compute async URLs are subscription/location resources. Only operation-status
// reads are granted here; VM power operations remain scoped to the peer VM.
resource role 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(subscription().id, clusterId, 'ha-operation-status')
  properties: {
    roleName: 'OPNazure operation status ${uniqueString(clusterId)}'
    type: 'CustomRole'
    assignableScopes: [subscription().id]
    permissions: [{
      actions: ['Microsoft.Compute/locations/operations/read']
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in principalIds: {
  name: guid(subscription().id, principal, role.id)
  properties: {
    roleDefinitionId: role.id
    principalId: principal
    principalType: 'ServicePrincipal'
  }
}]
