#!/usr/bin/env python3
"""
mqtt_monitor.py — Subscribe to the Arctic Spa cloud MQTT broker and log live telemetry.

Authenticates with your My Arctic Spa email + password, obtains a JWT, then
subscribes to spa telemetry topics.  The spa keeps its normal cloud connection
untouched — this is purely passive.

Two broker paths (auto-detected from JWT audience):
  mqtt-mobile  → tcp://broker.myarcticspa.com:1884      (plain TCP)
  aws-iot      → wss://a2t84nz...amazonaws.com:443/mqtt (WebSocket TLS)

Usage:
    python scripts/mqtt_monitor.py <username> <password>
    python scripts/mqtt_monitor.py         # reads ARCTIC_SPA_USERNAME / ARCTIC_SPA_PASSWORD from .env

    # Override spa ID if auto-detect fails:
    python scripts/mqtt_monitor.py jkol hunter2 --spa-id abc123

Requirements:
    pip install requests "paho-mqtt>=1.6" python-dotenv
    # For AWS IoT path also: pip install "paho-mqtt[websockets]"
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

AUTH_URL   = "https://myarcticspa.com/api/auth"
TOKEN_URL  = "https://api.myarcticspa.com/access_token"
CLIENT_SECRET = "@4EUu^Y:U+FGtt2P"

BROKER_TCP  = ("broker.myarcticspa.com", 1884)
BROKER_WSS  = ("a2t84nz00o45m2-ats.iot.ca-central-1.amazonaws.com", 443)
AUTHORIZER  = "MyArcticSpaCustomAuthorizer_Prod"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def fetch_salt_and_authenticate(username: str, password: str) -> tuple[str, list]:
    """
    Two-round challenge against /api/auth:
      Round 1: send empty hash → server returns Salt
      Round 2: send SHA-1(salt+password) hash → server returns real UserId + Spas
    Returns (user_id, spas).
    """
    # Round 1 — get salt
    resp = requests.post(
        AUTH_URL,
        json={"username": username, "hash": None, "AllowNoSpaLogin": True},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    salt = data.get("Salt")
    if not salt:
        raise ValueError(f"No Salt in /api/auth round-1 response: {data}")
    print(f"[auth] Round-1 salt: {salt}")

    # Round 2 — authenticate with hashed password
    pw_hash = hash_password(password, salt)
    print(f"[auth] Round-2 hash: {pw_hash}")
    resp = requests.post(
        AUTH_URL,
        json={"username": username, "hash": pw_hash, "AllowNoSpaLogin": True},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"[auth] Round-2 response: {data}")
    return data.get("UserId", ""), data.get("Spas") or [], pw_hash


def hash_password(password: str, salt_b64: str) -> str:
    """SHA-1( base64_decode(salt) + password.utf16le ), base64-encoded."""
    salt = base64.b64decode(salt_b64)
    digest = hashlib.sha1(salt + password.encode("utf-16-le")).digest()
    return base64.b64encode(digest).decode("ascii")


def fetch_token(username: str, pw_hash: str, client_id: str, spa: dict, user_id: str) -> str:
    """POST /access_token → JWT access_token string.

    The username field is compound: "username|spa_id_lowercase"
    (or "username|user_id_lowercase" if no spa).
    The full spa object is included in the body.
    """
    spa_id = spa.get("Id", "")
    if spa_id:
        compound_username = f"{username}|{spa_id.lower()}"
    elif user_id:
        compound_username = f"{username}|{user_id.lower()}"
    else:
        raise ValueError("Cannot build token request: no spa ID or user ID available")

    payload = {
        "grant_type": "password",
        "client_id": client_id,
        "client_secret": CLIENT_SECRET,
        "username": compound_username,
        "password": pw_hash,
        "spa": spa,
    }
    print(f"[auth] Token request username: {compound_username}")
    resp = requests.post(
        TOKEN_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if not resp.ok:
        print(f"[auth] token {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()["access_token"]


def decode_jwt_audience(token: str) -> list[str]:
    """Decode JWT payload (no verification) and return audience list."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        aud = payload.get("aud", [])
        return [aud] if isinstance(aud, str) else aud
    except Exception:
        return []


def extract_spa_id(spas: list) -> str | None:
    """Try common field names for spa ID from the spas list."""
    if not spas:
        return None
    for key in ("SpaId", "spaId", "spa_id", "id", "Id"):
        if key in spas[0]:
            return spas[0][key]
    return None


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------

def on_connect(client, userdata, flags, rc):
    RC_CODES = {
        0: "Connected OK",
        1: "Bad protocol version",
        2: "Client ID rejected",
        3: "Broker unavailable",
        4: "Bad credentials",
        5: "Not authorised",
    }
    msg = RC_CODES.get(rc, f"Unknown rc={rc}")
    print(f"[mqtt] {msg}")
    if rc == 0:
        userdata["reconnect_attempt"][0] = 0  # reset backoff on success
        for topic in userdata["topics"]:
            client.subscribe(topic)
            print(f"[mqtt] Subscribed → {topic}")


def on_message(client, userdata, msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    topic_label = "/".join(msg.topic.rsplit("/", 2)[-2:])
    try:
        data = json.loads(msg.payload)
        print(f"\n[{ts}] {topic_label}")
        print(json.dumps(data, indent=2))
    except Exception:
        print(f"[{ts}] {topic_label}: {msg.payload!r}")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"[mqtt] Unexpected disconnect rc={rc}")
        userdata["reconnect_event"].set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Arctic Spa MQTT telemetry monitor")
    parser.add_argument("username", nargs="?", default=os.getenv("ARCTIC_SPA_USERNAME"))
    parser.add_argument("password", nargs="?", default=os.getenv("ARCTIC_SPA_PASSWORD"))
    parser.add_argument("--spa-id", default=os.getenv("ARCTIC_SPA_ID"),
                        help="Spa ID (auto-detected from login response if omitted)")
    args = parser.parse_args()

    if not args.username or not args.password:
        parser.error("username and password are required (or set ARCTIC_SPA_USERNAME / ARCTIC_SPA_PASSWORD in .env)")

    install_id = str(uuid.uuid4())

    # Step 1 — get salt
    print(f"[auth] Authenticating {args.username} ...")
    user_id, spas, pw_hash = fetch_salt_and_authenticate(args.username, args.password)
    spa_ids = [extract_spa_id([s]) for s in spas if extract_spa_id([s])]
    print(f"[auth] UserId={user_id}  Spas={spa_ids}")

    first_spa = spas[0] if spas else {}
    client_id = f"mqtt-mobile_{install_id}"
    print(f"[auth] Fetching token (client_id={client_id}) ...")
    token = fetch_token(args.username, pw_hash, client_id, first_spa, user_id)
    audience = decode_jwt_audience(token)
    print(f"[auth] JWT audience: {audience}")

    # Determine spa_id for topic construction
    spa_id = args.spa_id or (spa_ids[0] if spa_ids else None)
    if not spa_id:
        print("[warn] Could not determine spa ID — subscribing to wildcard '#'")
        spa_id = "+"

    topics = [
        f"arctic/spa/{spa_id}/telemetry/spa",
        f"arctic/spa/{spa_id}/telemetry/filters",
        f"arctic/spa/{spa_id}/telemetry/errors",
        f"arctic/spa/{spa_id}/telemetry/spaboy",
        f"arctic/spa/{spa_id}/telemetry/heartbeat",
        f"arctic/spa/{spa_id}/telemetry/update",
        f"arctic/spa/{spa_id}/telemetry/rfid",
        f"arctic/spa/{spa_id}/settings/spa",
        f"arctic/spa/{spa_id}/settings/spaboy",
        f"arctic/spa/{spa_id}/config/spa",
        f"arctic/spa/{spa_id}/information/spa",
        f"arctic/spa/{spa_id}/information/network",
        f"arctic/spa/{spa_id}/settings/peak",
    ]

    # Choose broker path based on JWT audience
    use_aws = any("aws-iot" in a for a in audience)

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        sys.exit("paho-mqtt not installed — run: pip install paho-mqtt")

    reconnect_event = threading.Event()
    reconnect_attempt = [0]  # mutable so on_connect callback can reset it
    _reconnect_delays = [5, 30, 300, 600]

    mqtt_client = mqtt.Client(
        client_id=f"ha-spa-monitor_{install_id[:8]}",
        transport="websockets" if use_aws else "tcp",
        userdata={
            "topics": topics,
            "reconnect_event": reconnect_event,
            "reconnect_attempt": reconnect_attempt,
        },
    )
    mqtt_client.on_connect    = on_connect
    mqtt_client.on_message    = on_message
    mqtt_client.on_disconnect = on_disconnect

    if use_aws:
        broker_host, broker_port = BROKER_WSS
        mqtt_username = f"{token}?x-amz-customauthorizer-name={AUTHORIZER}"
        mqtt_client.tls_set()
        mqtt_client.ws_set_options(path="/mqtt")
        print(f"[mqtt] Using AWS IoT WebSocket broker ({broker_host}:{broker_port})")
    else:
        broker_host, broker_port = BROKER_TCP
        mqtt_username = token
        print(f"Client ID: {mqtt_client._client_id.decode()}")
        print(f"Username for MQTT auth: {mqtt_username}")
        print(f"[mqtt] Using broker.myarcticspa.com:{broker_port} (plain TCP)")

    mqtt_client.username_pw_set(mqtt_username, "anything")

    print(f"[mqtt] Connecting to {broker_host}:{broker_port} ...")
    try:
        mqtt_client.connect(broker_host, broker_port, keepalive=60)
    except Exception as e:
        sys.exit(f"[error] {e}")

    mqtt_client.loop_start()
    try:
        while True:
            if not reconnect_event.wait(timeout=1.0):
                continue
            reconnect_event.clear()

            attempt = reconnect_attempt[0]
            delay = _reconnect_delays[min(attempt, len(_reconnect_delays) - 1)]
            reconnect_attempt[0] += 1
            print(f"[mqtt] Reconnecting in {delay}s (attempt {attempt + 1}, fetching fresh token)...")
            time.sleep(delay)

            try:
                _, _, pw_hash = fetch_salt_and_authenticate(args.username, args.password)
                token = fetch_token(args.username, pw_hash, client_id, first_spa, user_id)
                if use_aws:
                    mqtt_username = f"{token}?x-amz-customauthorizer-name={AUTHORIZER}"
                else:
                    mqtt_username = token
                mqtt_client.username_pw_set(mqtt_username, "anything")
                mqtt_client.reconnect()
            except Exception as e:
                print(f"[mqtt] Reconnect error: {e}")
                reconnect_event.set()  # retry after next backoff

    except KeyboardInterrupt:
        print("\n[mqtt] Disconnecting.")
        mqtt_client.disconnect()
    finally:
        mqtt_client.loop_stop()


if __name__ == "__main__":
    main()
