// MatchMind on Azure: Container Apps + Microsoft Foundry + Cosmos DB + AI Translator.
// Deploy with `azd up` (see README). Foundry and Cosmos use the app's managed identity (no keys).
targetScope = 'resourceGroup'

@minLength(1)
@maxLength(64)
@description('azd environment name, used to name resources')
param environmentName string

@description('Region for all resources')
param location string = resourceGroup().location

@description('Foundry model to deploy')
param modelName string = 'gpt-5-mini'
param modelVersion string = '2025-08-07'
param modelCapacity int = 50
@description('Deployment SKU: check quota with az cognitiveservices usage list')
@allowed([ 'Standard', 'GlobalStandard', 'DataZoneStandard' ])
param modelSku string = 'GlobalStandard'

@description('Optional Fabric Eventstream custom-endpoint connection string (create the Eventstream in Fabric first)')
@secure()
param fabricEventstreamConnectionString string = ''

@description('Set by azd: container image for the web service')
param webImageName string = ''

var token = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var tags = { 'azd-env-name': environmentName, app: 'matchmind' }

// ---------------------------------------------------------------- identity & monitoring

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-matchmind-${token}'
  location: location
  tags: tags
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-matchmind-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.1') }  // hard cap: stays inside the free ingestion allowance
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-matchmind-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

// ---------------------------------------------------------------- Microsoft Foundry (AI Services + project + model)

resource foundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: 'aif-matchmind-${token}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'aif-matchmind-${token}'
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: foundry
  name: 'matchmind'
  location: location
  dependsOn: [ model ]  // the account accepts one change at a time
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'MatchMind'
    description: 'Multi-agent explainable match intelligence'
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundry
  name: modelName
  sku: { name: modelSku, capacity: modelCapacity }
  properties: {
    model: { format: 'OpenAI', name: modelName, version: modelVersion }
  }
}

// Azure AI User on the Foundry resource for the app identity
resource foundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, identity.id, '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  scope: foundry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  }
}

// Cognitive Services OpenAI User, so the same identity can call the model directly
resource openaiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, identity.id, '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  scope: foundry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  }
}

// ---------------------------------------------------------------- Azure AI Translator

resource translator 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'trl-matchmind-${token}'
  location: location
  tags: tags
  kind: 'TextTranslation'
  sku: { name: 'F0' }
  properties: { customSubDomainName: 'trl-matchmind-${token}', publicNetworkAccess: 'Enabled' }
}

// ---------------------------------------------------------------- Azure Cosmos DB (serverless, Entra-only)

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'cosmos-matchmind-${token}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [ { locationName: location, failoverPriority: 0 } ]
    capabilities: [ { name: 'EnableServerless' } ]
    disableLocalAuth: true
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'matchmind'
  properties: { resource: { id: 'matchmind' } }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'match_items'
  properties: {
    resource: {
      id: 'match_items'
      partitionKey: { paths: [ '/match_id' ], kind: 'Hash' }
    }
  }
}

// Cosmos DB Built-in Data Contributor for the app identity
resource cosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, identity.id, 'data-contributor')
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: cosmos.id
  }
}

// ---------------------------------------------------------------- Container Registry + Container Apps

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'crmatchmind${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-matchmind-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var secrets = concat(
  [ { name: 'translator-key', value: translator.listKeys().key1 } ],
  empty(fabricEventstreamConnectionString) ? [] : [ { name: 'fabric-conn', value: fabricEventstreamConnectionString } ]
)

var envVars = concat([
  { name: 'LLM_PROVIDER', value: 'foundry' }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: 'https://${foundry.properties.customSubDomainName}.services.ai.azure.com/api/projects/${foundryProject.name}' }
  { name: 'FOUNDRY_MODEL', value: modelName }
  { name: 'LLM_TIMEOUT_S', value: '20' }
  { name: 'LLM_DAILY_CALL_BUDGET', value: '600' }  // cost guard for the public demo; past it agents use rules
  { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
  { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
  { name: 'COSMOS_DATABASE', value: 'matchmind' }
  { name: 'COSMOS_CONTAINER', value: 'match_items' }
  { name: 'TRANSLATOR_KEY', secretRef: 'translator-key' }
  { name: 'TRANSLATOR_REGION', value: location }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
], empty(fabricEventstreamConnectionString) ? [] : [ { name: 'FABRIC_EVENTSTREAM_CONNECTION_STRING', secretRef: 'fabric-conn' } ])

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-matchmind-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  dependsOn: [ acrPull ]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: [ { server: registry.properties.loginServer, identity: identity.id } ]
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'web'
          image: empty(webImageName) ? 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' : webImageName
          resources: { cpu: json('0.5'), memory: '1Gi' }  // small on purpose: stays well inside free-trial credit
          env: envVars
          probes: [
            { type: 'Liveness', httpGet: { path: '/api/health', port: 8000 }, initialDelaySeconds: 10, periodSeconds: 30 }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.properties.loginServer
output SERVICE_WEB_NAME string = web.name
output SERVICE_WEB_URI string = 'https://${web.properties.configuration.ingress.fqdn}'
output FOUNDRY_PROJECT_ENDPOINT string = 'https://${foundry.properties.customSubDomainName}.services.ai.azure.com/api/projects/${foundryProject.name}'
output FOUNDRY_MODEL string = modelName
output COSMOS_ENDPOINT string = cosmos.properties.documentEndpoint
