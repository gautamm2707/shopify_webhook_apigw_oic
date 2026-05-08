import io
import json
import logging
import base64
from typing import Any, Dict, Optional

import oci
import requests
from fdk import response


LOGGER = logging.getLogger()
CONFIG = {}


# Prefer setting these as OCI Function configuration values instead of hard-coding.
# Example function config keys:
# TOKEN_URL=https://<identity-domain>.identity.oraclecloud.com/oauth2/v1/token
# OIC_SCOPE=https://<oic-instance>.integration.<region>.ocp.oraclecloud.com:443urn:opc:resource:consumer::all
# OIC_ENDPOINT=https://<oic-instance>.integration.<region>.ocp.oraclecloud.com/ic/api/integration/v1/flows/rest/<endpoint>
# CLIENT_ID=<identity-domain-oauth-client-id>
# CLIENT_SECRET_OCID=<oci-vault-secret-ocid-containing-client-secret>
#
DEFAULT_TOKEN_URL = "https://idcs-ff3532e3a9ba4###########.identity.oraclecloud.com/oauth2/v1/token"
DEFAULT_OIC_SCOPE = "https://01DB8CF84FDB4C##############.integration.us-ashburn-1.ocp.oraclecloud.com:443urn:opc:resource:consumer::all"
DEFAULT_OIC_ENDPOINT = "https://01DB8CF84FDB4C##############.integration.us-ashburn-1.ocp.oraclecloud.com/ic/api/integration/v1/flows/rest/<replace-with-oic-endpoint>"
DEFAULT_TOKEN_GRANT_TYPE = "client_credentials"


def initContext(config: Dict[str, str]) -> None:
    global CONFIG
    CONFIG = config or {}


def getSecret(ocid: str) -> str:
    signer = oci.auth.signers.get_resource_principals_signer()
    try:
        client = oci.secrets.SecretsClient({}, signer=signer)
        secret_bundle = client.get_secret_bundle(ocid).data
        secret_content = secret_bundle.secret_bundle_content.content.encode("utf-8")
        return base64.b64decode(secret_content).decode("utf-8")
    except Exception as ex:
        LOGGER.error("getSecret: failed to retrieve secret: %s", ex)
        raise


def _json_response(ctx: Any, status_code: int, payload: Dict[str, Any]) -> response.Response:
    return response.Response(
        ctx,
        response_data=json.dumps(payload),
        status_code=status_code,
        headers={"Content-Type": "application/json"},
    )


def _get_header(headers: Dict[str, str], name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _get_config(key: str, default: str, *aliases: str) -> str:
    for config_key in (key, *aliases):
        value = CONFIG.get(config_key)
        if value:
            return value
    return default


def _get_required_config(key: str, *aliases: str) -> str:
    value = _get_config(key, "", *aliases)
    if not value:
        accepted_keys = ", ".join((key, *aliases))
        raise ValueError(f"Missing required function config. Expected one of: {accepted_keys}")
    return value


def _build_basic_auth_header(client_id: str, client_secret: str) -> str:
    encoded = f"{client_id}:{client_secret}"
    baseencoded = base64.urlsafe_b64encode(encoded.encode("UTF-8")).decode("ascii")
    return f"Basic {baseencoded}"


def _get_jwt_assertion() -> Optional[str]:
    jwt_assertion = CONFIG.get("JWT_ASSERTION")
    if jwt_assertion:
        return jwt_assertion

    jwt_assertion_secret_ocid = CONFIG.get("JWT_ASSERTION_SECRET_OCID")
    if jwt_assertion_secret_ocid:
        return getSecret(jwt_assertion_secret_ocid)

    return None


def _get_identity_domain_token(
    token_url: str,
    scope: str,
    basic_auth_header: str,
    grant_type: str,
) -> str:
    token_headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Authorization": basic_auth_header,
        "Accept": "*/*",
    }
    token_payload = {
        "grant_type": grant_type,
        "scope": scope,
    }

    if grant_type == "urn:ietf:params:oauth:grant-type:jwt-bearer":
        jwt_assertion = _get_jwt_assertion()
        if not jwt_assertion:
            raise ValueError(
                "JWT bearer grant requires JWT_ASSERTION or JWT_ASSERTION_SECRET_OCID function config"
            )
        token_payload["assertion"] = jwt_assertion

    token_response = requests.post(
        token_url,
        headers=token_headers,
        data=token_payload,
        timeout=10,
    )

    if token_response.status_code != 200:
        raise RuntimeError(
            f"Token endpoint failed with status {token_response.status_code}: {token_response.text}"
        )

    token_body = token_response.json()
    access_token = token_body.get("access_token")
    if not access_token:
        raise RuntimeError("Token endpoint response did not include access_token")

    return access_token


def handler(ctx, data: io.BytesIO = None):
    try:
        initContext(dict(ctx.Config() or {}))
        LOGGER.info("handler: started function execution")

        request_headers = ctx.Headers() or {}
        token_url = _get_config("TOKEN_URL", DEFAULT_TOKEN_URL, "idcs_token_endpoint")
        oic_scope = _get_config("OIC_SCOPE", DEFAULT_OIC_SCOPE, "idcs_oauth_scope")
        oic_endpoint = _get_config("OIC_ENDPOINT", DEFAULT_OIC_ENDPOINT, "OIC_Endpoint", "oic_endpoint")
        grant_type = _get_config("TOKEN_GRANT_TYPE", DEFAULT_TOKEN_GRANT_TYPE, "idcs_token_grant_type")
        client_id = _get_required_config("CLIENT_ID", "idcs_app_client_id")
        client_secret_ocid = _get_required_config("CLIENT_SECRET_OCID", "idcs_client_secret_ocid")
        client_secret = getSecret(client_secret_ocid)
        basic_auth_header = _build_basic_auth_header(client_id, client_secret)

        access_token = _get_identity_domain_token(
            token_url,
            oic_scope,
            basic_auth_header,
            grant_type,
        )

        oic_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        # Forward Shopify webhook metadata to OIC when present.
        for header_name in (
            "X-Shopify-Topic",
            "X-Shopify-Shop-Domain",
            "X-Shopify-Webhook-Id",
            "X-Shopify-Hmac-Sha256",
            "X-Shopify-Triggered-At",
            "X-Shopify-Event-Id",
        ):
            header_value = _get_header(request_headers, header_name)
            if header_value:
                oic_headers[header_name] = header_value

        oic_response = requests.get(
            oic_endpoint,
            headers=oic_headers,
            timeout=30,
        )

        return response.Response(
            ctx,
            response_data=oic_response.text,
            status_code=oic_response.status_code,
            headers={
                "Content-Type": oic_response.headers.get("Content-Type", "application/json")
            },
        )

    except requests.Timeout:
        LOGGER.exception("Timeout while calling downstream service")
        return _json_response(ctx, 504, {"error": "Timeout while calling downstream service"})
    except Exception as ex:
        LOGGER.exception("Exception occurred")
        return _json_response(ctx, 500, {"error": str(ex)})
