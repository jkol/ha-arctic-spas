"""
Fetches a fresh JWT and prints exact MQTT Explorer connection settings
for the Arctic Spas AWS IoT broker.

Usage:
    python scripts/get_mqtt_credentials.py <username>
    (will prompt for password securely)
"""
import asyncio
import base64
import getpass
import hashlib
import json
import sys
import uuid

import aiohttp

_AUTH_URL  = "https://myarcticspa.com/api/auth"
_TOKEN_URL = "https://api.myarcticspa.com/access_token"
_TIMEOUT   = aiohttp.ClientTimeout(total=15, connect=10)
_AUTHORIZER = "MyArcticSpaCustomAuthorizer_Prod"
_BROKER_WSS = "a2t84nz00o45m2-ats.iot.ca-central-1.amazonaws.com"


def _hash_password(password: str, salt_b64: str) -> str:
    salt = base64.b64decode(salt_b64)
    digest = hashlib.sha1(salt + password.encode("utf-16-le")).digest()  # noqa: S324
    return base64.b64encode(digest).decode("ascii")


def _decode_jwt_field(token: str, field: str):
    try:
        parts = token.split(".")
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get(field)
    except Exception:
        return None


async def fetch_credentials(username: str, password: str):
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(timeout=_TIMEOUT, connector=connector) as session:
        # Round 1 — get salt
        r1 = await session.post(_AUTH_URL, json={
            "username": username, "hash": None, "AllowNoSpaLogin": True
        })
        r1.raise_for_status()
        salt = (await r1.json()).get("Salt")
        if not salt:
            raise RuntimeError("No Salt in auth response")

        pw_hash = _hash_password(password, salt)

        # Round 2 — authenticate
        r2 = await session.post(_AUTH_URL, json={
            "username": username, "hash": pw_hash, "AllowNoSpaLogin": True
        })
        r2.raise_for_status()
        data2 = await r2.json()
        spas = data2.get("Spas") or []
        if not spas:
            raise RuntimeError("No spas in auth response")
        spa = spas[0]

        # Find spa ID
        spa_id = None
        for key in ("SpaId", "spaId", "spa_id", "id", "Id"):
            if key in spa:
                spa_id = spa[key]
                break
        if not spa_id:
            raise RuntimeError(f"Cannot find spa ID in: {list(spa.keys())}")

        # Fetch JWT
        install_id = str(uuid.uuid4())
        client_id = f"mqtt-mobile_{install_id}"
        user_id = data2.get("UserId", "")
        compound_username = f"{username}|{spa_id.lower()}"

        r3 = await session.post(_TOKEN_URL, json={
            "grant_type": "password",
            "client_id": client_id,
            "client_secret": "@4EUu^Y:U+FGtt2P",
            "username": compound_username,
            "password": pw_hash,
            "spa": spa,
        })
        r3.raise_for_status()
        token_data = await r3.json()
        jwt = token_data.get("access_token")
        if not jwt:
            raise RuntimeError("No access_token in response")

        return jwt, spa_id, client_id


async def main():
    if len(sys.argv) < 2:
        print("Usage: python get_mqtt_credentials.py <username>")
        sys.exit(1)

    username = sys.argv[1]
    password = getpass.getpass(f"Password for {username}: ")

    print("\nAuthenticating...")
    try:
        jwt, spa_id, client_id = await fetch_credentials(username, password)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    exp = _decode_jwt_field(jwt, "exp")
    aud = _decode_jwt_field(jwt, "aud")

    use_aws = isinstance(aud, list) and any("aws-iot" in a for a in aud)
    if not use_aws and isinstance(aud, str):
        use_aws = "aws-iot" in aud

    print(f"\nSpa ID  : {spa_id}")
    print(f"JWT aud : {aud}")
    print(f"JWT exp : {exp}")
    print(f"Mode    : {'AWS IoT (WSS)' if use_aws else 'Standard MQTT broker'}\n")

    if use_aws:
        mqtt_username = f"{jwt}?x-amz-customauthorizer-name={_AUTHORIZER}"
        print("=" * 70)
        print("MQTT EXPLORER SETTINGS (AWS IoT / WSS)")
        print("=" * 70)
        print(f"  Protocol  : wss")
        print(f"  Host      : {_BROKER_WSS}")
        print(f"  Port      : 443")
        print(f"  Path      : /mqtt")
        print(f"  Client ID : {client_id}")
        print(f"  Username  : {mqtt_username}")
        print(f"  Password  : anything")
        print(f"  TLS/SSL   : ON (no client cert needed)")
        print()
        print("TOPICS TO SUBSCRIBE (paste these in MQTT Explorer):")
        print(f"  arctic/spa/{spa_id}/#")
        print(f"  (or individually:)")
        print(f"  arctic/spa/{spa_id}/telemetry/spa")
        print(f"  arctic/spa/{spa_id}/telemetry/spaboy")
        print(f"  arctic/spa/{spa_id}/telemetry/errors")
        print(f"  arctic/spa/{spa_id}/telemetry/filters")
        print(f"  arctic/spa/{spa_id}/telemetry/heartbeat")
        print(f"  arctic/spa/{spa_id}/settings/spa")
        print(f"  arctic/spa/{spa_id}/config/spa")
        print(f"  arctic/spa/{spa_id}/information/spa")
        print(f"  arctic/spa/{spa_id}/information/network")
    else:
        print("=" * 70)
        print("MQTT EXPLORER SETTINGS (Standard broker)")
        print("=" * 70)
        print(f"  Protocol  : mqtt")
        print(f"  Host      : broker.myarcticspa.com")
        print(f"  Port      : 1884")
        print(f"  Client ID : {client_id}")
        print(f"  Username  : {jwt}")
        print(f"  Password  : anything")
        print(f"  TLS/SSL   : OFF")
        print()
        print("TOPICS TO SUBSCRIBE:")
        print(f"  arctic/spa/{spa_id}/#")

    print()
    print("NOTE: JWT expires. Re-run this script if MQTT Explorer loses connection.")


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
