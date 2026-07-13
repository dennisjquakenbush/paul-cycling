"""
Garmin Connect authentication for Paul's cycling dashboard.

Paul is logged in via a Garmin "DI" mobile OAuth2 token (the newer diauth.garmin.com
flow). The access token lasts ~29h; the refresh token rotates on every refresh and
lasts much longer. This module keeps a valid bearer token available so the daily
noon job can sustain itself indefinitely without anyone re-entering a password.

Token store: ~/.garminconnect/garmin_tokens.json
  {
    "di_token":         "<JWT access token>",
    "di_refresh_token": "<base64 JSON refresh token>",
    "di_client_id":     "GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2"
  }
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_PATH = os.path.expanduser("~/.garminconnect/garmin_tokens.json")
REFRESH_URL = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
UA = "com.garmin.android.apps.connectmobile"

# Refresh when the access token has less than this many seconds of life left.
REFRESH_MARGIN_S = 30 * 60


def _load():
    with open(TOKEN_PATH) as f:
        return json.load(f)


def _save(store):
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(store, f, indent=2)
    os.replace(tmp, TOKEN_PATH)


def _jwt_exp(token):
    """Return the exp (unix seconds) claim of a JWT access token, or 0."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
    except Exception:
        return 0


def _refresh(store):
    """Exchange the (rotating) refresh token for a fresh access token."""
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        # Garmin expects the raw base64 refresh-token string, not the decoded value.
        "refresh_token": store["di_refresh_token"],
        "client_id": store["di_client_id"],
    }).encode()
    req = urllib.request.Request(
        REFRESH_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        tok = json.loads(resp.read())

    store["di_token"] = tok["access_token"]
    # The refresh token rotates; if we don't persist the new one, the next
    # refresh fails. Garmin returns it already base64-encoded.
    if tok.get("refresh_token"):
        store["di_refresh_token"] = tok["refresh_token"]
    _save(store)
    return store


def get_token(force_refresh=False):
    """
    Return a currently-valid Garmin bearer access token, refreshing and
    persisting a new one if the stored token is expired or nearly so.
    """
    store = _load()
    exp = _jwt_exp(store["di_token"])
    if force_refresh or exp - time.time() < REFRESH_MARGIN_S:
        try:
            store = _refresh(store)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf8", "ignore")
            raise RuntimeError(
                f"Garmin token refresh failed (HTTP {e.code}): {body}\n"
                "The refresh token may have expired. Paul needs to re-authorize "
                "Garmin in the app that created ~/.garminconnect/garmin_tokens.json."
            ) from e
    return store["di_token"]


if __name__ == "__main__":
    tok = get_token()
    exp = _jwt_exp(tok)
    print(f"Valid Garmin token; expires in {round((exp - time.time()) / 3600, 1)}h")
