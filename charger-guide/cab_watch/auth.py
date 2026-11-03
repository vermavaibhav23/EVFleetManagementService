"""Account authentication for CabWatch's private fleet endpoints."""

import asyncio
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

router = APIRouter(prefix="/auth")
bearer = HTTPBearer(auto_error=False)
ISSUER = "cabwatch"
AUDIENCE = "cabwatch-fleet"
hash_slots = asyncio.Semaphore(2)


class Credentials(BaseModel):
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class Signup(Credentials):
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")


async def password_digest(password, salt):
    async with hash_slots:
        return await asyncio.to_thread(
            hashlib.scrypt,
            password.encode(),
            salt=salt,
            n=2**15,
            r=8,
            p=3,
            maxmem=64 * 1024 * 1024,
            dklen=32,
        )


def issue(account):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        dict(
            sub=str(account["id"]),
            iss=ISSUER,
            aud=AUDIENCE,
            iat=now,
            exp=now + timedelta(hours=2),
        ),
        account["jwt_key"],
        algorithm="HS256",
    )
    return dict(access_token=token, token_type="bearer", name=account["name"])


@router.post("/register", status_code=201)
async def register(data: Signup, request: Request):
    salt = secrets.token_bytes(16)
    digest = await password_digest(data.password, salt)
    row = await request.app.state.db.fetchrow(
        """INSERT INTO cab_watch_accounts.users
        (id,name,email,password_hash,jwt_key) VALUES($1,$2,$3,$4,$5)
        ON CONFLICT(email) DO NOTHING RETURNING id,name,jwt_key""",
        uuid4(),
        data.name.strip(),
        data.email,
        salt.hex() + ":" + digest.hex(),
        secrets.token_bytes(32),
    )
    if not row:
        raise HTTPException(409, "That email is already registered.")
    return issue(row)


@router.post("/login")
async def login(data: Credentials, request: Request):
    row = await request.app.state.db.fetchrow(
        """SELECT id,name,password_hash,jwt_key
        FROM cab_watch_accounts.users WHERE email=$1""",
        data.email,
    )
    salt, expected = row["password_hash"].split(":") if row else ("00" * 16, "00" * 32)
    actual = await password_digest(data.password, bytes.fromhex(salt))
    if not hmac.compare_digest(actual, bytes.fromhex(expected)) or not row:
        raise HTTPException(401, "Email or password is incorrect.")
    return issue(row)


async def owner(
    request: Request, credentials: HTTPAuthorizationCredentials = Depends(bearer)
) -> UUID:
    denied = HTTPException(
        401, "Please sign in.", headers={"WWW-Authenticate": "Bearer"}
    )
    if not credentials or len(credentials.credentials) > 4096:
        raise denied
    token = credentials.credentials
    try:
        # Use the unverified subject only to look up the candidate verification key.
        subject = UUID(jwt.decode(token, options={"verify_signature": False})["sub"])
        key = await request.app.state.db.fetchval(
            "SELECT jwt_key FROM cab_watch_accounts.users WHERE id=$1", subject
        )
        if not key:
            raise denied
        claims = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
        return UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError, AttributeError):
        raise denied from None
