# OCI API Gateway + OCI Function Bridge for Secure OIC Webhook Invocation

This repository contains an OCI Function that acts as a secure bridge between an external commerce platform webhook, such as Shopify, and an Oracle Integration Cloud REST endpoint.

The function is designed for API Gateway backend routes. API Gateway exposes a stable HTTPS URL to the commerce platform, while the OCI Function handles secure OAuth token generation and invokes OIC with a bearer token.

## Use Case

Commerce platforms commonly send webhook events when business actions occur, such as:

- Order created
- Payment captured
- Fulfillment updated
- Refund created
- Product or inventory changed
- Customer updated

Oracle Integration Cloud is often used to process these events and orchestrate downstream flows into ERP, warehouse, CRM, finance, or custom applications.

The challenge is that OIC REST APIs are usually protected by OCI Identity Domain OAuth, while webhook platforms typically send simple HTTP requests and do not generate OIC-scoped OAuth tokens.

This function solves that gap by acting as a token broker.

## Architecture

```text
Commerce Platform Webhook
        |
        v
OCI API Gateway
        |
        v
OCI Function
        |
        +--> OCI Vault
        |
        +--> OCI Identity Domain Token Endpoint
        |
        v
Oracle Integration Cloud REST Endpoint
```

## What the Function Does

At runtime, the function:

1. Receives a request from OCI API Gateway.
2. Reads Identity Domain and OIC configuration from OCI Function or Application config.
3. Retrieves the OAuth client secret from OCI Vault using Resource Principals.
4. Builds a Basic OAuth header from `client_id:client_secret`.
5. Calls the OCI Identity Domain token endpoint.
6. Requests an access token scoped for Oracle Integration Cloud.
7. Invokes the configured OIC REST endpoint using `Authorization: Bearer <access_token>`.
8. Returns the OIC response to API Gateway.

The external webhook caller does not need to know the OIC scope, client secret, token endpoint, or bearer token.

## Repository Files

```text
func.py              OCI Function handler
requirements.txt    Python dependencies
```

## Required OCI Configuration

The function expects configuration values from OCI Functions application config or function config.

Recommended application-level config:

```text
idcs_app_client_id=<identity-domain-oauth-client-id>
idcs_client_secret_ocid=<oci-vault-secret-ocid>
idcs_token_endpoint=https://<identity-domain>.identity.oraclecloud.com/oauth2/v1/token
idcs_oauth_scope=https://<oic-host>:443urn:opc:resource:consumer::all
OIC_ENDPOINT=https://<oic-host>/ic/api/integration/v2/flows/rest/<integration-endpoint>
```

Recommended function-level config:

```text
Avoid Hardcoding
```

The code also supports these alternate config names:

```text
CLIENT_ID
CLIENT_SECRET_OCID
TOKEN_URL
OIC_SCOPE
OIC_Endpoint
oic_endpoint
```

## OCI Vault Requirement

Store the Identity Domain OAuth client secret in OCI Vault.

The function config should contain only the Vault secret OCID:

```text
idcs_client_secret_ocid=ocid1.vaultsecret...
```

The actual client secret should not be hardcoded in source code or stored in API Gateway.

## IAM Policy

The OCI Function must be allowed to read the Vault secret.

Example policy:

```text
Allow dynamic-group <function-dynamic-group> to read secret-bundles in compartment <compartment-name>
```

The exact policy depends on your compartment structure and dynamic group rules.

## API Gateway Route

Create an OCI API Gateway route with an Oracle Functions backend.

Example route for webhook traffic:

```text
Path: /commerce/webhook
Method: POST
Backend type: Oracle Functions
Function: specialized
```

For a test OIC endpoint that expects `GET`, configure the route as:

```text
Path: /commerce/test
Method: GET
Backend type: Oracle Functions
Function: specialized
```

The current function implementation invokes OIC using `GET`. For real commerce webhooks, OIC will usually expose a `POST` endpoint and the function should call OIC with `POST` while forwarding the webhook body.

## Deploying with Fn CLI

Login to OCI and configure the Fn CLI for your tenancy, region, compartment, and Functions app.

Initialize or use this function directory:

```bash
fn init --runtime python specialized
```

Replace the generated `func.py` and `requirements.txt` with the files from this repository.

Deploy:

```bash
fn -v deploy --app <function-app-name>
```

## Deploying with Docker and OCI CLI

Build the image:

```bash
docker build --platform linux/amd64 \
  -t <region-key>.ocir.io/<namespace>/<repo>/specialized:0.0.1 .
```

Push the image:

```bash
docker push <region-key>.ocir.io/<namespace>/<repo>/specialized:0.0.1
```

Create the function:

```bash
oci fn function create \
  --application-id <function-app-ocid> \
  --display-name specialized \
  --image <region-key>.ocir.io/<namespace>/<repo>/specialized:0.0.1 \
  --memory-in-mbs 256 \
  --timeout-in-seconds 30
```

Update an existing function:

```bash
oci fn function update \
  --function-id <function-ocid> \
  --image <region-key>.ocir.io/<namespace>/<repo>/specialized:0.0.1
```

## Invoking the Function

Invoke directly with OCI CLI:

```bash
oci fn function invoke \
  --function-id <function-ocid> \
  --body '{"test":true}' \
  --file -
```

Or invoke through API Gateway using the deployment URL:

```bash
curl -X GET https://<api-gateway-deployment-url>/commerce/test
```

For webhook use cases:

```bash
curl -X POST https://<api-gateway-deployment-url>/commerce/webhook \
  -H "Content-Type: application/json" \
  -d '{"event":"order_created"}'
```

## Security Notes

- Do not hardcode client secrets in `func.py`.
- Store the Identity Domain client secret in OCI Vault.
- Use Resource Principals for Vault access.
- Do not log access tokens, client secrets, or authorization headers.
- For commerce webhook traffic, validate the platform signature before invoking OIC.
- For Shopify-style webhooks, validate `X-Shopify-Hmac-Sha256`.
- Use least-privilege Identity Domain clients and IAM policies.

## Production Recommendations

Before using this pattern in production:

- Enable webhook signature validation.
- Use a POST-enabled OIC integration for commerce webhook payloads.
- Add structured logging with correlation IDs and webhook IDs.
- Cache access tokens until expiry for high-volume traffic.
- Return clear 2xx, 4xx, and 5xx responses to the commerce platform.
- Keep separate OIC endpoints for test and production.

## Summary

This project provides a secure webhook-to-OIC bridge using OCI-native services.

It allows an external commerce platform to call a simple API Gateway URL while OCI Functions handles:

- Vault secret retrieval
- Identity Domain token generation
- OIC OAuth scope handling
- Bearer-token invocation of Oracle Integration Cloud

This keeps the webhook integration simple for the commerce platform while preserving secure OAuth-based access to OIC.
