import base64
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import time
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import EmailStr, TypeAdapter
from starlette.responses import JSONResponse, RedirectResponse

from src.auth import (
    CurrentUser, SESSION_COOKIE, app_url, cookie_secure, create_login_session, hash_token,
)
from src.db import (
    consume_oauth_state, get_or_create_oauth_user, get_session_user,
    link_oauth_account, save_oauth_state,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
Provider = Literal["google", "github"]
STATE_COOKIE = "folio_oauth_state"
FLOW_SECONDS = 600
ERROR_CODES = {"cancelled", "invalid_state", "provider_error", "email_unverified",
               "account_exists", "identity_in_use", "session_expired", "not_configured"}


def provider_config(provider: Provider) -> dict:
    prefix = provider.upper()
    return {
        "client_id": os.getenv(f"{prefix}_CLIENT_ID", ""),
        "client_secret": os.getenv(f"{prefix}_CLIENT_SECRET", ""),
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth" if provider == "google"
                         else "https://github.com/login/oauth/authorize",
        "token_url": "https://oauth2.googleapis.com/token" if provider == "google"
                     else "https://github.com/login/oauth/access_token",
        "scope": "openid email profile" if provider == "google" else "read:user user:email",
    }


def provider_ready(provider: Provider) -> bool:
    config = provider_config(provider)
    return bool(config["client_id"] and config["client_secret"])


def redirect_result(error: str | None = None) -> RedirectResponse:
    query = {"auth_error": error if error in ERROR_CODES else "provider_error"} if error else {"auth": "success"}
    response = RedirectResponse(f"{app_url()}/?{urlencode(query)}", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/", secure=cookie_secure(), httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def begin_flow(provider: Provider, link_user_id: str | None = None):
    if not provider_ready(provider):
        if link_user_id:
            raise HTTPException(status_code=503, detail="This sign-in provider is not configured yet.")
        return redirect_result("not_configured")
    config = provider_config(provider)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    now = int(time.time())
    save_oauth_state(hash_token(state), provider, verifier, now + FLOW_SECONDS, link_user_id, now)
    parameters = {
        "client_id": config["client_id"],
        "redirect_uri": f"{app_url()}/api/auth/oauth/{provider}/callback",
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider == "google":
        parameters["prompt"] = "select_account"
    authorization_url = f"{config['authorize_url']}?{urlencode(parameters)}"
    response = JSONResponse({"authorization_url": authorization_url}) if link_user_id else RedirectResponse(authorization_url, status_code=302)
    response.set_cookie(STATE_COOKIE, state, max_age=FLOW_SECONDS, httponly=True,
                        secure=cookie_secure(), samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


async def exchange_identity(provider: Provider, code: str, verifier: str) -> tuple[str, str]:
    config = provider_config(provider)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        exchange = await client.post(config["token_url"], headers={"Accept": "application/json"}, data={
            "client_id": config["client_id"], "client_secret": config["client_secret"],
            "code": code, "code_verifier": verifier, "grant_type": "authorization_code",
            "redirect_uri": f"{app_url()}/api/auth/oauth/{provider}/callback",
        })
        exchange.raise_for_status()
        token = exchange.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("provider_error")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if provider == "google":
            result = await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
            result.raise_for_status()
            identity = result.json()
            if identity.get("email_verified") is not True:
                raise ValueError("email_unverified")
            subject, email = identity.get("sub"), identity.get("email")
        else:
            headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"})
            result = await client.get("https://api.github.com/user", headers=headers)
            result.raise_for_status()
            subject = str(result.json().get("id", ""))
            addresses = await client.get("https://api.github.com/user/emails", headers=headers,
                                         params={"per_page": 100})
            addresses.raise_for_status()
            verified = [item for item in addresses.json() if item.get("verified") is True]
            if not verified:
                raise ValueError("email_unverified")
            primary = next((item for item in verified if item.get("primary") is True), verified[0])
            email = primary.get("email")
        if not isinstance(subject, str) or not subject or subject == "None":
            raise ValueError("provider_error")
        email = str(TypeAdapter(EmailStr).validate_python(email)).casefold()
        return subject, email


@router.get("/providers")
def providers():
    return {provider: provider_ready(provider) for provider in ("google", "github")}


@router.get("/oauth/{provider}/start")
def start(provider: Provider):
    return begin_flow(provider)


@router.post("/oauth/{provider}/link")
def link(provider: Provider, user: CurrentUser):
    return begin_flow(provider, link_user_id=user.id)


@router.get("/oauth/{provider}/callback")
async def callback(provider: Provider, request: Request,
                   state: str | None = Query(default=None, max_length=256),
                   code: str | None = Query(default=None, max_length=4096),
                   error: str | None = Query(default=None, max_length=256)):
    browser_state = request.cookies.get(STATE_COOKIE, "")
    if not state or not browser_state or not hmac.compare_digest(state.encode(), browser_state.encode()):
        return redirect_result("invalid_state")
    flow = consume_oauth_state(hash_token(state), provider, int(time.time()))
    if flow is None:
        return redirect_result("invalid_state")
    if error:
        return redirect_result("cancelled")
    if not code or not provider_ready(provider):
        return redirect_result("provider_error")
    try:
        subject, email = await exchange_identity(provider, code, flow["code_verifier"])
        if flow["link_user_id"]:
            token = request.cookies.get(SESSION_COOKIE, "")
            user = get_session_user(hash_token(token), int(time.time())) if token else None
            if user is None or user["id"] != flow["link_user_id"]:
                return redirect_result("session_expired")
            link_oauth_account(user["id"], provider, subject, email)
            return redirect_result()
        user = get_or_create_oauth_user(provider, subject, email)
        response = redirect_result()
        create_login_session(user["id"], response)
        return response
    except (httpx.HTTPError, ValueError, sqlite3.IntegrityError) as exc:
        error_code = str(exc) if isinstance(exc, ValueError) and str(exc) in ERROR_CODES else "provider_error"
        logger.warning("OAuth sign-in failed for %s (%s)", provider, error_code)
        return redirect_result(error_code)
