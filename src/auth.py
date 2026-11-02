import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.db import (
    create_session,
    create_user,
    delete_session,
    get_session_user,
    get_linked_providers,
    get_user_by_email,
    get_user_by_username,
    update_password_hash,
)
from src.schemas.auth import Credentials, RegisterCredentials, SessionOutput, TokenOutput, UserOutput

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
password_hasher = PasswordHasher()
dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))
SESSION_SECONDS = 60 * 60
SESSION_COOKIE = "folio_session"


def app_url() -> str:
    return os.getenv("APP_URL", "http://localhost:5173").rstrip("/")


def cookie_secure() -> bool:
    return app_url().startswith("https://")


def csrf_token(token: str) -> str:
    return hmac.new(token.encode(), b"folio-csrf", hashlib.sha256).hexdigest()


def validate_auth_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in {app_url(), str(request.base_url).rstrip("/")}:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> str | None:
    token = credentials.credentials if credentials else request.cookies.get(SESSION_COOKIE)
    return token if token and len(token) <= 256 else None


def get_current_user(request: Request, credentials: BearerCredentials) -> UserOutput:
    token = request_token(request, credentials)
    if token:
        user = get_session_user(hash_token(token), int(time.time()))
        if user is not None:
            if credentials is None and request.method not in {"GET", "HEAD", "OPTIONS"}:
                validate_auth_origin(request)
                supplied = request.headers.get("x-csrf-token", "")
                if not hmac.compare_digest(supplied.encode(), csrf_token(token).encode()):
                    raise HTTPException(status_code=403, detail="Invalid CSRF token. Refresh and try again.")
            request.state.user_id = user["id"]
            return UserOutput(**user)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid Bearer token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentUser = Annotated[UserOutput, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> UserOutput:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


def create_login_session(user_id: str, response: Response) -> TokenOutput:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    create_session(hash_token(token), user_id, now + SESSION_SECONDS, now)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS,
                        httponly=True, secure=cookie_secure(), samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return TokenOutput(access_token=token, expires_in=SESSION_SECONDS)


@router.post("/register", response_model=UserOutput, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(validate_auth_origin)])
def register(body: RegisterCredentials):
    email = str(body.email).casefold()
    password_hash = password_hasher.hash(body.password.get_secret_value())
    try:
        return create_user(email, password_hash, email=email)
    except sqlite3.IntegrityError as exc:
        if get_user_by_email(email) is None:
            raise
        raise HTTPException(status_code=409, detail="An account already uses this email. Sign in with your original method.") from exc


@router.post("/login", response_model=TokenOutput, dependencies=[Depends(validate_auth_origin)])
def login(body: Credentials, response: Response):
    identifier = body.email.casefold()
    user = get_user_by_email(identifier) if "@" in identifier else get_user_by_username(identifier)
    password_hash = user["password_hash"] if user else dummy_password_hash
    password = body.password.get_secret_value()
    try:
        password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        user = None
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if password_hasher.check_needs_rehash(password_hash):
        update_password_hash(user["id"], password_hasher.hash(password))

    return create_login_session(user["id"], response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=[Depends(get_current_user)])
def logout(request: Request, credentials: BearerCredentials):
    delete_session(hash_token(request_token(request, credentials)))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=cookie_secure(),
                           httponly=True, samesite="lax")
    return response


@router.get("/me", response_model=UserOutput)
def me(user: CurrentUser):
    return user


@router.get("/session", response_model=SessionOutput)
def session(request: Request, response: Response, user: CurrentUser,
            credentials: BearerCredentials):
    response.headers["Cache-Control"] = "no-store"
    return SessionOutput(user=user, csrf_token=csrf_token(request_token(request, credentials)),
                         providers=get_linked_providers(user.id))
