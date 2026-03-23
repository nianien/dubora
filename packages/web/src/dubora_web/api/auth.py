"""
Authentication endpoints.

Common:
  GET  /auth/me              — check login status
  POST /auth/logout           — clear session cookie

Google OAuth:
  GET  /auth/google/login     — redirect to Google OAuth
  GET  /auth/google/callback  — Google OAuth callback

Dev mode (no GOOGLE_CLIENT_ID):
  GET  /auth/google/login     — set dev cookie, redirect to /
"""

import fnmatch
import json
import os
import urllib.parse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "dubora_session"
COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 days

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _get_config():
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    allowed_raw = os.environ.get("AUTH_ALLOWED_EMAILS", "")
    allowed_patterns = [e.strip().lower() for e in allowed_raw.split(",") if e.strip()]
    return client_id, client_secret, allowed_patterns


def auth_enabled() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID"))


def _serializer() -> URLSafeTimedSerializer:
    secret_key = os.environ.get("AUTH_SECRET_KEY", "dubora-dev-key")
    return URLSafeTimedSerializer(secret_key)


def _get_session(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _callback_url(request: Request) -> str:
    """Get Google callback URL, respecting X-Forwarded-Proto from reverse proxy."""
    url = str(request.url_for("google_callback"))
    if request.headers.get("x-forwarded-proto") == "https" and url.startswith("http://"):
        url = "https://" + url[7:]
    return url


def _set_session(response: RedirectResponse, session_data: dict):
    token = _serializer().dumps(session_data)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
    )


# ── Common ──

@router.get("/me")
async def me(request: Request):
    session = _get_session(request)
    if not session:
        return {"authenticated": False, "user": None}
    user_id = session.get("user_id")
    if user_id:
        store = request.app.state.store
        user = store.get_user(user_id)
        if user:
            return {"authenticated": True, "user": {
                "id": user["id"], "email": user["email"],
                "name": user["name"], "picture": user["picture"],
            }}
    return {"authenticated": True, "user": session}


@router.post("/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response


# ── Google OAuth ──

@router.get("/google/login")
async def google_login(request: Request):
    if not auth_enabled():
        store = request.app.state.store
        user_id = store.upsert_user(email="dev@localhost", name="Dev Mode")
        store.upsert_user_auth(
            user_id=user_id, provider="dev", provider_id="dev@localhost",
            email="dev@localhost",
        )
        session_data = {"user_id": user_id, "email": "dev@localhost", "name": "Dev Mode", "provider": "dev"}
        response = RedirectResponse("/")
        _set_session(response, session_data)
        return response
    client_id, _, _ = _get_config()
    callback_url = _callback_url(request)
    params = {
        "client_id": client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}")


@router.get("/google/callback")
async def google_callback(request: Request, code: str = ""):
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)

    client_id, client_secret, allowed_patterns = _get_config()
    callback_url = _callback_url(request)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": callback_url,
        })
        if token_resp.status_code != 200:
            return JSONResponse({"error": "token exchange failed"}, status_code=401)
        tokens = token_resp.json()

        userinfo_resp = await client.get(GOOGLE_USERINFO_URL, headers={
            "Authorization": f"Bearer {tokens['access_token']}",
        })
        if userinfo_resp.status_code != 200:
            return JSONResponse({"error": "failed to get user info"}, status_code=401)
        userinfo = userinfo_resp.json()

    email = userinfo.get("email", "").lower()
    name = userinfo.get("name", "")
    picture = userinfo.get("picture", "")

    if allowed_patterns and not any(fnmatch.fnmatch(email, p) for p in allowed_patterns):
        return JSONResponse(
            {"error": f"email {email} is not in the allowed list"},
            status_code=403,
        )

    store = request.app.state.store
    user_id = store.upsert_user(email=email, name=name, picture=picture)
    store.upsert_user_auth(
        user_id=user_id, provider="google", provider_id=email,
        email=email, raw=json.dumps(userinfo, ensure_ascii=False),
    )

    session_data = {"user_id": user_id, "email": email, "name": name, "picture": picture, "provider": "google"}
    response = RedirectResponse("/")
    _set_session(response, session_data)
    return response
