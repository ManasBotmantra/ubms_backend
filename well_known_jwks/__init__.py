"""Azure Function exposing public key set (JWKS) for external token verification by agents/clients."""

import json
import logging
import os
import azure.functions as func
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("well_known_jwks")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, authorize_token",
    "Content-Type": "application/json;charset=UTF-8",
    "Cache-Control": "public, max-age=3600",
}


# Fields whose presence means the JWK carries private/secret material.
# RSA: d,p,q,dp,dq,qi,oth  EC: d  symmetric(oct): k
_PRIVATE_JWK_FIELDS = {"d", "p", "q", "dp", "dq", "qi", "oth", "k"}

# Symmetric keys are secrets by definition and must never appear in a public JWKS.
_PUBLIC_KEY_TYPES = {"RSA", "EC", "OKP"}


def _load_public_keys() -> dict:
    """Loads JWKS_KEYS_JSON and strips anything that isn't a public key.

    Fails safe: a malformed or absent value yields an empty key set rather than
    an error or a passthrough.
    """
    raw = os.environ.get("JWKS_KEYS_JSON")
    if not raw:
        return {"keys": []}

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.error("JWKS_KEYS_JSON is not valid JSON -- serving an empty key set")
        return {"keys": []}

    candidates = parsed.get("keys") if isinstance(parsed, dict) else parsed
    if not isinstance(candidates, list):
        logger.error("JWKS_KEYS_JSON has no 'keys' array -- serving an empty key set")
        return {"keys": []}

    public_keys = []
    for key in candidates:
        if not isinstance(key, dict):
            continue
        if key.get("kty") not in _PUBLIC_KEY_TYPES:
            logger.warning(
                "Refusing to publish JWK of type %r (not an asymmetric public key)",
                key.get("kty"),
            )
            continue
        leaked = _PRIVATE_JWK_FIELDS.intersection(key.keys())
        if leaked:
            logger.error(
                "Refusing to publish JWK kid=%r: contains private fields %s. "
                "Publish only the public half of the key.",
                key.get("kid"),
                sorted(leaked),
            )
            continue
        public_keys.append(key)

    return {"keys": public_keys}


def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse("", status_code=204, headers=CORS_HEADERS)

    # A JWKS may only ever contain PUBLIC key material.
    #
    # Two things were wrong here before:
    #  1. It advertised a symmetric HS256 ("kty": "oct") key. A symmetric key can
    #     never be published -- the verification key IS the signing key -- and
    #     advertising it alongside RS256 in the discovery document is the classic
    #     algorithm-confusion setup. This API signs with HS256 and verifies with a
    #     pinned algorithm list, so external verification is not possible by design
    #     and the correct answer is an empty key set.
    #  2. JWKS_KEYS_JSON was echoed verbatim. Pasting a full JWK (which for RSA/EC
    #     contains the private fields d, p, q, dp, dq, qi, or k for symmetric) would
    #     publish the private key to an anonymous endpoint. We now filter.
    keys_data = _load_public_keys()

    return func.HttpResponse(
        body=json.dumps(keys_data, indent=2),
        status_code=200,
        headers=CORS_HEADERS,
    )
