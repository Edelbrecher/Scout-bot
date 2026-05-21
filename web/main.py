import os
import asyncio
import base64
import re
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx
import stripe

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi import Request as StarletteRequest
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware

import database

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
SESSION_COOKIE = "scouter_session"
SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours

signer = URLSafeTimedSerializer(SECRET_KEY)

DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "http://localhost/auth/callback")

PERM_ADMINISTRATOR = 0x8
PERM_MANAGE_GUILD = 0x20

ADMIN_DISCORD_IDS = set(
    s.strip().strip('"').strip("'")
    for s in os.environ.get("ADMIN_DISCORD_IDS", "").split(",")
    if s.strip().strip('"').strip("'")
)
# Always include hardcoded fallback admin IDs
ADMIN_DISCORD_IDS.add("680889952883834882")

STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Pricing tiers: Starter / Clan / Alliance / Imperium
STRIPE_PRICES: dict[str, dict[str, str]] = {
    "starter": {
        "monthly": os.environ.get("STRIPE_PRICE_STARTER_M", "price_1TZBc13rAqb4qtRxOuJbuV8P"),
        "annual":  os.environ.get("STRIPE_PRICE_STARTER_A", "price_1TZBdb3rAqb4qtRxfyiH8bLx"),
    },
    "clan": {
        "monthly": os.environ.get("STRIPE_PRICE_CLAN_M", "price_1TZKYe3rAqb4qtRxbKdiDnKk"),
        "annual":  os.environ.get("STRIPE_PRICE_CLAN_A", "price_1TZKbS3rAqb4qtRx2dHjKNID"),
    },
    "alliance": {
        "monthly": os.environ.get("STRIPE_PRICE_ALLIANCE_M", "price_1TZKZC3rAqb4qtRxul7TvBlF"),
        "annual":  os.environ.get("STRIPE_PRICE_ALLIANCE_A", "price_1TZKcF3rAqb4qtRxyT6kMn8o"),
    },
    "imperium": {
        "monthly": os.environ.get("STRIPE_PRICE_IMPERIUM_M", "price_1TZKa33rAqb4qtRxUntPWFfM"),
        "annual":  os.environ.get("STRIPE_PRICE_IMPERIUM_A", "price_1TZKdO3rAqb4qtRxTio11mmP"),
    },
}
# Keep legacy vars as aliases for Starter
STRIPE_PRICE_MONTHLY = STRIPE_PRICES["starter"]["monthly"]
STRIPE_PRICE_ANNUAL  = STRIPE_PRICES["starter"]["annual"]

TIER_META = {
    "starter":  {"name": "Starter",  "servers": 1, "monthly": 6.99,  "annual": 55.99},
    "clan":     {"name": "Clan",     "servers": 2, "monthly": 10.99, "annual": 87.99},
    "alliance": {"name": "Alliance", "servers": 3, "monthly": 14.99, "annual": 119.99},
    "imperium": {"name": "Imperium", "servers": 5, "monthly": 19.99, "annual": 159.99},
}

# Discord snowflake: 17-20 digit numeric string
SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")

def is_snowflake(value: str) -> bool:
    return bool(SNOWFLAKE_RE.match(value.strip())) if value.strip() else True

def sanitize_snowflake(value: str) -> str:
    v = value.strip()
    return v if SNOWFLAKE_RE.match(v) else ""

def sanitize_snowflake_list(value: str) -> str:
    parts = [p.strip() for p in value.split(",") if SNOWFLAKE_RE.match(p.strip())]
    return ",".join(parts)


# ---------------------------------------------------------------------------
# Rate limiter (in-memory, per IP)
# ---------------------------------------------------------------------------

_rate_store: dict[str, list[float]] = defaultdict(list)

# Live user tracking
_active_users: dict[str, dict] = {}

def _is_rate_limited(ip: str, limit: int = 10, window: int = 60) -> bool:
    now = time.time()
    hits = [t for t in _rate_store[ip] if now - t < window]
    _rate_store[ip] = hits
    if len(hits) >= limit:
        return True
    _rate_store[ip].append(now)
    return False


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class UserTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Track active users
        token = request.cookies.get(SESSION_COOKIE)
        path = request.url.path
        if token:
            try:
                session_data = signer.loads(token, max_age=SESSION_MAX_AGE)
                if isinstance(session_data, dict):
                    uid = session_data.get("uid") or session_data.get("username", "")
                    uname = session_data.get("username", "")
                else:
                    uid = str(session_data)
                    uname = str(session_data)
            except Exception:
                uid = None
                uname = None
            ip = request.client.host if request.client else None
            if uid:
                _active_users[token] = {
                    "user_id": uid,
                    "username": uname,
                    "path": path,
                    "last_seen": time.time(),
                    "ip": ip,
                }
                # Cleanup old entries
                cutoff = time.time() - 300
                for k in list(_active_users.keys()):
                    if _active_users[k]["last_seen"] < cutoff:
                        del _active_users[k]
                # Log billing page visits to DB
                if "/billing" in path:
                    import asyncio as _aio
                    _aio.create_task(database.log_page_visit(uid, uname, path, ip))
        response = await call_next(request)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://js.stripe.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: cdn.discordapp.com; "
            "font-src 'self'; "
            "frame-src https://js.stripe.com https://hooks.stripe.com; "
            "connect-src 'self' https://api.stripe.com;"
        )
        return response


# ---------------------------------------------------------------------------
# Client ID helper
# ---------------------------------------------------------------------------

def get_client_id() -> str:
    token = os.environ.get("DISCORD_TOKEN", "")
    try:
        part = token.split(".")[0]
        padding = 4 - len(part) % 4
        return base64.b64decode(part + "=" * padding).decode()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
# Session dict: {"type": "discord"|"admin", "uid": str, "username": str, "guilds": list|None}
# guilds=None → super-admin, sees everything

def create_session(data: dict) -> str:
    return signer.dumps(data)


def get_session(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = signer.loads(token, max_age=SESSION_MAX_AGE)
        if isinstance(data, dict):
            return data
        # Legacy string session → treat as admin
        return {"type": "admin", "uid": data, "username": data, "guilds": None}
    except (BadSignature, SignatureExpired):
        return None


def can_access_guild(session: dict, guild_id: str) -> bool:
    if session.get("guilds") is None:
        return True  # super-admin
    return guild_id in session["guilds"]


def _require_session(request: Request):
    """Returns (session, error_response). error_response is set if auth fails."""
    session = get_session(request)
    if not session:
        return None, RedirectResponse("/login", status_code=303)
    return session, None


def _get_session(request: Request) -> dict | None:
    """Returns session dict or None without redirecting (for public pages)."""
    return get_session(request)


def _require_guild(session: dict, guild_id: str):
    """Returns error_response if guild access denied."""
    if not SNOWFLAKE_RE.match(guild_id):
        return RedirectResponse("/dashboard", status_code=303)
    if not can_access_guild(session, guild_id):
        return RedirectResponse("/dashboard", status_code=303)


def is_guild_owner(session: dict, guild: dict) -> bool:
    """True if the logged-in user is the subscription owner of this guild."""
    if session.get("type") == "admin":
        return True
    return session.get("uid", "") == (guild.get("owner_discord_id") or "")
    return None


PREMIUM_STATUSES = ("active", "trialing")


async def _require_premium(guild: dict | None, guild_id: str):
    """Returns redirect to billing if guild does not have an active subscription.
    past_due guilds are allowed through (grace period).
    Returns None if access is granted."""
    if guild is None:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=premium_required", status_code=303)
    status = guild.get("subscription_status") or "free"
    if status not in PREMIUM_STATUSES and status != "past_due":
        return RedirectResponse(f"/guild/{guild_id}/billing?error=premium_required", status_code=303)
    return None


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def _parse_map_sql(content: str) -> list[dict]:
    """Parse map.sql CSV content into a list of village dicts."""
    villages = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("INSERT") or line.startswith("--"):
            continue
        # Strip SQL INSERT wrapper if present
        if "VALUES" in line.upper():
            # Extract just the values part after VALUES
            idx = line.upper().find("VALUES")
            line = line[idx + 6:].strip().lstrip("(").rstrip(");")
        # Try comma separator
        parts = [p.strip().strip("'\"") for p in line.split(",")]
        if len(parts) < 8:
            continue
        try:
            # Detect format: if first field is large number (>10000) → village_id first
            first = parts[0].strip("'\" ")
            if first.lstrip("-").isdigit() and abs(int(first)) > 10000:
                # Format A: village_id, village_name, x, y, _, _, player_id, population, tribe, player_name, alliance_id, alliance_name
                vid = parts[0]
                vname = parts[1] if len(parts) > 1 else ""
                x = int(parts[2]) if len(parts) > 2 else 0
                y = int(parts[3]) if len(parts) > 3 else 0
                player_id = parts[6] if len(parts) > 6 else ""
                population = int(parts[7]) if len(parts) > 7 else 0
                tribe = int(parts[8]) if len(parts) > 8 else 0
                player_name = parts[9] if len(parts) > 9 else ""
                alliance_id = parts[10] if len(parts) > 10 else ""
                alliance_name = parts[11] if len(parts) > 11 else ""
            else:
                # Format B: x, y, tribe, village_id, village_name, player_id, player_name, population, alliance_id, alliance_name
                x = int(parts[0])
                y = int(parts[1])
                tribe = int(parts[2]) if len(parts) > 2 else 0
                vid = parts[3] if len(parts) > 3 else ""
                vname = parts[4] if len(parts) > 4 else ""
                player_id = parts[5] if len(parts) > 5 else ""
                player_name = parts[6] if len(parts) > 6 else ""
                population = int(parts[7]) if len(parts) > 7 else 0
                alliance_id = parts[8] if len(parts) > 8 else ""
                alliance_name = parts[9] if len(parts) > 9 else ""
            if not (-800 <= x <= 800 and -800 <= y <= 800):
                continue
            villages.append({
                "village_id": vid,
                "village_name": vname,
                "x": x, "y": y,
                "player_id": player_id,
                "player_name": player_name,
                "alliance_id": alliance_id,
                "alliance_name": alliance_name,
                "population": population,
                "tribe": tribe,
            })
        except (ValueError, IndexError):
            continue
    return villages


async def _fetch_and_save_snapshot(guild_id: str, tw_world: str):
    url = tw_world.rstrip("/") + "/map.sql"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        r.raise_for_status()
    villages = _parse_map_sql(r.text)
    if villages:
        await database.save_map_snapshot(guild_id, villages)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()

    async def _snapshot_loop():
        import datetime as _datetime
        # Initial fetch on startup (after short delay to let app boot)
        await asyncio.sleep(30)
        while True:
            guilds = await database.get_all_guilds()
            for g in guilds:
                tw_world = (g.get("tw_world") or "").strip()
                if not tw_world:
                    continue
                try:
                    latest = await database.get_latest_snapshot_time(g["guild_id"])
                    if latest:
                        age_h = (_datetime.datetime.utcnow() - _datetime.datetime.fromisoformat(latest)).total_seconds() / 3600
                        if age_h < 6:
                            continue
                    await _fetch_and_save_snapshot(g["guild_id"], tw_world)
                    # Keep only last 30 days of snapshots
                    await database.prune_old_snapshots(g["guild_id"], keep_days=30)
                except Exception:
                    pass
            await asyncio.sleep(6 * 3600)

    asyncio.create_task(_snapshot_loop())
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(UserTrackingMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")

from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        ctx = {"emoji": "🗺️", "code": "404", "message": "Diese Seite existiert nicht.", "detail": None}
    elif exc.status_code == 403:
        ctx = {"emoji": "🔒", "code": "403", "message": "Zugriff verweigert.", "detail": None}
    else:
        ctx = {"emoji": "⚙️", "code": str(exc.status_code), "message": str(exc.detail) if exc.detail else "Ein Fehler ist aufgetreten.", "detail": None}
    return templates.TemplateResponse("error.html", {"request": request, **ctx}, status_code=exc.status_code)

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    import traceback as _tb
    detail = type(exc).__name__ + ": " + str(exc)
    print(f"[500] {request.url} → {detail}\n{''.join(_tb.format_exc())}", flush=True)
    return templates.TemplateResponse("error.html", {
        "request": request,
        "emoji": "💥",
        "code": "500",
        "message": "Interner Serverfehler.",
        "detail": detail,
    }, status_code=500)

@app.get("/sw.js")
async def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})
templates = Jinja2Templates(directory="templates")

VIEW_CHANNEL = "1024"  # Discord permission bit

async def _sync_archive_permissions(guild_id: str, archive_channel_id: str, allowed_role_ids: str):
    """Set archive channel: @everyone hidden, allowed roles can view."""
    token = os.environ.get("DISCORD_TOKEN", "")
    overwrites = [
        {"id": guild_id, "type": 0, "allow": "0", "deny": VIEW_CHANNEL},  # @everyone
    ]
    for role_id in [r.strip() for r in allowed_role_ids.split(",") if r.strip()]:
        overwrites.append({"id": role_id, "type": 0, "allow": VIEW_CHANNEL, "deny": "0"})
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"https://discord.com/api/v10/channels/{archive_channel_id}",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"permission_overwrites": overwrites},
        )


def _get_username(request: Request) -> str:
    session = get_session(request)
    return session.get("username", "") if session else ""

templates.env.globals["get_username"] = _get_username

def _get_is_admin(request: Request) -> bool:
    session = get_session(request)
    return bool(session and session.get("type") == "admin")


def _require_admin(session: dict):
    """Return a redirect if the user is not a TravOps admin, else None."""
    if session.get("type") != "admin":
        return RedirectResponse("/dashboard", status_code=303)
    return None

templates.env.globals["get_is_admin"] = _get_is_admin

import json as _json
import datetime as _dt_global
templates.env.filters["from_json"] = lambda s: _json.loads(s) if s else []
templates.env.globals["current_year"] = _dt_global.datetime.utcnow().year


# ---------------------------------------------------------------------------
# Routes — auth
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not get_session(request):
        return RedirectResponse("/login")
    return RedirectResponse("/dashboard")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    # Generate OAuth state token and store in cookie
    state = secrets.token_urlsafe(32)
    response = templates.TemplateResponse("login.html", {"request": request, "error": error[:200]})
    response.set_cookie("oauth_state", state, max_age=300, httponly=True, samesite="lax")
    return response


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(ip, limit=10, window=60):
        return RedirectResponse("/login?error=Too+many+attempts.+Try+again+later.", status_code=303)
    # Sanitize inputs
    username = username.strip()[:64]
    if not username or len(password) > 256:
        return RedirectResponse("/login?error=Invalid+credentials", status_code=303)
    if await database.verify_password(username, password):
        token = create_session({"type": "admin", "uid": username, "username": username, "guilds": None})
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return response
    return RedirectResponse("/login?error=Invalid+credentials", status_code=303)


@app.get("/auth/discord")
async def auth_discord(request: Request):
    client_id = get_client_id()
    if not client_id or not DISCORD_CLIENT_SECRET:
        return RedirectResponse("/login?error=Discord+OAuth2+not+configured")
    state = request.cookies.get("oauth_state") or secrets.token_urlsafe(32)
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
    })
    response = RedirectResponse(f"https://discord.com/api/oauth2/authorize?{params}")
    response.set_cookie("oauth_state", state, max_age=300, httponly=True, samesite="lax")
    return response


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", error: str = "", state: str = ""):
    _ip = request.client.host if request.client else ""
    if error or not code:
        await database.log_auth(status="cancelled", ip=_ip, detail=error or "no_code")
        return RedirectResponse("/login?error=Discord+authentication+cancelled")

    # Validate OAuth state to prevent CSRF
    expected_state = request.cookies.get("oauth_state", "")
    if not expected_state or not secrets.compare_digest(expected_state, state):
        await database.log_auth(status="csrf_error", ip=_ip, detail="state_mismatch")
        return RedirectResponse("/login?error=Invalid+OAuth+state.+Please+try+again.")

    client_id = get_client_id()

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            await database.log_auth(status="token_error", ip=_ip, detail=f"status={r.status_code}")
            return RedirectResponse("/login?error=Discord+authentication+failed.+Please+try+again.")
        access_token = r.json()["access_token"]

        user_r = await client.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_r.status_code != 200:
            await database.log_auth(status="user_fetch_error", ip=_ip, detail=f"status={user_r.status_code}")
            return RedirectResponse("/login?error=Failed+to+fetch+user+info.")
        user = user_r.json()

        guilds_r = await client.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_guilds = guilds_r.json() if guilds_r.status_code == 200 else []

    bot_guild_ids = {g["guild_id"] for g in await database.get_all_guilds()}

    accessible = [
        g["id"] for g in user_guilds
        if g["id"] in bot_guild_ids
        and (
            g.get("owner")
            or (int(g.get("permissions", 0)) & PERM_ADMINISTRATOR)
            or (int(g.get("permissions", 0)) & PERM_MANAGE_GUILD)
        )
    ]
    # No longer block login if no accessible servers — users can subscribe without a server

    username = user.get("global_name") or user.get("username", "Unknown")
    discord_id = str(user["id"])

    # Gather log metadata before mutating DB
    _is_returning = await database.has_logged_in_before(discord_id)
    _sub = await database.get_user_subscription(discord_id)
    _has_active_sub = bool(_sub and _sub.get("subscription_status") in ("active", "trialing"))
    await database.log_auth(
        status="success",
        discord_id=discord_id,
        username=username,
        ip=_ip,
        guild_count=len(user_guilds),
        accessible_guilds=len(accessible),
        has_active_sub=_has_active_sub,
        is_returning=_is_returning,
    )

    session_type = "admin" if discord_id in ADMIN_DISCORD_IDS else "discord"
    session_data = {
        "type": session_type,
        "uid": discord_id,
        "username": username,
        "guilds": None if session_type == "admin" else accessible,
    }
    token = create_session(session_data)
    # Cache username in user_subscriptions so admin can see who is who
    await database.cache_discord_username(discord_id, username)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    response.delete_cookie("oauth_state")
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Routes — dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    session = get_session(request)
    if not session:
        return RedirectResponse("/login")
    all_guilds = await database.get_all_guilds()
    if session["guilds"] is not None:
        allowed = set(session["guilds"])
        guilds = [g for g in all_guilds if g["guild_id"] in allowed]
    else:
        guilds = all_guilds
    client_id = get_client_id()
    invite_url = (
        f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions=805432336&scope=bot+applications.commands"
        if client_id else ""
    )

    # Server-slot limits for this user
    owner_discord_id = session.get("uid", "")
    owner_active_guilds = await database.get_owner_active_guilds(owner_discord_id) if owner_discord_id else []
    slots_used = len(owner_active_guilds)
    slots_max = await database.get_owner_tier_limit(owner_discord_id) if owner_discord_id else 0
    slots_full = slots_max > 0 and slots_used >= slots_max

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "guilds": guilds,
            "invite_url": invite_url,
            "session": session,
            "slots_used": slots_used,
            "slots_max": slots_max,
            "slots_full": slots_full,
        },
    )


# ---------------------------------------------------------------------------
# Routes — guild
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}", response_class=HTMLResponse)
async def guild_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    token = os.environ.get("DISCORD_TOKEN", "")
    roles = []
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/roles",
            headers={"Authorization": f"Bot {token}"},
        )
        if r.status_code == 200:
            roles = sorted(r.json(), key=lambda x: -x.get("position", 0))

    is_admin = session.get("type") == "admin"
    is_owner = is_guild_owner(session, guild)
    return templates.TemplateResponse(
        "guild.html",
        {"request": request, "guild": guild, "saved": saved, "roles": roles,
         "is_admin": is_admin, "is_owner": is_owner},
    )


@app.get("/guild/{guild_id}/scout", response_class=HTMLResponse)
async def scout_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err
    scout_channels = await database.get_scout_channels(guild_id)
    return templates.TemplateResponse(
        "scout.html",
        {"request": request, "guild": guild, "scout_channels": scout_channels, "saved": saved},
    )


@app.post("/guild/{guild_id}/scout")
async def scout_save(
    request: Request,
    guild_id: str,
    category_id: str = Form(""),
    archive_channel_id: str = Form(""),
    allowed_role_ids: str = Form(""),
    scout_channel_id: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err

    category_id = sanitize_snowflake(category_id)
    archive_channel_id = sanitize_snowflake(archive_channel_id)
    scout_channel_id = sanitize_snowflake(scout_channel_id)
    normalized_roles = sanitize_snowflake_list(allowed_role_ids)

    await database.update_guild_config(
        guild_id=guild_id,
        category_id=category_id,
        archive_channel_id=archive_channel_id,
        allowed_role_ids=normalized_roles,
        scout_channel_id=scout_channel_id,
    )
    if archive_channel_id:
        await _sync_archive_permissions(guild_id, archive_channel_id, normalized_roles)
    return RedirectResponse(f"/guild/{guild_id}/scout?saved=1", status_code=303)


@app.post("/guild/{guild_id}")
async def guild_save(
    request: Request,
    guild_id: str,
    category_id: str = Form(""),
    archive_channel_id: str = Form(""),
    allowed_role_ids: str = Form(""),
    scout_channel_id: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    category_id = sanitize_snowflake(category_id)
    archive_channel_id = sanitize_snowflake(archive_channel_id)
    scout_channel_id = sanitize_snowflake(scout_channel_id)
    normalized_roles = sanitize_snowflake_list(allowed_role_ids)

    await database.update_guild_config(
        guild_id=guild_id,
        category_id=category_id,
        archive_channel_id=archive_channel_id,
        allowed_role_ids=normalized_roles,
        scout_channel_id=scout_channel_id,
    )
    if archive_channel_id:
        await _sync_archive_permissions(guild_id, archive_channel_id, normalized_roles)
    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)


@app.post("/guild/{guild_id}/roles/{role_id}/toggle")
async def toggle_role(request: Request, guild_id: str, role_id: str, field: str = Form(...)):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=403)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    if field not in {"allowed_role_ids", "res_manager_role_ids"}:
        return JSONResponse({"error": "invalid field"}, status_code=400)
    if not SNOWFLAKE_RE.match(role_id):
        return JSONResponse({"error": "invalid role_id"}, status_code=400)
    added = await database.toggle_role_in_field(guild_id, role_id, field)
    if field == "allowed_role_ids":
        guild = await database.get_guild(guild_id)
        if guild and guild.get("archive_channel_id"):
            await _sync_archive_permissions(guild_id, guild["archive_channel_id"], guild.get("allowed_role_ids") or "")
    return JSONResponse({"added": added})


@app.post("/guild/{guild_id}/reset-scout")
async def reset_scout(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.reset_scout_config(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/scout?saved=1", status_code=303)


@app.post("/guild/{guild_id}/res-push/reset")
async def reset_res_push(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.reset_res_config(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


@app.post("/guild/{guild_id}/auto-setup")
async def auto_setup(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    token = os.environ.get("DISCORD_TOKEN", "")
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        # Get bot's own user ID for permission overwrites
        bot_user_r = await client.get("https://discord.com/api/v10/users/@me", headers=headers)
        bot_id = bot_user_r.json()["id"] if bot_user_r.status_code == 200 else None

        # Permission bits
        ALLOW_BOT = str(1024 + 2048 + 16384 + 32768)  # VIEW_CHANNEL + SEND_MESSAGES + EMBED_LINKS + ATTACH_FILES

        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "Scout", "type": 4})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=category_{r.status_code}", status_code=303)
        category_id = r.json()["id"]

        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "scout-requests", "type": 0, "parent_id": category_id})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=scout_ch_{r.status_code}", status_code=303)
        scout_channel_id = r.json()["id"]

        # Archive channel: hidden from @everyone, bot has explicit send/attach perms
        archive_overwrites = [{"id": guild_id, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}]
        if bot_id:
            archive_overwrites.append({"id": bot_id, "type": 1, "allow": ALLOW_BOT, "deny": "0"})

        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={
            "name": "scout-archive", "type": 0, "parent_id": category_id,
            "permission_overwrites": archive_overwrites,
        })
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=archive_ch_{r.status_code}", status_code=303)
        archive_channel_id = r.json()["id"]

        payload = {
            "embeds": [{"title": "📡 Scout Request", "description": "Click the button below to submit a scout request.\nFill in the coordinates, player, village and time.", "color": 5793266}],
            "components": [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Scout Request", "emoji": {"name": "🔍"}, "custom_id": "persistent:scout_request"}]}]
        }
        r = await client.post(f"https://discord.com/api/v10/channels/{scout_channel_id}/messages", headers=headers, json=payload)
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=button_{r.status_code}", status_code=303)
        button_message_id = r.json()["id"]

    await database.auto_setup_guild(guild_id=guild_id, category_id=category_id, scout_channel_id=scout_channel_id, archive_channel_id=archive_channel_id, button_message_id=button_message_id)
    return RedirectResponse(f"/guild/{guild_id}/scout?saved=1", status_code=303)


@app.post("/guild/{guild_id}/fix-archive-perms")
async def fix_archive_perms(request: Request, guild_id: str):
    """Fix bot permissions on the archive channel."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild or not guild.get("archive_channel_id"):
        return RedirectResponse(f"/guild/{guild_id}?error=no_archive_channel", status_code=303)

    token = os.environ.get("DISCORD_TOKEN", "")
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    ALLOW_BOT = str(1024 + 2048 + 16384 + 32768)
    archive_channel_id = guild["archive_channel_id"]

    async with httpx.AsyncClient() as client:
        bot_user_r = await client.get("https://discord.com/api/v10/users/@me", headers=headers)
        if bot_user_r.status_code != 200:
            return RedirectResponse(f"/guild/{guild_id}?error=bot_id_failed", status_code=303)
        bot_id = bot_user_r.json()["id"]
        r = await client.put(
            f"https://discord.com/api/v10/channels/{archive_channel_id}/permissions/{bot_id}",
            headers=headers,
            json={"allow": ALLOW_BOT, "deny": "0", "type": 1},
        )
        if r.status_code not in (200, 201, 204):
            return RedirectResponse(f"/guild/{guild_id}/settings?flash=⚠️+Fehler:+perms_{r.status_code}", status_code=303)

    return RedirectResponse(f"/guild/{guild_id}/settings?flash=✅+Berechtigungen+erfolgreich+repariert", status_code=303)


@app.get("/guild/{guild_id}/stats", response_class=HTMLResponse)
async def guild_stats(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    scout_stats = await database.get_guild_stats(guild_id)
    res_stats = await database.get_res_stats(guild_id)
    polls = await database.get_polls(guild_id)
    for p in polls:
        responses = await database.get_poll_responses(p["id"])
        p["count_available"]   = sum(1 for r in responses if r["response"] == "available")
        p["count_maybe"]       = sum(1 for r in responses if r["response"] == "maybe")
        p["count_unavailable"] = sum(1 for r in responses if r["response"] == "unavailable")
    poll_participation  = await database.get_poll_participation_stats(guild_id)
    res_leaderboard     = await database.get_res_contribution_leaderboard(guild_id)
    res_contrib_details = await database.get_res_contribution_details(guild_id)
    scout_requesters    = await database.get_scout_requester_stats(guild_id)
    token = os.environ.get("DISCORD_TOKEN", "")
    discord_guild = None
    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://discord.com/api/v10/guilds/{guild_id}?with_counts=true", headers={"Authorization": f"Bot {token}"})
        if r.status_code == 200:
            discord_guild = r.json()

    return templates.TemplateResponse("stats.html", {
        "request": request, "guild": guild,
        "scout_stats": scout_stats, "res_stats": res_stats, "polls": polls,
        "discord_guild": discord_guild,
        "poll_participation": poll_participation,
        "res_leaderboard": res_leaderboard,
        "res_contrib_details": res_contrib_details,
        "scout_requesters": scout_requesters,
    })


@app.post("/guild/{guild_id}/post-button")
async def post_button(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    guild = await database.get_guild(guild_id)
    if not guild or not guild.get("scout_channel_id"):
        return RedirectResponse(f"/guild/{guild_id}?error=no_channel", status_code=303)

    token = os.environ.get("DISCORD_TOKEN", "")
    channel_id = guild["scout_channel_id"]
    payload = {
        "embeds": [{"title": "📡 Scout Request", "description": "Click the button below to submit a scout request.\nFill in the coordinates, player, village and time.", "color": 5793266}],
        "components": [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Scout Request", "emoji": {"name": "🔍"}, "custom_id": "persistent:scout_request"}]}]
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"https://discord.com/api/v10/channels/{channel_id}/messages", headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"}, json=payload)

    if resp.status_code in (200, 201):
        msg_id = resp.json().get("id", "")
        await database.update_button_message(guild_id, channel_id, msg_id)
        return RedirectResponse(f"/guild/{guild_id}/scout?saved=1", status_code=303)
    return RedirectResponse(f"/guild/{guild_id}/scout?error=discord_{resp.status_code}", status_code=303)


# ---------------------------------------------------------------------------
# Routes — res-push
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/res-push", response_class=HTMLResponse)
async def res_push_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    res_requests = await database.get_res_requests(guild_id)
    return templates.TemplateResponse("res_push.html", {"request": request, "guild": guild, "res_requests": res_requests, "saved": saved})


@app.post("/guild/{guild_id}/res-push")
async def res_push_save(
    request: Request,
    guild_id: str,
    res_request_channel_id: str = Form(""),
    res_answer_channel_id: str = Form(""),
    res_push_category_id: str = Form(""),
    res_manager_role_ids: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    await database.update_res_config(
        guild_id=guild_id,
        res_request_channel_id=sanitize_snowflake(res_request_channel_id),
        res_answer_channel_id=sanitize_snowflake(res_answer_channel_id),
        res_push_category_id=sanitize_snowflake(res_push_category_id),
        res_manager_role_ids=sanitize_snowflake_list(res_manager_role_ids),
    )
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


@app.post("/guild/{guild_id}/res-push/post-button")
async def res_post_button(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild or not guild.get("res_request_channel_id"):
        return RedirectResponse(f"/guild/{guild_id}/res-push?error=no_channel", status_code=303)

    token = os.environ.get("DISCORD_TOKEN", "")
    channel_id = guild["res_request_channel_id"]
    payload = {
        "embeds": [{"title": "🪖 Res-Push Request", "description": "Click the button below to submit a resource push request.", "color": 5793266}],
        "components": [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Res Request", "emoji": {"name": "🪖"}, "custom_id": "persistent:res_request"}]}]
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"https://discord.com/api/v10/channels/{channel_id}/messages", headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"}, json=payload)

    if resp.status_code in (200, 201):
        msg_id = resp.json().get("id", "")
        await database.update_res_button(guild_id, channel_id, msg_id)
        return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)
    return RedirectResponse(f"/guild/{guild_id}/res-push?error=discord_{resp.status_code}", status_code=303)


@app.post("/guild/{guild_id}/res-push/auto-setup")
async def res_auto_setup(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    token = os.environ.get("DISCORD_TOKEN", "")
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "Res-Push", "type": 4})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/res-push?error=category_{r.status_code}", status_code=303)
        category_id = r.json()["id"]

        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "res-request", "type": 0, "parent_id": category_id})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/res-push?error=req_ch_{r.status_code}", status_code=303)
        res_request_channel_id = r.json()["id"]

        # res-answer is manager-only: deny @everyone, allow manager roles
        res_answer_overwrites = [{"id": guild_id, "type": 0, "allow": "0", "deny": VIEW_CHANNEL}]
        for rid in (guild.get("res_manager_role_ids") or "").split(","):
            rid = rid.strip()
            if rid:
                res_answer_overwrites.append({"id": rid, "type": 0, "allow": VIEW_CHANNEL, "deny": "0"})
        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={
            "name": "res-answer", "type": 0, "parent_id": category_id,
            "permission_overwrites": res_answer_overwrites,
        })
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/res-push?error=ans_ch_{r.status_code}", status_code=303)
        res_answer_channel_id = r.json()["id"]

        payload = {
            "embeds": [{"title": "🪖 Res-Push Request", "description": "Click the button below to submit a resource push request.", "color": 5793266}],
            "components": [{"type": 1, "components": [{"type": 2, "style": 1, "label": "Res Request", "emoji": {"name": "🪖"}, "custom_id": "persistent:res_request"}]}]
        }
        r = await client.post(f"https://discord.com/api/v10/channels/{res_request_channel_id}/messages", headers=headers, json=payload)
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/res-push?error=button_{r.status_code}", status_code=303)
        res_button_message_id = r.json()["id"]

    await database.update_res_config(guild_id=guild_id, res_request_channel_id=res_request_channel_id, res_answer_channel_id=res_answer_channel_id, res_push_category_id=category_id, res_manager_role_ids=guild.get("res_manager_role_ids") or "")
    await database.update_res_button(guild_id, res_request_channel_id, res_button_message_id)
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


@app.get("/guild/{guild_id}/res-push/stats", response_class=HTMLResponse)
async def res_push_stats(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    stats = await database.get_res_stats(guild_id)
    return templates.TemplateResponse("res_push_stats.html", {"request": request, "guild": guild, "stats": stats})


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/inactive")
async def res_request_inactive(request: Request, guild_id: str, request_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    # Verify the request belongs to this guild
    req = await database.get_res_request_by_id_web(request_id)
    if not req or req.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/res-push", status_code=303)
    await database.set_res_request_status_by_id(request_id, "inactive")
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/activate")
async def res_request_activate(request: Request, guild_id: str, request_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    req = await database.get_res_request_by_id_web(request_id)
    if not req or req.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/res-push", status_code=303)
    await database.set_res_request_status_by_id(request_id, "accepted")
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


async def _close_scout_channel_after_delay(channel_id: str, token: str, delay: int = 120):
    await asyncio.sleep(delay)
    async with httpx.AsyncClient() as client:
        await client.delete(f"https://discord.com/api/v10/channels/{channel_id}", headers={"Authorization": f"Bot {token}"})
    await database.delete_scout_channel(channel_id)


@app.post("/guild/{guild_id}/scout-channels/{channel_id}/close")
async def scout_channel_close(request: Request, guild_id: str, channel_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if not SNOWFLAKE_RE.match(channel_id):
        return RedirectResponse(f"/guild/{guild_id}", status_code=303)
    # Verify the channel belongs to this guild
    ch = await database.get_scout_channel_info(channel_id)
    if not ch or ch.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}", status_code=303)
    token = os.environ.get("DISCORD_TOKEN", "")
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"content": "🔒 Scout channel closed via dashboard. Channel will be deleted in 2 minutes."},
        )
    asyncio.create_task(_close_scout_channel_after_delay(channel_id, token, delay=120))
    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Routes — polls
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/polls", response_class=HTMLResponse)
async def polls_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    polls = await database.get_polls(guild_id)
    # Attach response counts to each poll
    for p in polls:
        responses = await database.get_poll_responses(p["id"])
        p["count_available"]   = sum(1 for r in responses if r["response"] == "available")
        p["count_maybe"]       = sum(1 for r in responses if r["response"] == "maybe")
        p["count_unavailable"] = sum(1 for r in responses if r["response"] == "unavailable")
        p["responses"]         = responses
    is_admin = session.get("type") == "admin"
    return templates.TemplateResponse("polls.html", {
        "request": request, "guild": guild, "polls": polls, "saved": saved, "is_admin": is_admin,
    })


@app.post("/guild/{guild_id}/polls/config")
async def polls_config_save(request: Request, guild_id: str, poll_channel_id: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.update_poll_channel(guild_id, sanitize_snowflake(poll_channel_id))
    return RedirectResponse(f"/guild/{guild_id}/polls?saved=1", status_code=303)


@app.post("/guild/{guild_id}/polls/create")
async def polls_create(
    request: Request,
    guild_id: str,
    title: str = Form(...),
    description: str = Form(""),
    event_datetime: str = Form(...),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild or not guild.get("poll_channel_id"):
        return RedirectResponse(f"/guild/{guild_id}/polls?error=no_channel", status_code=303)

    title       = title.strip()[:100]
    description = description.strip()[:500]

    poll_id = await database.create_poll(guild_id, title, description, event_datetime)

    token      = os.environ.get("DISCORD_TOKEN", "")
    channel_id = guild["poll_channel_id"]
    embed = {
        "title": f"📅 {title}",
        "description": description or "",
        "color": 5793266,
        "fields": [{"name": "🕐 Zeitpunkt (UTC)", "value": event_datetime.replace("T", " "), "inline": False}],
        "footer": {"text": f"Umfrage #{poll_id} · Klicke einen Button um deine Verfügbarkeit anzugeben"},
    }
    components = [{"type": 1, "components": [
        {"type": 2, "style": 3, "label": "Dabei",       "emoji": {"name": "✅"}, "custom_id": f"poll_available_{poll_id}"},
        {"type": 2, "style": 1, "label": "Vielleicht",  "emoji": {"name": "⏰"}, "custom_id": f"poll_maybe_{poll_id}"},
        {"type": 2, "style": 4, "label": "Nicht dabei", "emoji": {"name": "❌"}, "custom_id": f"poll_unavailable_{poll_id}"},
    ]}]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"embeds": [embed], "components": components},
        )
    if resp.status_code in (200, 201):
        await database.set_poll_message_id(poll_id, resp.json()["id"])
    return RedirectResponse(f"/guild/{guild_id}/polls?saved=1", status_code=303)


@app.post("/guild/{guild_id}/polls/auto-setup")
async def polls_auto_setup(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    token = os.environ.get("DISCORD_TOKEN", "")
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "Umfragen", "type": 4})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/polls?error=category_{r.status_code}", status_code=303)
        category_id = r.json()["id"]
        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "umfragen", "type": 0, "parent_id": category_id})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/polls?error=channel_{r.status_code}", status_code=303)
        poll_channel_id = r.json()["id"]
    await database.update_poll_channel(guild_id, poll_channel_id)
    return RedirectResponse(f"/guild/{guild_id}/polls?saved=1", status_code=303)


@app.post("/guild/{guild_id}/polls/reset")
async def polls_reset(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.update_poll_channel(guild_id, "")
    return RedirectResponse(f"/guild/{guild_id}/polls?saved=1", status_code=303)


@app.post("/guild/{guild_id}/polls/{poll_id}/close")
async def polls_close(request: Request, guild_id: str, poll_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    poll = await database.get_poll(poll_id)
    if not poll or poll.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
    await database.close_poll(poll_id)
    # Edit Discord message to remove buttons
    if poll.get("discord_message_id") and poll.get("poll_channel_id") or True:
        guild = await database.get_guild(guild_id)
        if guild and guild.get("poll_channel_id") and poll.get("discord_message_id"):
            token = os.environ.get("DISCORD_TOKEN", "")
            responses = await database.get_poll_responses(poll_id)
            c_av = sum(1 for r in responses if r["response"] == "available")
            c_ma = sum(1 for r in responses if r["response"] == "maybe")
            c_un = sum(1 for r in responses if r["response"] == "unavailable")
            closed_embed = {
                "title": f"🔒 {poll['title']} (geschlossen)",
                "description": poll.get("description") or "",
                "color": 0x555555,
                "fields": [
                    {"name": "🕐 Zeitpunkt (UTC)", "value": poll["event_datetime"].replace("T", " "), "inline": False},
                    {"name": "Ergebnis", "value": f"✅ Dabei: {c_av} · ⏰ Vielleicht: {c_ma} · ❌ Nicht dabei: {c_un}", "inline": False},
                ],
            }
            async with httpx.AsyncClient() as client:
                await client.patch(
                    f"https://discord.com/api/v10/channels/{guild['poll_channel_id']}/messages/{poll['discord_message_id']}",
                    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                    json={"embeds": [closed_embed], "components": []},
                )
    return RedirectResponse(f"/guild/{guild_id}/polls?saved=1", status_code=303)


@app.post("/guild/{guild_id}/polls/{poll_id}/responses/edit")
async def poll_response_edit(
    request: Request, guild_id: str, poll_id: int,
    user_id: str = Form(...), user_name: str = Form(...), response: str = Form(...),
):
    session, err = _require_session(request)
    if err: return err
    # Only admin session or guild owner can override closed poll responses
    if session.get("type") != "admin":
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
    err = _require_guild(session, guild_id)
    if err: return err
    poll = await database.get_poll(poll_id)
    if not poll or poll.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
    if response not in ("available", "maybe", "unavailable"):
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
    await database.upsert_poll_response_admin(poll_id, user_id, user_name, response)
    return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)


@app.post("/guild/{guild_id}/polls/{poll_id}/responses/delete")
async def poll_response_delete(
    request: Request, guild_id: str, poll_id: int,
    user_id: str = Form(...),
):
    session, err = _require_session(request)
    if err: return err
    if session.get("type") != "admin":
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
    err = _require_guild(session, guild_id)
    if err: return err
    poll = await database.get_poll(poll_id)
    if not poll or poll.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
    await database.delete_poll_response(poll_id, user_id)
    return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)


@app.post("/guild/{guild_id}/polls/{poll_id}/delete")
async def polls_delete(request: Request, guild_id: str, poll_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    poll = await database.get_poll(poll_id)
    if not poll or poll.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
    await database.delete_poll(poll_id)
    return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)


@app.get("/guild/{guild_id}/timer", response_class=HTMLResponse)
async def guild_timer(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("timer.html", {"request": request, "guild": guild})


@app.get("/guild/{guild_id}/map", response_class=HTMLResponse)
async def guild_map(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err
    is_admin = session.get("type") == "admin"
    scouted = await database.get_scouted_coordinates(guild_id)
    return templates.TemplateResponse("map.html", {
        "request": request,
        "guild": guild,
        "scouted": scouted,
        "is_admin": is_admin,
    })


@app.post("/guild/{guild_id}/map/world")
async def guild_map_set_world(request: Request, guild_id: str, server_url: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    # Validate: must be https://....travian.com or similar
    url = server_url.strip().rstrip("/")
    if url and not re.match(r"^https://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", url):
        return RedirectResponse(f"/guild/{guild_id}/map?error=invalid_url", status_code=303)
    await database.update_tw_world(guild_id, url)
    return RedirectResponse(f"/guild/{guild_id}", status_code=303)


@app.get("/guild/{guild_id}/map/data")
async def guild_map_data(request: Request, guild_id: str):
    """Proxy Travian map.sql to avoid CORS issues."""
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=403)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    guild = await database.get_guild(guild_id)
    server_url = (guild or {}).get("tw_world", "")
    if not server_url:
        return JSONResponse({"error": "no server configured"}, status_code=400)
    if not re.match(r"^https://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", server_url):
        return JSONResponse({"error": "invalid server url"}, status_code=400)
    url = f"{server_url}/map.sql"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return JSONResponse({"error": f"Server returned {r.status_code}"}, status_code=502)
            return JSONResponse({"data": r.text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/remove")
async def res_request_remove(request: Request, guild_id: str, request_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    req = await database.get_res_request_by_id_web(request_id)
    if not req or req.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/res-push", status_code=303)
    if req.get("push_channel_id"):
        bot_token = os.environ.get("DISCORD_TOKEN", "")
        async with httpx.AsyncClient() as client:
            await client.delete(f"https://discord.com/api/v10/channels/{req['push_channel_id']}", headers={"Authorization": f"Bot {bot_token}"})
    await database.delete_res_request(request_id)
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Billing routes
# ---------------------------------------------------------------------------

def _stripe_client():
    if not STRIPE_SECRET_KEY:
        return None
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


@app.get("/guild/{guild_id}/settings")
async def guild_settings_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    flash = request.query_params.get("flash", "")
    # Load Discord roles for the guild (same as main guild page)
    token = os.environ.get("DISCORD_TOKEN", "")
    roles = []
    if token:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"https://discord.com/api/v10/guilds/{guild_id}/roles",
                    headers={"Authorization": f"Bot {token}"},
                    timeout=5,
                )
                if r.status_code == 200:
                    roles = sorted(r.json(), key=lambda x: -x.get("position", 0))
        except Exception:
            pass
    return templates.TemplateResponse("guild_settings.html", {
        "request": request,
        "guild": guild,
        "is_owner": is_guild_owner(session, guild),
        "flash": flash,
        "roles": roles,
    })


@app.get("/guild/{guild_id}/billing")
async def billing_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    is_admin = session.get("type") == "admin"
    if not is_guild_owner(session, guild):
        return RedirectResponse(f"/guild/{guild_id}?error=billing_owner_only", status_code=303)
    stripe_configured = bool(STRIPE_SECRET_KEY)
    return templates.TemplateResponse("billing.html", {
        "request": request,
        "guild": guild,
        "is_admin": is_admin,
        "stripe_pk": STRIPE_PUBLISHABLE_KEY,
        "stripe_configured": stripe_configured,
        "tier_meta": TIER_META,
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@app.post("/guild/{guild_id}/billing/checkout")
async def billing_checkout(
    request: Request,
    guild_id: str,
    plan: str = Form("monthly"),
    tier: str = Form("starter"),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    s = _stripe_client()
    if not s:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=stripe_not_configured", status_code=303)
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    tier = tier if tier in STRIPE_PRICES else "starter"
    interval = "monthly" if plan == "monthly" else "annual"
    price_id = STRIPE_PRICES[tier][interval]
    if not price_id:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=price_not_configured", status_code=303)

    base_url = os.environ.get("BASE_URL", str(request.base_url).rstrip("/"))
    customer_id = guild.get("stripe_customer_id") or None

    checkout_kwargs = dict(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        allow_promotion_codes=True,
        subscription_data={"trial_period_days": 7},
        success_url=f"{base_url}/guild/{guild_id}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}/guild/{guild_id}/billing?error=cancelled",
        metadata={"guild_id": guild_id, "tier": tier, "owner_discord_id": session.get("uid", "")},
    )
    if customer_id:
        checkout_kwargs["customer"] = customer_id

    try:
        checkout_session = s.checkout.Session.create(**checkout_kwargs)
    except Exception as e:
        print(f"[billing] Stripe checkout error: {e}")
        return RedirectResponse(
            f"/guild/{guild_id}/billing?error={str(e)[:80].replace(' ', '+')}",
            status_code=303,
        )
    return RedirectResponse(checkout_session.url, status_code=303)


@app.get("/guild/{guild_id}/billing/success")
async def billing_success(request: Request, guild_id: str, session_id: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    s = _stripe_client()
    if not s or not session_id:
        return RedirectResponse(f"/guild/{guild_id}/billing", status_code=303)

    try:
        checkout = s.checkout.Session.retrieve(session_id, expand=["subscription"])
        if checkout.metadata.get("guild_id") != guild_id:
            return RedirectResponse(f"/guild/{guild_id}/billing?error=invalid", status_code=303)
        sub = checkout.subscription
        interval = "annual" if sub.items.data[0].price.recurring.interval == "year" else "monthly"
        tier = checkout.metadata.get("tier", "starter")
        plan_str = f"{tier}_{interval}"
        import datetime
        expires_at = datetime.datetime.utcfromtimestamp(sub.current_period_end).isoformat()
        owner_discord_id = checkout.metadata.get("owner_discord_id") or session.get("uid", "")
        await database.update_subscription(
            guild_id=guild_id,
            stripe_customer_id=checkout.customer,
            stripe_subscription_id=sub.id,
            status="active",
            plan=plan_str,
            expires_at=expires_at,
            owner_discord_id=owner_discord_id or None,
        )
    except Exception:
        pass

    return RedirectResponse(f"/guild/{guild_id}/billing?saved=1", status_code=303)


@app.post("/guild/{guild_id}/remove-bot")
async def remove_bot(request: Request, guild_id: str):
    """Owner removes the bot from their server — frees the subscription slot."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    if not is_guild_owner(session, guild):
        return RedirectResponse(f"/guild/{guild_id}?error=only_owner", status_code=303)

    # Make the bot leave via Discord API
    bot_token = os.environ.get("DISCORD_TOKEN", "")
    left_ok = False
    if bot_token:
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"https://discord.com/api/v10/users/@me/guilds/{guild_id}",
                headers={"Authorization": f"Bot {bot_token}"},
            )
            left_ok = r.status_code in (204, 200)

    # Mark as kicked + keep subscription data intact (slot freed via bot_status)
    await database.set_bot_kicked(guild_id)
    print(f"[remove-bot] Guild {guild_id} removed by owner {session.get('uid')} — Discord leave: {left_ok}")
    return RedirectResponse("/dashboard?removed=1", status_code=303)


@app.post("/guild/{guild_id}/billing/portal")
async def billing_portal(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    s = _stripe_client()
    if not s:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=stripe_not_configured", status_code=303)
    guild = await database.get_guild(guild_id)
    customer_id = (guild or {}).get("stripe_customer_id")
    if not customer_id:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=no_subscription", status_code=303)
    base_url = os.environ.get("BASE_URL", str(request.base_url).rstrip("/"))
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{base_url}/guild/{guild_id}/billing",
    )
    return RedirectResponse(portal.url, status_code=303)


# ---------------------------------------------------------------------------
# Routes — Plans (subscribe without a server)
# ---------------------------------------------------------------------------

@app.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request, error: str = ""):
    session = _get_session(request)
    user_sub = None
    if session:
        user_sub = await database.get_user_subscription(session.get("uid", ""))
    return templates.TemplateResponse("plans.html", {
        "request": request,
        "tier_meta": TIER_META,
        "logged_in": bool(session),
        "user_sub": user_sub,
        "error": error,
    })


@app.post("/plans/checkout")
async def plans_checkout(request: Request, plan: str = Form("monthly"), tier: str = Form("starter")):
    session, err = _require_session(request)
    if err:
        return err
    s = _stripe_client()
    if not s:
        return RedirectResponse("/plans?error=stripe_not_configured", status_code=303)

    tier = tier if tier in STRIPE_PRICES else "starter"
    interval = "monthly" if plan == "monthly" else "annual"
    price_id = STRIPE_PRICES[tier][interval]
    if not price_id:
        return RedirectResponse("/plans?error=price_not_configured", status_code=303)

    base_url = os.environ.get("BASE_URL", str(request.base_url).rstrip("/"))
    discord_user_id = session.get("uid", "")

    # Re-use existing Stripe customer if available
    user_sub = await database.get_user_subscription(discord_user_id)
    customer_id = (user_sub or {}).get("stripe_customer_id") or None

    checkout_kwargs = dict(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        allow_promotion_codes=True,
        subscription_data={"trial_period_days": 7},
        success_url=f"{base_url}/plans/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}/plans?error=cancelled",
        metadata={
            "discord_user_id": discord_user_id,
            "tier": tier,
            "source": "plans",
        },
    )
    if customer_id:
        checkout_kwargs["customer"] = customer_id

    try:
        checkout_session = s.checkout.Session.create(**checkout_kwargs)
    except Exception as e:
        print(f"[plans/checkout] Stripe error: {e}")
        return RedirectResponse(
            f"/plans?error={str(e)[:80].replace(' ', '+')}",
            status_code=303,
        )
    return RedirectResponse(checkout_session.url, status_code=303)


@app.get("/plans/success")
async def plans_success(request: Request, session_id: str = ""):
    web_session, err = _require_session(request)
    if err:
        return err
    s = _stripe_client()
    if not s or not session_id:
        return RedirectResponse("/plans", status_code=303)

    try:
        checkout = s.checkout.Session.retrieve(session_id, expand=["subscription"])
        if checkout.metadata.get("source") == "plans":
            sub = checkout.subscription
            interval = "annual" if sub.items.data[0].price.recurring.interval == "year" else "monthly"
            tier = checkout.metadata.get("tier", "starter")
            plan_str = f"{tier}_{interval}"
            import datetime
            expires_at = datetime.datetime.utcfromtimestamp(sub.current_period_end).isoformat()
            discord_user_id = checkout.metadata.get("discord_user_id", web_session.get("uid", ""))
            await database.upsert_user_subscription(
                discord_user_id=discord_user_id,
                stripe_customer_id=checkout.customer,
                stripe_subscription_id=sub.id,
                status="active",
                plan=plan_str,
                expires_at=expires_at,
            )
    except Exception as e:
        print(f"[plans/success] Error: {e}")

    return RedirectResponse("/dashboard?saved=1", status_code=303)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not STRIPE_WEBHOOK_SECRET:
        return Response(status_code=400)
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return Response(status_code=400)

    obj = event["data"]["object"]

    if event["type"] in ("customer.subscription.updated", "customer.subscription.created"):
        customer_id = obj.get("customer")
        import datetime

        # Check if this is a user-level subscription
        user_sub = await database.get_user_by_stripe_customer(customer_id)
        if user_sub:
            status = obj.get("status", "inactive")
            interval = obj.get("items", {}).get("data", [{}])[0].get("price", {}).get("recurring", {}).get("interval", "month")
            plan_interval = "annual" if interval == "year" else "monthly"
            existing_plan = (user_sub.get("plan") or "starter").split("_")[0]
            plan_str = f"{existing_plan}_{plan_interval}"
            expires_at = datetime.datetime.utcfromtimestamp(obj["current_period_end"]).isoformat()
            await database.upsert_user_subscription(
                discord_user_id=user_sub["discord_user_id"],
                stripe_customer_id=customer_id,
                stripe_subscription_id=obj["id"],
                status=status,
                plan=plan_str,
                expires_at=expires_at,
            )
        else:
            guild = await database.get_guild_by_stripe_customer(customer_id)
            if guild:
                status = obj.get("status", "inactive")
                interval = obj.get("items", {}).get("data", [{}])[0].get("price", {}).get("recurring", {}).get("interval", "month")
                plan = "annual" if interval == "year" else "monthly"
                expires_at = datetime.datetime.utcfromtimestamp(obj["current_period_end"]).isoformat()
                await database.update_subscription(
                    guild_id=guild["guild_id"],
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=obj["id"],
                    status=status,
                    plan=plan,
                    expires_at=expires_at,
                )

    elif event["type"] == "checkout.session.completed":
        # Handle user-level checkout completions (source=plans)
        meta = obj.get("metadata") or {}
        if meta.get("source") == "plans" and meta.get("discord_user_id"):
            discord_user_id = meta["discord_user_id"]
            tier = meta.get("tier", "starter")
            import datetime
            # Retrieve subscription details if available
            sub_id = obj.get("subscription")
            status = "active"
            expires_at = None
            plan_str = f"{tier}_monthly"
            if sub_id:
                s = _stripe_client()
                if s:
                    try:
                        sub = s.subscriptions.retrieve(sub_id)
                        status = sub.status
                        interval = sub.items.data[0].price.recurring.interval
                        plan_str = f"{tier}_{'annual' if interval == 'year' else 'monthly'}"
                        expires_at = datetime.datetime.utcfromtimestamp(sub.current_period_end).isoformat()
                    except Exception:
                        pass
            await database.upsert_user_subscription(
                discord_user_id=discord_user_id,
                stripe_customer_id=obj.get("customer", ""),
                stripe_subscription_id=sub_id or "",
                status=status,
                plan=plan_str,
                expires_at=expires_at,
            )

    elif event["type"] == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        user_sub = await database.get_user_by_stripe_customer(customer_id)
        if user_sub:
            await database.upsert_user_subscription(
                discord_user_id=user_sub["discord_user_id"],
                stripe_customer_id=customer_id,
                stripe_subscription_id=user_sub.get("stripe_subscription_id", ""),
                status="cancelled",
                plan=user_sub.get("plan", ""),
                expires_at=user_sub.get("expires_at"),
            )
        else:
            guild = await database.get_guild_by_stripe_customer(customer_id)
            if guild:
                await database.set_subscription_status(guild["guild_id"], "cancelled")

    elif event["type"] == "invoice.payment_failed":
        customer_id = obj.get("customer")
        user_sub = await database.get_user_by_stripe_customer(customer_id)
        if user_sub:
            await database.upsert_user_subscription(
                discord_user_id=user_sub["discord_user_id"],
                stripe_customer_id=customer_id,
                stripe_subscription_id=user_sub.get("stripe_subscription_id", ""),
                status="past_due",
                plan=user_sub.get("plan", ""),
                expires_at=user_sub.get("expires_at"),
            )
        else:
            guild = await database.get_guild_by_stripe_customer(customer_id)
            if guild:
                await database.set_subscription_status(guild["guild_id"], "past_due")


# ---------------------------------------------------------------------------
# Own-village helpers
# ---------------------------------------------------------------------------

def classify_own_village(troops: dict) -> tuple:
    """Returns (village_type, off_score, def_score, priority)"""
    TROOP_OFF = {
        "Imperianer": 70, "Equites Imperatoris": 120, "Equites Caesaris": 180,
        "Axtkämpfer": 55, "Teut. Ritter": 150, "Keulenschwinger": 40,
        "Theutates-Blitz": 90, "Haeduer": 200, "Schwertkämpfer": 65,
    }
    TROOP_DEF = {
        "Prätorianer": 65, "Legionär": 35, "Equites Legati": 20,
        "Speerkämpfer": 60, "Paladin": 100,
        "Phalanx": 40, "Druidentreiter": 115,
    }
    TROOP_SCOUT = {"Equites Legati": 1, "Späher": 1, "Pathfinder": 1}
    SIEGE = {
        "Rammbock": 1, "Feuerkatapult": 1, "Teutonen-Rammbock": 1,
        "Kriegsmaschine": 1, "Gallier-Rammbock": 1, "Gallier-Kata": 1,
    }

    off_score = sum(TROOP_OFF.get(t, 0) * c for t, c in troops.items())
    def_score = sum(TROOP_DEF.get(t, 0) * c for t, c in troops.items())
    scout_count = sum(c for t, c in troops.items() if t in TROOP_SCOUT)
    siege_count = sum(c for t, c in troops.items() if t in SIEGE)
    total = sum(troops.values())

    if total == 0:
        return "leer", 0, 0, 0
    if siege_count > 5:
        return "off", off_score, def_score, off_score
    if off_score > def_score * 2:
        return "off", off_score, def_score, off_score
    if def_score > off_score * 1.5:
        return "def", off_score, def_score, def_score
    if scout_count > total * 0.5:
        return "scout", off_score, def_score, 10
    return "mixed", off_score, def_score, (off_score + def_score) // 2


def parse_own_villages(text: str) -> list:
    """
    Parse Travian 'Eigene Truppen' overview copy-paste (Strg+A / Strg+C).

    Format: tab-delimited table with header "Dorfname\tTroop1\tTroop2\t..."
    followed by village rows "VillageName\tN1\tN2\t..."
    Village coordinates come from the sidebar section below the table.

    Also handles alternative Gaul troop names as used in the overview UI:
    Theutates Blitz, Druidenreiter, Haeduaner, Rammholz, Kriegskatapult etc.
    """
    import re
    text = re.sub(r'[​-‏‪-‮⁦-⁩﻿]', '', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Canonical name aliases for troop names as they appear in the overview UI
    TROOP_ALIASES = {
        # Gaul overview names → canonical
        "Theutates Blitz":  "Theutates-Blitz",
        "Theutates-Blitz":  "Theutates-Blitz",
        "Druidenreiter":    "Druidentreiter",
        "Haeduaner":        "Haeduer",
        "Rammholz":         "Gallier-Rammbock",
        "Kriegskatapult":   "Gallier-Kata",
        # Gaul scout: "Späher" in overview = Pathfinder
        # (only when other Gaul troops present — handled by context)
        # Teuton overview names
        "Kundschafter":     "Späher",
        "Teutonen Reiter":  "Teut. Ritter",
        "Ramme":            "Teutonen-Rammbock",
        "Katapult":         "Kriegsmaschine",
        "Stammesführer":    "Häuptling",
    }

    def normalize(name: str) -> str:
        name = name.strip()
        return TROOP_ALIASES.get(name, name)

    lines = text.split('\n')
    coord_re = re.compile(r'\((-?\d+)\|(-?\d+)\)')

    # ── Step 1: Find the "Dorfname" header row ────────────────────────────
    header_idx = None
    col_names = []
    for i, line in enumerate(lines):
        parts = [p.strip() for p in line.split('\t')]
        if parts and parts[0].lower() == 'dorfname' and len(parts) >= 3:
            header_idx = i
            col_names = [normalize(p) for p in parts[1:]]  # skip "Dorfname"
            break

    # Detect if this is Gaul from header — "Späher" in Gaul context maps to Pathfinder
    is_gaul = any(n in col_names for n in ("Phalanx", "Theutates-Blitz", "Gallier-Rammbock", "Druidentreiter", "Haeduer", "Gallier-Kata"))
    if is_gaul:
        col_names = ["Pathfinder" if n == "Späher" else n for n in col_names]

    # ── Step 2: Parse village rows from the table ─────────────────────────
    # Each row: VillageName\tN1\tN2\t...  (stops at "Summe" row)
    table_villages = {}  # name → troops dict
    if header_idx is not None:
        for line in lines[header_idx + 1:]:
            parts = [p.strip() for p in line.split('\t')]
            if not parts or not parts[0]:
                continue
            vname = parts[0]
            if vname.lower() in ('summe', 'total', 'gesamt'):
                break
            # Check if row has numeric data
            nums_raw = parts[1:]
            nums = []
            for n in nums_raw:
                n_clean = re.sub(r'[ \s., ]', '', n)
                if re.match(r'^\d+$', n_clean):
                    nums.append(int(n_clean))
                else:
                    nums.append(None)
            if not any(isinstance(n, int) for n in nums):
                continue
            troops = {}
            for k, n in enumerate(nums):
                if k < len(col_names) and isinstance(n, int) and n > 0:
                    troops[col_names[k]] = n
            table_villages[vname] = troops

    # ── Step 3: Parse sidebar for village coordinates ─────────────────────
    # Sidebar format:
    #   VillageName\n
    #   (x|y)\n
    #   [optional group label]\n
    sidebar_coords = {}  # name → (x, y)
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        cm = coord_re.match(line)
        if cm:
            x, y = int(cm.group(1)), int(cm.group(2))
            # Look back for the village name (line before coords)
            if i > 0:
                prev = lines[i - 1].strip()
                if prev and not coord_re.search(prev) and '\t' not in prev:
                    sidebar_coords[prev] = (x, y)
        # Also handle "VillageName\t(x|y)" on same line
        elif '\t' in line:
            for m in re.finditer(r'^([^\t]+)\t\((-?\d+)\|(-?\d+)\)', line):
                sidebar_coords[m.group(1).strip()] = (int(m.group(2)), int(m.group(3)))
        i += 1

    # ── Step 4: Merge table rows with sidebar coords ──────────────────────
    villages = []
    for vname, troops in table_villages.items():
        coords = sidebar_coords.get(vname)
        x = coords[0] if coords else None
        y = coords[1] if coords else None
        villages.append({
            "village_name": vname,
            "x": x,
            "y": y,
            "population": 0,
            "troops": troops,
        })

    # Fallback: if no table found, try old coord-on-same-line format
    if not villages:
        for line in lines:
            cm = coord_re.search(line)
            if not cm:
                continue
            x, y = int(cm.group(1)), int(cm.group(2))
            vname = line[:cm.start()].strip().rstrip('\t').strip() or f"({x}|{y})"
            villages.append({"village_name": vname, "x": x, "y": y, "population": 0, "troops": {}})

    return villages


# ---------------------------------------------------------------------------
# Routes — Attacks
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/attacks", response_class=HTMLResponse)
async def attacks_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err
    import json as _jl, datetime as _dtl, math as _ml
    is_admin = session.get("type") == "admin"
    attack_reports = await database.get_attack_reports(guild_id, limit=50)
    attack_stats = await database.get_attack_stats(guild_id)

    # For each report: parse arrival times and detect zwischendef windows
    def _parse_remaining(arrival_str):
        import re as _re2
        m = _re2.search(r"in\s+(\d+):(\d+):(\d+)", arrival_str or "")
        return int(m.group(1))*3600+int(m.group(2))*60+int(m.group(3)) if m else None

    enriched_reports = []
    for rpt in attack_reports:
        try:
            waves = _jl.loads(rpt.get("attacks_json") or "[]")
        except Exception:
            waves = []
        created_at = rpt.get("created_at", "")
        try:
            created_ts = _dtl.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            created_ts = None
        timed_waves = []
        for w in waves:
            rs = _parse_remaining(w.get("arrival", ""))
            if created_ts and rs is not None:
                timed_waves.append(created_ts + _dtl.timedelta(seconds=rs))
            else:
                timed_waves.append(None)
        # Detect zwischendef
        zw = []
        valid = [(i, t) for i, t in enumerate(timed_waves) if t]
        valid.sort(key=lambda x: x[1])
        for j in range(len(valid)-1):
            ia, ta = valid[j]
            ib, tb = valid[j+1]
            if ta != tb:
                gap = int((tb - ta).total_seconds())
                if gap > 60:
                    gap_m = gap // 60
                    zw.append({
                        "wave_a": ia+1, "wave_b": ib+1,
                        "gap_label": f"{gap_m//60}h {gap_m%60}min" if gap_m>=60 else f"{gap_m}min",
                        "arrival_a": ta.strftime("%H:%M UTC"),
                        "arrival_b": tb.strftime("%H:%M UTC"),
                    })
        enriched_reports.append({**dict(rpt), "zwischendef": zw, "wave_count": len(waves)})

    return templates.TemplateResponse(
        "attacks.html",
        {
            "request": request,
            "guild": guild,
            "saved": saved,
            "is_admin": is_admin,
            "attack_reports": enriched_reports,
            "attack_stats": attack_stats,
        },
    )


@app.get("/guild/{guild_id}/attacks/{report_id}/analyse", response_class=HTMLResponse)
async def attacks_analyse(request: Request, guild_id: str, report_id: int):
    import json as _json, math as _math
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    guild = await database.get_guild(guild_id)

    report = await database.get_attack_report(guild_id, report_id)
    if not report:
        return RedirectResponse(f"/guild/{guild_id}/attacks", status_code=303)

    try:
        attacks = _json.loads(report["attacks_json"])
    except Exception:
        attacks = []

    MAP_SIZE = 400

    def travian_dist(x1, y1, x2, y2):
        dx = abs(x1 - x2)
        dy = abs(y1 - y2)
        if dx > MAP_SIZE: dx = 2 * MAP_SIZE - dx
        if dy > MAP_SIZE: dy = 2 * MAP_SIZE - dy
        return _math.sqrt(dx * dx + dy * dy)

    TROOP_DATA = {
        # Romans
        "Legionär":            {"speed": 6,  "atk": 40,  "def_inf": 35,  "def_cav": 50,  "tribe": "Römer",    "crop": 1, "tp": 40},
        "Prätorianer":         {"speed": 5,  "atk": 5,   "def_inf": 65,  "def_cav": 35,  "tribe": "Römer",    "crop": 1, "tp": 30},
        "Imperianer":          {"speed": 7,  "atk": 70,  "def_inf": 30,  "def_cav": 25,  "tribe": "Römer",    "crop": 1, "tp": 70},
        "Equites Legati":      {"speed": 16, "atk": 40,  "def_inf": 20,  "def_cav": 10,  "tribe": "Römer",    "crop": 2, "tp": 80},
        "Equites Imperatoris": {"speed": 14, "atk": 120, "def_inf": 65,  "def_cav": 50,  "tribe": "Römer",    "crop": 3, "tp": 260},
        "Equites Caesaris":    {"speed": 10, "atk": 180, "def_inf": 80,  "def_cav": 105, "tribe": "Römer",    "crop": 4, "tp": 450},
        "Rammbock":            {"speed": 4,  "atk": 60,  "def_inf": 30,  "def_cav": 75,  "tribe": "Römer",    "crop": 5, "tp": 300},
        "Feuerkatapult":       {"speed": 3,  "atk": 75,  "def_inf": 60,  "def_cav": 10,  "tribe": "Römer",    "crop": 6, "tp": 600},
        "Senator":             {"speed": 4,  "atk": 50,  "def_inf": 40,  "def_cav": 30,  "tribe": "Römer",    "crop": 5, "tp": 500},
        # Teutons
        "Keulenschwinger":     {"speed": 7,  "atk": 40,  "def_inf": 20,  "def_cav": 5,   "tribe": "Germanen", "crop": 1, "tp": 40},
        "Speerkämpfer":        {"speed": 7,  "atk": 10,  "def_inf": 35,  "def_cav": 60,  "tribe": "Germanen", "crop": 1, "tp": 20},
        "Axtkämpfer":          {"speed": 7,  "atk": 55,  "def_inf": 10,  "def_cav": 5,   "tribe": "Germanen", "crop": 1, "tp": 60},
        "Späher":              {"speed": 9,  "atk": 0,   "def_inf": 10,  "def_cav": 10,  "tribe": "Germanen", "crop": 1, "tp": 20},
        "Paladin":             {"speed": 10, "atk": 55,  "def_inf": 100, "def_cav": 40,  "tribe": "Germanen", "crop": 2, "tp": 160},
        "Teut. Ritter":        {"speed": 11, "atk": 150, "def_inf": 50,  "def_cav": 75,  "tribe": "Germanen", "crop": 3, "tp": 400},
        "Häuptling":           {"speed": 7,  "atk": 40,  "def_inf": 60,  "def_cav": 40,  "tribe": "Germanen", "crop": 4, "tp": 500},
        "Teutonen-Rammbock":   {"speed": 5,  "atk": 65,  "def_inf": 30,  "def_cav": 80,  "tribe": "Germanen", "crop": 5, "tp": 350},
        "Kriegsmaschine":      {"speed": 3,  "atk": 50,  "def_inf": 60,  "def_cav": 10,  "tribe": "Germanen", "crop": 6, "tp": 600},
        # Gauls
        "Phalanx":             {"speed": 7,  "atk": 15,  "def_inf": 40,  "def_cav": 50,  "tribe": "Gallier",  "crop": 1, "tp": 20},
        "Schwertkämpfer":      {"speed": 6,  "atk": 65,  "def_inf": 35,  "def_cav": 20,  "tribe": "Gallier",  "crop": 1, "tp": 60},
        "Pathfinder":          {"speed": 17, "atk": 0,   "def_inf": 10,  "def_cav": 10,  "tribe": "Gallier",  "crop": 2, "tp": 20},
        "Theutates-Blitz":     {"speed": 19, "atk": 90,  "def_inf": 25,  "def_cav": 10,  "tribe": "Gallier",  "crop": 2, "tp": 160},
        "Druidentreiter":      {"speed": 16, "atk": 45,  "def_inf": 115, "def_cav": 55,  "tribe": "Gallier",  "crop": 2, "tp": 240},
        "Haeduer":             {"speed": 13, "atk": 200, "def_inf": 45,  "def_cav": 80,  "tribe": "Gallier",  "crop": 3, "tp": 500},
        "Stammesältester":     {"speed": 5,  "atk": 40,  "def_inf": 50,  "def_cav": 50,  "tribe": "Gallier",  "crop": 5, "tp": 500},
        "Gallier-Rammbock":    {"speed": 6,  "atk": 50,  "def_inf": 30,  "def_cav": 105, "tribe": "Gallier",  "crop": 5, "tp": 320},
        "Gallier-Kata":        {"speed": 3,  "atk": 70,  "def_inf": 45,  "def_cav": 10,  "tribe": "Gallier",  "crop": 6, "tp": 600},
    }
    # Add aliases for alternate UI names (Teuton variants etc.)
    TROOP_ALIASES = {
        "Kundschafter":    "Späher",
        "Teutonen Reiter": "Teut. Ritter",
        "Ramme":           "Teutonen-Rammbock",
        "Katapult":        "Kriegsmaschine",
        "Stammesführer":   "Häuptling",
        "Aufklärer":       "Pathfinder",
    }
    for alias, canonical in TROOP_ALIASES.items():
        if canonical in TROOP_DATA:
            TROOP_DATA[alias] = TROOP_DATA[canonical]
    TROOP_SPEEDS = {name: d["speed"] for name, d in TROOP_DATA.items()}
    TROOP_TRIBE  = {name: d["tribe"] for name, d in TROOP_DATA.items()}
    TRIBE_EMOJI = {1: "🏛️", 2: "⚒️", 3: "🌿", 4: "🌑", 5: "⛩️"}

    enriched = []
    for atk in attacks:
        troops = atk.get("troops", {})
        total_troops = sum(troops.values()) if troops else 0

        # Classify attack
        has_siege = any(k in troops for k in ("Rammbock", "Feuerkatapult", "Teutonen-Rammbock",
                                               "Kriegsmaschine", "Gallier-Rammbock", "Gallier-Kata",
                                               "Ramme", "Katapult"))
        has_cav   = any(k in troops for k in ("Equites Imperatoris", "Equites Caesaris", "Paladin",
                                               "Teut. Ritter", "Teutonen Reiter", "Theutates-Blitz",
                                               "Druidentreiter", "Haeduer"))
        if total_troops == 0:
            classification = "❓ Keine Truppendaten"
            classification_color = "#888"
        elif total_troops <= 3:
            classification = "🪶 Fake / Probe"
            classification_color = "#f59e0b"
        elif has_siege:
            classification = "💥 Echte Belagerung"
            classification_color = "#dc2626"
        elif has_cav and total_troops > 100:
            classification = "⚡ Kavallerie-Angriff"
            classification_color = "#f97316"
        elif total_troops > 500:
            classification = "⚔️ Großangriff"
            classification_color = "#dc2626"
        else:
            classification = "🗡️ Normaler Angriff"
            classification_color = "#ef4444"

        # Detected tribe — prefer field stored by parser, fallback to vote from troop names
        detected_tribe = atk.get("tribe")
        if not detected_tribe:
            tribe_votes = {}
            for tname in troops:
                t = TROOP_TRIBE.get(tname)
                if t: tribe_votes[t] = tribe_votes.get(t, 0) + 1
            detected_tribe = max(tribe_votes, key=tribe_votes.get) if tribe_votes else None

        # Slowest troop = determines march speed
        if troops:
            slowest_speed = min(TROOP_SPEEDS.get(t, 6) for t in troops)
        else:
            slowest_speed = None

        # Parse remaining march time from arrival string e.g. "in 22:00:28 Std. um 15:00:14"
        import re as _re
        remaining_seconds = None
        arrival_str = atk.get("arrival", "")
        rm = _re.search(r"in\s+(\d+):(\d+):(\d+)", arrival_str)
        if rm:
            remaining_seconds = int(rm.group(1)) * 3600 + int(rm.group(2)) * 60 + int(rm.group(3))

        # Parse coords from attacker village "(x|y)"
        atk_x = atk_y = None
        cm = _re.search(r"\((-?\d+)\|(-?\d+)\)", atk.get("coords", ""))
        if cm:
            atk_x, atk_y = int(cm.group(1)), int(cm.group(2))

        # Defender coords — stored in report by bot modal
        def_x = atk.get("def_x")
        def_y = atk.get("def_y")

        # Offline time from report (user was offline this long → adds to possible march window)
        offline_seconds = atk.get("offline_seconds", 0) or 0

        # Server-side speed analysis if we have both coord sets + remaining time
        speed_analysis = None
        import datetime as _dt
        try:
            created_ts = _dt.datetime.fromisoformat(report["created_at"].replace("Z", "+00:00"))
        except Exception:
            created_ts = None

        arrival_ts = None
        if created_ts and remaining_seconds is not None:
            arrival_ts = created_ts + _dt.timedelta(seconds=remaining_seconds)

        if atk_x is not None and def_x is not None and remaining_seconds is not None:
            dist = travian_dist(atk_x, atk_y, def_x, def_y)
            # Total possible window = remaining + offline (attack could have been sent while offline)
            total_window_seconds = remaining_seconds + offline_seconds
            total_window_hours = total_window_seconds / 3600.0
            # max_speed: if the attack was sent right as user went offline
            max_speed = dist / total_window_hours if total_window_hours > 0 else 0
            # min_speed: attack was sent as soon as it was visible (remaining only)
            remaining_hours = remaining_seconds / 3600.0
            min_speed = dist / remaining_hours if remaining_hours > 0 else 0

            possible_speeds = []
            seen_speeds = set()
            for tname, tdata in TROOP_DATA.items():
                s = tdata["speed"]
                if s in seen_speeds:
                    continue
                # Possible if speed <= min_speed (certain) or <= max_speed (possible with offline window)
                if s > max_speed + 0.01:
                    continue
                seen_speeds.add(s)
                total_march_s = int(dist / s * 3600)
                departure_ts = None
                if arrival_ts:
                    departure_ts = arrival_ts - _dt.timedelta(seconds=total_march_s)
                certain = s <= min_speed + 0.01  # certain even without offline window
                possible_speeds.append({
                    "speed": s,
                    "total_march_s": total_march_s,
                    "departure": departure_ts.strftime("%H:%M:%S %d.%m.") if departure_ts else None,
                    "certain": certain,
                })
            possible_speeds.sort(key=lambda x: x["speed"])

            best_speed = max((p for p in possible_speeds if p["certain"]), key=lambda x: x["speed"])["speed"] if any(p["certain"] for p in possible_speeds) else (max(possible_speeds, key=lambda x: x["speed"])["speed"] if possible_speeds else None)

            speed_to_troops = {}
            for tname, tdata in TROOP_DATA.items():
                s = tdata["speed"]
                if s <= max_speed + 0.01:
                    speed_to_troops.setdefault(s, []).append(tname)

            speed_analysis = {
                "dist": round(dist, 1),
                "remaining_hours": round(remaining_hours, 2),
                "offline_hours": round(offline_seconds / 3600, 2),
                "total_window_hours": round(total_window_hours, 2),
                "max_speed": round(max_speed, 2),
                "min_speed": round(min_speed, 2),
                "possible_speeds": possible_speeds,
                "speed_to_troops": {str(k): v for k, v in speed_to_troops.items()},
                "best_speed": best_speed,
                "def_x": def_x,
                "def_y": def_y,
                "arrival_ts": arrival_ts.strftime("%d.%m. %H:%M:%S UTC") if arrival_ts else None,
            }

        # Map lookup: attacker player data + attacker village confirmation + defender village
        attacker_data = await database.get_player_from_snapshot(guild_id, atk.get("attacker", ""))
        # Confirm attacker village from map
        attacker_village_data = None
        if atk_x is not None and attacker_data:
            for v in attacker_data.get("villages", []):
                if v["x"] == atk_x and v["y"] == atk_y:
                    attacker_village_data = v
                    break
        # Look up defender village from map by coords
        defender_village_data = None
        if def_x is not None and def_y is not None:
            defender_village_data = await database.get_village_from_snapshot(guild_id, def_x, def_y)

        enriched.append({
            **atk,
            "total_troops": total_troops,
            "classification": classification,
            "classification_color": classification_color,
            "detected_tribe": detected_tribe,
            "slowest_speed": slowest_speed,
            "atk_x": atk_x,
            "atk_y": atk_y,
            "def_x": def_x,
            "def_y": def_y,
            "offline_seconds": offline_seconds,
            "attacker_data": attacker_data,
            "attacker_village_data": attacker_village_data,
            "defender_village_data": defender_village_data,
            "remaining_seconds": remaining_seconds,
            "arrival_ts": arrival_ts,
            "speed_analysis": speed_analysis,
            "is_stack": False,
            "stack_count": 1,
        })

    # Wave stack detection: same arrival → stack
    from collections import defaultdict as _defaultdict
    arrival_groups = _defaultdict(list)
    for i, atk in enumerate(enriched):
        key = atk.get("arrival", "")
        if key:
            arrival_groups[key].append(i)
    for indices in arrival_groups.values():
        if len(indices) > 1:
            for i in indices:
                enriched[i]["is_stack"] = True
                enriched[i]["stack_count"] = len(indices)

    # Zwischendef detection: multiple waves with DIFFERENT arrival times
    import datetime as _dt2
    zwischendef_windows = []
    if len(enriched) > 1:
        # Sort by arrival_ts
        timed = [(i, e) for i, e in enumerate(enriched) if e.get("arrival_ts")]
        timed.sort(key=lambda x: x[1]["arrival_ts"])
        for j in range(len(timed) - 1):
            idx_a, wave_a = timed[j]
            idx_b, wave_b = timed[j + 1]
            if wave_a["arrival_ts"] != wave_b["arrival_ts"]:
                gap = (wave_b["arrival_ts"] - wave_a["arrival_ts"]).total_seconds()
                if gap > 60:  # only meaningful gaps
                    gap_m = int(gap // 60)
                    zwischendef_windows.append({
                        "wave_a": j + 1,
                        "wave_b": j + 2,
                        "arrival_a": wave_a["arrival_ts"].strftime("%H:%M:%S UTC"),
                        "arrival_b": wave_b["arrival_ts"].strftime("%H:%M:%S UTC"),
                        "gap_seconds": int(gap),
                        "gap_label": f"{gap_m // 60}h {gap_m % 60}min" if gap_m >= 60 else f"{gap_m}min",
                        "attacker_a": wave_a.get("attacker", "?"),
                        "attacker_b": wave_b.get("attacker", "?"),
                    })

    import json as _json2
    troop_data_json = _json2.dumps(TROOP_DATA)

    # Historical reports from same attacker
    all_attackers = {atk.get("attacker") for atk in attacks if atk.get("attacker")}
    history = await database.get_reports_by_attackers(guild_id, list(all_attackers))

    # Load own villages and cross-reference defender coords
    own_villages_raw = await database.get_own_villages(guild_id)
    own_by_coords = {}
    for v in own_villages_raw:
        try:
            v["troops"] = _json2.loads(v.get("troops_json") or "{}")
        except Exception:
            v["troops"] = {}
        if v.get("x") is not None and v.get("y") is not None:
            own_by_coords[(v["x"], v["y"])] = v

    # Attach own-village data to each attack wave
    for atk in enriched:
        dx, dy = atk.get("def_x"), atk.get("def_y")
        if dx is not None and dy is not None:
            atk["own_village"] = own_by_coords.get((int(dx), int(dy)))
        else:
            atk["own_village"] = None

    return templates.TemplateResponse("attack_analysis.html", {
        "request": request,
        "guild": guild,
        "report": report,
        "attacks": enriched,
        "history_count": len(history),
        "history": history[:20],
        "TRIBE_EMOJI": TRIBE_EMOJI,
        "troop_data_json": troop_data_json,
        "troop_data": TROOP_DATA,
        "zwischendef_windows": zwischendef_windows,
        "own_villages_count": len(own_villages_raw),
    })


@app.post("/guild/{guild_id}/attacks/config")
async def attacks_config_save(
    request: Request,
    guild_id: str,
    attack_channel_id: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    attack_channel_id = sanitize_snowflake(attack_channel_id)
    await database.set_attack_channel_web(guild_id, attack_channel_id)
    return RedirectResponse(f"/guild/{guild_id}/attacks?saved=1", status_code=303)


@app.post("/guild/{guild_id}/attacks/delete/{report_id}")
async def attacks_delete(request: Request, guild_id: str, report_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    if session.get("type") != "admin":
        return RedirectResponse(f"/guild/{guild_id}/attacks", status_code=303)
    await database.delete_attack_report(report_id)
    return RedirectResponse(f"/guild/{guild_id}/attacks", status_code=303)


@app.post("/guild/{guild_id}/attacks/auto-setup")
async def attacks_auto_setup(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    token = os.environ.get("DISCORD_TOKEN", "")
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        # Category
        r = await client.post(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers,
            json={"name": "Angriff-Detection", "type": 4},
        )
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/attacks?error=category_{r.status_code}", status_code=303)
        category_id = r.json()["id"]

        # Get bot's own user ID
        me = await client.get("https://discord.com/api/v10/users/@me", headers=headers)
        bot_id = me.json().get("id", "")

        # Alert channel — hidden from @everyone, bot has full access
        overwrites = [
            {"id": guild_id, "type": 0, "allow": "0", "deny": "1024"},
        ]
        if bot_id:
            overwrites.append({"id": bot_id, "type": 1, "allow": "52224", "deny": "0"})  # view+send+embed

        r = await client.post(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers,
            json={
                "name": "angriff-alarm",
                "type": 0,
                "parent_id": category_id,
                "permission_overwrites": overwrites,
            },
        )
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/attacks?error=channel_{r.status_code}", status_code=303)
        channel_id = r.json()["id"]

        # Post the persistent button message
        button_payload = {
            "content": "## ⚔️ Angriff melden\nWenn du einen Angriff siehst: klicke den Button, öffne deinen **Truppenplatz** in Travian, markiere alles (`Strg+A`), kopiere (`Strg+C`) und füge es in das Eingabefeld ein.",
            "components": [{
                "type": 1,
                "components": [{
                    "type": 2,
                    "style": 4,
                    "label": "⚔️ Angriff melden",
                    "custom_id": "report_attack",
                }]
            }]
        }
        r = await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=headers,
            json=button_payload,
        )
        message_id = r.json().get("id", "") if r.status_code in (200, 201) else ""

    await database.set_attack_channel_web(guild_id, channel_id, message_id)
    return RedirectResponse(f"/guild/{guild_id}/attacks?saved=1", status_code=303)


@app.post("/guild/{guild_id}/attacks/reset")
async def attacks_reset(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    await database.set_attack_channel_web(guild_id, "", "")
    return RedirectResponse(f"/guild/{guild_id}/attacks?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Own Troops / Village Upload
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/attacks/own-troops", response_class=HTMLResponse)
async def own_troops_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err

    import json as _json
    own_villages = await database.get_own_villages(guild_id)
    # Parse troops_json back to dict for display
    CROP_MAP = {
        "Legionär": 1, "Prätorianer": 1, "Imperianer": 1,
        "Equites Legati": 2, "Equites Imperatoris": 3, "Equites Caesaris": 4,
        "Rammbock": 5, "Feuerkatapult": 6, "Senator": 5,
        "Keulenschwinger": 1, "Speerkämpfer": 1, "Axtkämpfer": 1,
        "Späher": 1, "Kundschafter": 1, "Paladin": 2, "Teut. Ritter": 3, "Teutonen Reiter": 3,
        "Häuptling": 4, "Stammesführer": 4, "Teutonen-Rammbock": 5, "Ramme": 5, "Kriegsmaschine": 6, "Katapult": 6,
        "Phalanx": 1, "Schwertkämpfer": 1, "Pathfinder": 2,
        "Theutates-Blitz": 2, "Druidentreiter": 2, "Haeduer": 3,
        "Stammesältester": 5, "Gallier-Rammbock": 5, "Gallier-Kata": 6,
        "Siedler": 1, "Held": 0,
    }
    for v in own_villages:
        try:
            v["troops"] = _json.loads(v.get("troops_json") or "{}")
        except Exception:
            v["troops"] = {}
        v["total_crop"] = sum(CROP_MAP.get(t, 1) * c for t, c in v["troops"].items())

    # Load recent attack reports for defense priority cross-reference
    attack_reports = await database.get_attack_reports(guild_id, limit=20)
    # Build a set of defender coords from recent reports
    import datetime as _dt
    def_targets = []
    for rpt in attack_reports:
        try:
            waves = _json.loads(rpt.get("attacks_json") or "[]")
        except Exception:
            waves = []
        for w in waves:
            dx, dy = w.get("def_x"), w.get("def_y")
            if dx is not None and dy is not None:
                def_targets.append({"x": int(dx), "y": int(dy), "report_id": rpt["id"],
                                     "created_at": rpt.get("created_at","")[:16],
                                     "arrival": w.get("arrival","")})

    # For each def target, find matching own village or nearest
    own_by_coords = {(v["x"], v["y"]): v for v in own_villages}
    priority_rows = []
    seen = set()
    for t in def_targets:
        key = (t["x"], t["y"])
        if key in seen:
            continue
        seen.add(key)
        match = own_by_coords.get(key)
        if match:
            priority_rows.append({**t, "own_village": match, "urgency": "red"})

    history = await database.get_own_villages_history(guild_id)

    return templates.TemplateResponse("own_troops.html", {
        "request": request,
        "guild": guild,
        "own_villages": own_villages,
        "priority_rows": priority_rows,
        "history": history,
        "upload_msg": None,
    })


@app.post("/guild/{guild_id}/attacks/own-troops")
async def own_troops_upload(
    request: Request,
    guild_id: str,
    troop_text: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err

    uploaded_by = session.get("username") or session.get("discord_username") or "unknown"
    parsed = parse_own_villages(troop_text)
    for v in parsed:
        vtype, off_s, def_s, prio = classify_own_village(v.get("troops", {}))
        v["village_type"] = vtype
        v["off_score"] = off_s
        v["def_score"] = def_s
        v["priority"] = prio

    if parsed:
        await database.save_own_villages(guild_id, parsed, uploaded_by)

    return RedirectResponse(f"/guild/{guild_id}/attacks/own-troops?uploaded={len(parsed)}", status_code=303)


@app.post("/guild/{guild_id}/attacks/own-troops/clear")
async def own_troops_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    err = _require_admin(session)
    if err: return err
    await database.delete_own_villages(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/attacks/own-troops", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Farming
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/farming", response_class=HTMLResponse)
async def farming_page(
    request: Request,
    guild_id: str,
    saved: str = "",
    min_days: int = 3,
    min_pop: int = 0,
    max_pop: int = 9999,
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    is_admin = session.get("guilds") is None

    farm_stats = await database.get_farm_stats(guild_id)
    inactive_farms = await database.get_inactive_farms(guild_id, min_days=min_days, min_pop=min_pop, max_pop=max_pop)
    farm_list = await database.get_farm_list(guild_id)
    cross_reference = await database.get_farming_cross_reference(guild_id, min_days=min_days)
    cross_ref_coords = {(r["x"], r["y"]) for r in cross_reference}

    return templates.TemplateResponse("farming.html", {
        "request": request,
        "guild": guild,
        "is_admin": is_admin,
        "saved": saved,
        "farm_stats": farm_stats,
        "inactive_farms": inactive_farms,
        "farm_list": farm_list,
        "cross_reference": cross_reference,
        "cross_ref_coords": cross_ref_coords,
        "min_days": min_days,
        "min_pop": min_pop,
        "max_pop": max_pop,
    })


@app.post("/guild/{guild_id}/farming/snapshot")
async def farming_snapshot(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    tw_world = (guild.get("tw_world") or "").strip()
    if not tw_world:
        return RedirectResponse(f"/guild/{guild_id}/farming?error=no_world", status_code=303)
    try:
        await _fetch_and_save_snapshot(guild_id, tw_world)
    except Exception as e:
        return RedirectResponse(f"/guild/{guild_id}/farming?error=fetch_failed", status_code=303)
    return RedirectResponse(f"/guild/{guild_id}/farming?saved=snapshot", status_code=303)


@app.post("/guild/{guild_id}/farming/farmlist/add")
async def farming_farmlist_add(
    request: Request,
    guild_id: str,
    x: int = Form(...),
    y: int = Form(...),
    village_name: str = Form(""),
    player_name: str = Form(""),
    population: str = Form(""),
    notes: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    pop_int = None
    if population.strip().isdigit():
        pop_int = int(population.strip())
    uid = session.get("uid", "unknown")
    uname = session.get("username", "unknown")
    await database.add_farm_list_entry(
        guild_id, uid, uname, x, y,
        village_name.strip() or None,
        player_name.strip() or None,
        pop_int,
        notes.strip() or None,
    )
    return RedirectResponse(f"/guild/{guild_id}/farming?saved=added", status_code=303)


@app.post("/guild/{guild_id}/farming/farmlist/delete/{entry_id}")
async def farming_farmlist_delete(request: Request, guild_id: str, entry_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_farm_list_entry(guild_id, entry_id)
    return RedirectResponse(f"/guild/{guild_id}/farming", status_code=303)


# ── Einsatzplanung ────────────────────────────────────────────────────────────

@app.get("/guild/{guild_id}/einsatz", response_class=HTMLResponse)
async def einsatz_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    plans = await database.get_attack_plans(guild_id)
    return templates.TemplateResponse("einsatz.html", {
        "request": request,
        "guild": guild,
        "plans": plans,
        "session": session,
        "saved": request.query_params.get("saved"),
    })


@app.post("/guild/{guild_id}/einsatz/create")
async def einsatz_create(
    request: Request,
    guild_id: str,
    plan_name: str = Form(...),
    target_x: int = Form(...),
    target_y: int = Form(...),
    target_name: str = Form(""),
    player_name: str = Form(""),
    arrival_time: str = Form(...),
    wave_type: str = Form("attack"),
    troop_speed: float = Form(6.0),
    notes: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if wave_type not in ("attack", "raid", "reinforce", "spy"):
        wave_type = "attack"
    if troop_speed <= 0 or troop_speed > 50:
        troop_speed = 6.0
    uid = session.get("uid", "unknown")
    uname = session.get("username", "unknown")
    await database.create_attack_plan(
        guild_id, uid, uname,
        plan_name.strip() or "Einsatz",
        target_x, target_y,
        target_name.strip() or None,
        player_name.strip() or None,
        arrival_time,
        wave_type,
        troop_speed,
        notes.strip() or None,
    )
    return RedirectResponse(f"/guild/{guild_id}/einsatz?saved=1", status_code=303)


@app.post("/guild/{guild_id}/einsatz/delete/{plan_id}")
async def einsatz_delete(request: Request, guild_id: str, plan_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_attack_plan(guild_id, plan_id)
    return RedirectResponse(f"/guild/{guild_id}/einsatz", status_code=303)


# ---------------------------------------------------------------------------
# Routes — admin panel
# ---------------------------------------------------------------------------

import json as _json_mod

def _require_admin(request: Request):
    """Returns (session, error_response). error_response set if not admin."""
    session = get_session(request)
    if not session:
        return None, RedirectResponse("/login", status_code=303)
    if session.get("type") != "admin":
        return None, RedirectResponse("/dashboard", status_code=303)
    return session, None


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    session, err = _require_admin(request)
    if err: return err
    guilds = await database.get_all_guilds()
    total = len(guilds)
    active = sum(1 for g in guilds if g.get("subscription_status") == "active")
    trialing = sum(1 for g in guilds if g.get("subscription_status") == "trialing")
    free = sum(1 for g in guilds if g.get("subscription_status") in (None, "free", ""))
    # MRR estimate based on plan
    plan_prices = {"starter": 6.99, "clan": 10.99, "alliance": 14.99, "imperium": 19.99}
    mrr = sum(
        plan_prices.get(g.get("subscription_plan") or "starter", 6.99)
        for g in guilds
        if g.get("subscription_status") in ("active", "trialing")
    )
    recent = await database.get_recent_guilds(10)
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "total": total,
        "active": active,
        "trialing": trialing,
        "free": free,
        "mrr": round(mrr, 2),
        "recent": recent,
        "session": session,
    })


@app.get("/admin/customers", response_class=HTMLResponse)
async def admin_customers(request: Request):
    session, err = _require_admin(request)
    if err: return err
    customers = await database.get_customers_overview()

    # Enrich with Stripe customer data (email, name) where we have a stripe_customer_id
    stripe_cache: dict[str, dict] = {}
    if STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY
        # Collect unique stripe_customer_ids across all customers + guilds
        cids: set[str] = set()
        for c in customers:
            cid = c["user_sub"].get("stripe_customer_id")
            if cid:
                cids.add(cid)
            for g in c["guilds"]:
                gcid = g.get("stripe_customer_id")
                if gcid:
                    cids.add(gcid)
        for cid in cids:
            try:
                sc = stripe.Customer.retrieve(cid)
                stripe_cache[cid] = {
                    "email": sc.get("email") or "",
                    "name": sc.get("name") or "",
                }
            except Exception:
                pass

    # Attach stripe info to each customer
    for c in customers:
        cid = c["user_sub"].get("stripe_customer_id")
        if not cid:
            # Fall back to first guild with a stripe_customer_id
            for g in c["guilds"]:
                if g.get("stripe_customer_id"):
                    cid = g["stripe_customer_id"]
                    break
        c["stripe_info"] = stripe_cache.get(cid, {}) if cid else {}

    total_active = sum(
        1 for c in customers
        if any(g.get("subscription_status") in ("active","trialing") for g in c["guilds"])
        or c["user_sub"].get("subscription_status") in ("active","trialing")
    )
    return templates.TemplateResponse("admin_customers.html", {
        "request": request,
        "customers": customers,
        "total_customers": len(customers),
        "total_active": total_active,
        "session": session,
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@app.post("/admin/customers/{guild_id}/set-plan")
async def admin_set_plan(
    request: Request,
    guild_id: str,
    status: str = Form(...),
    plan: str = Form(""),
):
    session, err = _require_admin(request)
    if err: return err
    if status not in ("free", "active", "trialing", "canceled", "past_due"):
        status = "free"
    if plan not in ("starter", "clan", "alliance", "imperium", ""):
        plan = ""
    await database.update_subscription_plan(guild_id, status, plan)
    return RedirectResponse("/admin/customers?saved=1", status_code=303)


@app.post("/admin/customers/user/{discord_user_id}/set-plan")
async def admin_set_user_plan(
    request: Request,
    discord_user_id: str,
    status: str = Form(...),
    plan: str = Form(""),
):
    session, err = _require_admin(request)
    if err: return err
    if status not in ("free", "active", "trialing", "canceled", "past_due"):
        status = "free"
    valid_plans = [f"{t}_{i}" for t in ("starter","clan","alliance","imperium") for i in ("monthly","annual")]
    valid_plans += ["starter","clan","alliance","imperium",""]
    if plan not in valid_plans:
        plan = ""
    await database.update_user_subscription_admin(discord_user_id, status, plan)
    return RedirectResponse("/admin/customers?saved=1", status_code=303)


@app.get("/admin/promos", response_class=HTMLResponse)
async def admin_promos(request: Request):
    session, err = _require_admin(request)
    if err: return err
    coupons = []
    error = ""
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        coupon_list = stripe.Coupon.list(limit=20)
        for c in coupon_list.data:
            promos = stripe.PromotionCode.list(coupon=c.id, limit=10)
            coupons.append({
                "id": c.id,
                "name": c.name or c.id,
                "percent_off": c.percent_off,
                "duration": c.duration,
                "valid": c.valid,
                "promo_codes": [{"id": p.id, "code": p.code, "active": p.active} for p in promos.data],
            })
    except Exception as exc:
        error = str(exc)
    return templates.TemplateResponse("admin_promos.html", {
        "request": request,
        "coupons": coupons,
        "error": error,
        "session": session,
    })


@app.post("/admin/promos/create")
async def admin_promos_create(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    percent_off: int = Form(...),
    duration: str = Form("once"),
):
    session, err = _require_admin(request)
    if err: return err
    if not 5 <= percent_off <= 100:
        return RedirectResponse("/admin/promos?error=percent_off+must+be+5-100", status_code=303)
    if duration not in ("once", "forever", "repeating"):
        duration = "once"
    import re as _re
    clean_code = _re.sub(r'[^a-zA-Z0-9_-]', '', code.strip().upper())
    if not clean_code:
        return RedirectResponse("/admin/promos?error=Invalid+promo+code+format", status_code=303)
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        coupon = stripe.Coupon.create(percent_off=percent_off, duration=duration, name=name.strip())
        stripe.PromotionCode.create(coupon=coupon.id, code=clean_code)
    except Exception as exc:
        return RedirectResponse(f"/admin/promos?error={exc}", status_code=303)
    return RedirectResponse("/admin/promos?saved=1", status_code=303)


@app.post("/admin/promos/delete")
async def admin_promos_delete(
    request: Request,
    coupon_id: str = Form(""),
    promo_id: str = Form(""),
):
    session, err = _require_admin(request)
    if err: return err
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        if promo_id:
            stripe.PromotionCode.modify(promo_id, active=False)
        if coupon_id and not promo_id:
            stripe.Coupon.delete(coupon_id)
    except Exception as exc:
        return RedirectResponse(f"/admin/promos?error={str(exc)[:80].replace(' ', '+')}", status_code=303)
    return RedirectResponse("/admin/promos?saved=1", status_code=303)


@app.get("/admin/popup", response_class=HTMLResponse)
async def admin_popup(request: Request):
    session, err = _require_admin(request)
    if err: return err
    raw = await database.get_setting("popup_config")
    config = _json_mod.loads(raw) if raw else {
        "enabled": False,
        "title": "🎉 Angebot",
        "body": "Upgrade auf Pro und spare 20%!",
        "button_text": "Jetzt upgraden",
        "button_url": "",
        "bg_color": "#1a1a2e",
        "version": 0,
    }
    return templates.TemplateResponse("admin_popup.html", {
        "request": request,
        "config": config,
        "session": session,
    })


@app.post("/admin/popup/save")
async def admin_popup_save(
    request: Request,
    title: str = Form(""),
    body: str = Form(""),
    button_text: str = Form(""),
    button_url: str = Form(""),
    bg_color: str = Form("#1a1a2e"),
    enabled: str = Form("off"),
):
    session, err = _require_admin(request)
    if err: return err
    import time as _time
    config = {
        "enabled": enabled == "on",
        "title": title.strip()[:200],
        "body": body.strip()[:1000],
        "button_text": button_text.strip()[:100],
        "button_url": button_url.strip()[:500],
        "bg_color": bg_color.strip()[:20] if bg_color.strip().startswith("#") else "#1a1a2e",
        "version": int(_time.time()),
    }
    await database.set_setting("popup_config", _json_mod.dumps(config))
    return RedirectResponse("/admin/popup?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# API — cookie consent
# ---------------------------------------------------------------------------

from pydantic import BaseModel

class CookieConsentBody(BaseModel):
    action: str

@app.post("/api/cookie-consent")
async def api_cookie_consent(request: Request, body: CookieConsentBody):
    session = get_session(request)
    user_id = session.get("uid") if session else None
    username = session.get("username") if session else None
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    action = body.action if body.action in ("accepted", "withdrawn") else "accepted"
    await database.log_cookie_consent(user_id, username, action, ip, ua)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin — cookie consents
# ---------------------------------------------------------------------------

@app.get("/admin/consents", response_class=HTMLResponse)
async def admin_consents(request: Request):
    session, err = _require_admin(request)
    if err: return err
    consents = await database.get_cookie_consents(200)
    return templates.TemplateResponse("admin_consents.html", {
        "request": request,
        "consents": consents,
        "session": session,
    })


# ---------------------------------------------------------------------------
# Admin — stats / live users / funnel
# ---------------------------------------------------------------------------

@app.get("/admin/stats", response_class=HTMLResponse)
async def admin_stats(request: Request):
    session, err = _require_admin(request)
    if err: return err
    now = time.time()
    live_users = []
    for entry in _active_users.values():
        live_users.append({
            "username": entry.get("username") or "—",
            "path": entry.get("path") or "—",
            "ip": entry.get("ip") or "—",
            "seconds_ago": int(now - entry.get("last_seen", now)),
        })
    live_users.sort(key=lambda x: x["seconds_ago"])
    funnel = await database.get_funnel_stats()
    no_sub = await database.get_billing_visitors_without_sub()
    return templates.TemplateResponse("admin_stats.html", {
        "request": request,
        "live_users": live_users,
        "funnel": funnel,
        "no_sub": no_sub,
        "session": session,
    })


@app.get("/api/live-users")
async def api_live_users(request: Request):
    session, err = _require_admin(request)
    if err: return {"users": []}
    now = time.time()
    users = []
    for entry in _active_users.values():
        users.append({
            "username": entry.get("username") or "—",
            "path": entry.get("path") or "—",
            "ip": entry.get("ip") or "—",
            "seconds_ago": int(now - entry.get("last_seen", now)),
        })
    users.sort(key=lambda x: x["seconds_ago"])
    return {"users": users}


# ---------------------------------------------------------------------------
# Admin — contact page editor
# ---------------------------------------------------------------------------

@app.get("/admin/contact", response_class=HTMLResponse)
async def admin_contact(request: Request):
    session, err = _require_admin(request)
    if err: return err
    raw = await database.get_setting("contact_config")
    config = _json_mod.loads(raw) if raw else {
        "email": "",
        "discord_invite": "",
        "response_time": "24 Stunden",
        "support_hours": "Mo–Fr 9–18 Uhr",
        "extra_text": "",
    }
    return templates.TemplateResponse("admin_contact.html", {
        "request": request,
        "config": config,
        "session": session,
    })


@app.get("/admin/auths", response_class=HTMLResponse)
async def admin_auths(request: Request):
    session, err = _require_admin(request)
    if err: return err
    logs = await database.get_auth_logs(limit=300)
    stats = await database.get_auth_stats()
    return templates.TemplateResponse("admin_auths.html", {
        "request": request,
        "logs": logs,
        "stats": stats,
        "session": session,
    })


@app.post("/admin/contact/save")
async def admin_contact_save(
    request: Request,
    email: str = Form(""),
    discord_invite: str = Form(""),
    response_time: str = Form(""),
    support_hours: str = Form(""),
    extra_text: str = Form(""),
):
    session, err = _require_admin(request)
    if err: return err
    config = {
        "email": email.strip()[:200],
        "discord_invite": discord_invite.strip()[:500],
        "response_time": response_time.strip()[:200],
        "support_hours": support_hours.strip()[:200],
        "extra_text": extra_text.strip()[:2000],
    }
    await database.set_setting("contact_config", _json_mod.dumps(config))
    return RedirectResponse("/admin/contact?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Public — contact page
# ---------------------------------------------------------------------------

@app.get("/kontakt", response_class=HTMLResponse)
async def kontakt(request: Request):
    raw = await database.get_setting("contact_config")
    config = _json_mod.loads(raw) if raw else {}
    return templates.TemplateResponse("kontakt.html", {
        "request": request,
        "config": config,
    })


# ---------------------------------------------------------------------------
# API — popup config (public, for JS fetch)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Legal pages
# ---------------------------------------------------------------------------

import datetime as _dt

_IMPRESSUM = {
    "name":        os.environ.get("IMPRESSUM_NAME", "Maximilian Frischholz"),
    "street":      os.environ.get("IMPRESSUM_STREET", "Musterstraße 1"),
    "city":        os.environ.get("IMPRESSUM_CITY", "12345 Musterstadt"),
    "country":     os.environ.get("IMPRESSUM_COUNTRY", "Deutschland"),
    "email":       os.environ.get("IMPRESSUM_EMAIL", "kontakt@travops.online"),
    "phone":       os.environ.get("IMPRESSUM_PHONE", ""),
    "website":     os.environ.get("IMPRESSUM_WEBSITE", "https://travops.online"),
    "ust_id":      os.environ.get("IMPRESSUM_UST_ID", ""),
    "responsible": os.environ.get("IMPRESSUM_RESPONSIBLE", "Maximilian Frischholz, Anschrift wie oben"),
    "updated":     os.environ.get("IMPRESSUM_UPDATED", "Mai 2026"),
}

templates.env.globals["impressum"] = _IMPRESSUM

def _legal_ctx(request: Request) -> dict:
    return {
        "request": request,
        "impressum": _IMPRESSUM,
        "current_year": _dt.datetime.utcnow().year,
    }


@app.get("/impressum", response_class=HTMLResponse)
async def page_impressum(request: Request):
    return templates.TemplateResponse("impressum.html", _legal_ctx(request))


@app.get("/datenschutz", response_class=HTMLResponse)
async def page_datenschutz(request: Request):
    return templates.TemplateResponse("datenschutz.html", _legal_ctx(request))


@app.get("/agb", response_class=HTMLResponse)
async def page_agb(request: Request):
    return templates.TemplateResponse("agb.html", _legal_ctx(request))


@app.get("/cookies", response_class=HTMLResponse)
async def page_cookies(request: Request):
    return templates.TemplateResponse("cookies.html", _legal_ctx(request))


# ---------------------------------------------------------------------------
# API — popup config (public, for JS fetch)
# ---------------------------------------------------------------------------

@app.get("/api/me")
async def api_me(request: Request):
    session = get_session(request)
    if not session:
        return JSONResponse({"logged_in": False})
    return JSONResponse({
        "logged_in": True,
        "type": session.get("type"),
        "uid": session.get("uid"),
        "username": session.get("username"),
        "is_admin": session.get("type") == "admin",
        "admin_ids_loaded": list(ADMIN_DISCORD_IDS),
    })


@app.get("/api/my-alerts")
async def api_my_alerts(request: Request):
    """Returns payment alerts for the logged-in user (used by nav profile dropdown)."""
    session = get_session(request)
    if not session:
        return JSONResponse({"past_due": []})
    all_guilds = await database.get_all_guilds()
    if session["guilds"] is not None:
        allowed = set(session["guilds"])
        guilds = [g for g in all_guilds if g["guild_id"] in allowed]
    else:
        guilds = all_guilds
    past_due = [
        {"guild_id": g["guild_id"], "guild_name": g["guild_name"]}
        for g in guilds
        if g.get("subscription_status") == "past_due"
    ]
    return JSONResponse({"past_due": past_due})


@app.get("/api/popup-config")
async def api_popup_config():
    raw = await database.get_setting("popup_config")
    if not raw:
        return JSONResponse({"enabled": False})
    try:
        data = _json_mod.loads(raw)
    except Exception:
        return JSONResponse({"enabled": False})
    return JSONResponse(data)
