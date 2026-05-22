import os
import asyncio
import base64
import re
import secrets
import smtplib
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from email.mime.text import MIMEText
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

# Reverse map: price_id → tier name (for webhook tier detection)
PRICE_TO_TIER: dict[str, str] = {
    price_id: tier
    for tier, intervals in STRIPE_PRICES.items()
    for price_id in intervals.values()
    if price_id
}

# ---------------------------------------------------------------------------
# E-Mail notifications
# ---------------------------------------------------------------------------
_NOTIFY_TO   = "support@travops.online"
_SMTP_HOST   = os.environ.get("SMTP_HOST", "")
_SMTP_PORT   = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER   = os.environ.get("SMTP_USER", "")
_SMTP_PASS   = os.environ.get("SMTP_PASS", "")
_SMTP_FROM   = os.environ.get("SMTP_FROM", _SMTP_USER)


def _send_email(subject: str, body: str) -> None:
    """Send a plain-text notification e-mail (fire-and-forget, runs in thread)."""
    if not _SMTP_HOST or not _SMTP_USER or not _SMTP_PASS:
        print(f"[mail] SMTP not configured — skipping: {subject}", flush=True)
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = _SMTP_FROM
        msg["To"]      = _NOTIFY_TO
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.login(_SMTP_USER, _SMTP_PASS)
            s.sendmail(_SMTP_FROM, [_NOTIFY_TO], msg.as_string())
        print(f"[mail] sent: {subject}", flush=True)
    except Exception as exc:
        print(f"[mail] error sending '{subject}': {exc}", flush=True)


async def _notify(subject: str, body: str) -> None:
    """Async wrapper — runs SMTP in a thread so it never blocks the event loop."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_email, subject, body)


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

def _parse_sql_tuple(s: str) -> list[str]:
    """Parse a SQL VALUES tuple string into a list of tokens, respecting quoted strings."""
    vals: list[str] = []
    i = 0
    L = len(s)
    while i < L:
        # skip whitespace
        while i < L and s[i] in (' ', '\t'):
            i += 1
        if i >= L:
            break
        if s[i] == "'":
            # quoted string — find closing quote ('' is escaped quote)
            i += 1
            buf = []
            while i < L:
                if s[i] == "'" and i + 1 < L and s[i + 1] == "'":
                    buf.append("'"); i += 2
                elif s[i] == "'":
                    break
                else:
                    buf.append(s[i]); i += 1
            vals.append("".join(buf))
            i += 1  # skip closing quote
        else:
            # unquoted token (number, NULL, …)
            j = i
            while j < L and s[j] != ',':
                j += 1
            vals.append(s[i:j].strip())
            i = j
        # skip comma separator
        while i < L and s[i] in (' ', '\t'):
            i += 1
        if i < L and s[i] == ',':
            i += 1
    return vals


_VALUES_RE = re.compile(r"VALUES\s*\((.+)\);?\s*$", re.IGNORECASE)


def _parse_map_sql(content: str) -> list[dict]:
    """Parse Travian map.sql into a list of village dicts.

    Travian map.sql has two known column layouts (same as map.html parseSql):

    Format X-first  (|v[0]| <= 800):
        x, y, ?, tribe, vid, vname, pop, pname, pid, aname, aid, ?, isCapital
        idx:  0  1  2    3    4      5    6    7      8     9    10  11   12

    Format VID-first (|v[0]| > 800):
        vid, x, y, ?, tribe, ?, vname, pop, pname, ?, aname, ?, ?, isCapital
        idx:   0  1  2  3    4   5      6    7     8   9    10  11  12   13
    """
    villages = []
    for raw_line in content.splitlines():
        m = _VALUES_RE.search(raw_line)
        if not m:
            continue
        v = _parse_sql_tuple(m.group(1))
        if len(v) < 7:
            continue
        try:
            f0 = float(v[0]) if v[0] not in ('NULL', '') else 0
            if abs(f0) <= 800:
                # x-first format
                x     = int(float(v[0]))
                y     = int(float(v[1]))
                tribe = int(float(v[3])) if len(v) > 3 and v[3] not in ('NULL','') else 0
                vname = v[5] if len(v) > 5 else ""
                pop   = int(float(v[6])) if len(v) > 6 and v[6] not in ('NULL','') else 0
                pname = v[7] if len(v) > 7 and v[7] not in ('NULL', '') else ""
                aname = v[9] if len(v) > 9 and v[9] not in ('NULL', '') else ""
                vid   = v[4] if len(v) > 4 else ""
                pid   = v[8] if len(v) > 8 else ""
                aid   = v[10] if len(v) > 10 else ""
            else:
                # vid-first format
                vid   = v[0]
                x     = int(float(v[1]))
                y     = int(float(v[2]))
                tribe = int(float(v[4])) if len(v) > 4 and v[4] not in ('NULL','') else 0
                vname = v[6] if len(v) > 6 else ""
                pop   = int(float(v[7])) if len(v) > 7 and v[7] not in ('NULL','') else 0
                pname = v[8] if len(v) > 8 and v[8] not in ('NULL', '') else ""
                aname = v[10] if len(v) > 10 and v[10] not in ('NULL', '') else ""
                pid   = v[8] if len(v) > 8 else ""
                aid   = v[9] if len(v) > 9 else ""
        except (ValueError, IndexError):
            continue
        if not (-800 <= x <= 800 and -800 <= y <= 800):
            continue
        villages.append({
            "village_id": vid,
            "village_name": vname,
            "x": x, "y": y,
            "player_id": pid,
            "player_name": pname,
            "alliance_id": aid,
            "alliance_name": aname,
            "population": pop,
            "tribe": tribe,
        })
    return villages


async def _fetch_and_save_snapshot(guild_id: str, tw_world: str):
    url = tw_world.rstrip("/") + "/map.sql"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        r.raise_for_status()
    villages = _parse_map_sql(r.text)
    if villages:
        await database.save_map_snapshot(guild_id, villages)
        # Auto-sync alliance members if alliance name is configured
        try:
            await database.sync_alliance_members_from_snapshot(guild_id)
        except Exception:
            pass


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

    # Notify on first-ever login
    if not _is_returning:
        asyncio.create_task(_notify(
            subject=f"🆕 Neuer Nutzer: {username}",
            body=(
                f"Ein neuer Nutzer hat sich zum ersten Mal bei TravOps angemeldet.\n\n"
                f"Discord-Name : {username}\n"
                f"Discord-ID   : {discord_id}\n"
                f"Server-Anzahl: {len(user_guilds)} (davon {len(accessible)} zugänglich)\n"
            ),
        ))

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
        await client.delete(
            f"https://discord.com/api/v10/channels/{channel_id}",
            headers={"Authorization": f"Bot {token}"},
        )
    # Always remove from DB, even if the channel was already deleted in Discord
    await database.delete_scout_channel(channel_id)


@app.post("/guild/{guild_id}/scout-channels/{channel_id}/close")
async def scout_channel_close(request: Request, guild_id: str, channel_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if not SNOWFLAKE_RE.match(channel_id):
        return RedirectResponse(f"/guild/{guild_id}", status_code=303)
    ch = await database.get_scout_channel_info(channel_id)
    if not ch or ch.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}", status_code=303)
    token = os.environ.get("DISCORD_TOKEN", "")
    DELAY = 120
    # Check if the channel still exists in Discord
    async with httpx.AsyncClient() as client:
        probe = await client.get(
            f"https://discord.com/api/v10/channels/{channel_id}",
            headers={"Authorization": f"Bot {token}"},
        )
    if probe.status_code == 404:
        # Channel already gone in Discord — remove from DB immediately
        await database.delete_scout_channel(channel_id)
        return RedirectResponse(f"/guild/{guild_id}/scout", status_code=303)
    # Channel still exists — send notice and schedule deletion
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"content": f"🔒 Scout channel closed via dashboard. Channel will be deleted in {DELAY // 60} minutes."},
        )
    asyncio.create_task(_close_scout_channel_after_delay(channel_id, token, delay=DELAY))
    # Return JSON so the frontend can start a countdown
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": True, "delete_in": DELAY})
    return RedirectResponse(f"/guild/{guild_id}/scout?saved=1", status_code=303)


@app.get("/guild/{guild_id}/scout-channels/{channel_id}/status")
async def scout_channel_status(request: Request, guild_id: str, channel_id: str):
    """Poll endpoint: returns whether channel still exists in our DB."""
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    ch = await database.get_scout_channel_info(channel_id)
    return JSONResponse({"exists": ch is not None})


@app.get("/guild/{guild_id}/scout-channels/{channel_id}")
async def scout_channel_detail(request: Request, guild_id: str, channel_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    ch = await database.get_scout_channel_info(channel_id)
    if not ch or ch.get("guild_id") != guild_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    reports = await database.get_scout_reports_for_channel(channel_id)
    # Always return JSON (used by the slide-over panel)
    return JSONResponse({"ch": dict(ch), "reports": reports})


@app.get("/guild/{guild_id}/scout/stats", response_class=HTMLResponse)
async def scout_stats_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    stats = await database.get_scout_stats(guild_id)
    return templates.TemplateResponse(
        "scout_stats.html",
        {"request": request, "guild": guild, "stats": stats},
    )


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


@app.get("/guild/{guild_id}/map/heatmap-data")
async def guild_map_heatmap_data(request: Request, guild_id: str):
    """Return farmlist resource data for heatmap overlay."""
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=403)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    uid = session.get("uid", "")
    data = await database.get_farmlist_heatmap(guild_id, uid)
    return JSONResponse({"data": data})


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
        "is_owner": is_guild_owner(session, guild),
        "stripe_pk": STRIPE_PUBLISHABLE_KEY,
        "stripe_configured": stripe_configured,
        "tier_meta": TIER_META,
        "saved": request.query_params.get("saved"),
        "cancelled": request.query_params.get("cancelled"),
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
        interval = "annual" if sub["items"]["data"][0]["price"]["recurring"]["interval"] == "year" else "monthly"
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


@app.post("/guild/{guild_id}/billing/cancel")
async def billing_cancel(request: Request, guild_id: str):
    """Cancel the guild subscription at period end."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=not_found", status_code=303)
    if not is_guild_owner(session, guild):
        return RedirectResponse(f"/guild/{guild_id}/billing?error=only_owner", status_code=303)
    sub_id = guild.get("stripe_subscription_id")
    if not sub_id:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=no_subscription", status_code=303)
    s = _stripe_client()
    if not s:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=stripe_error", status_code=303)
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
        await database.update_subscription(
            guild_id=guild_id,
            stripe_customer_id=guild.get("stripe_customer_id", ""),
            stripe_subscription_id=sub_id,
            status="cancelled",
            plan=guild.get("subscription_plan", ""),
            expires_at=guild.get("subscription_expires_at"),
        )
    except Exception as e:
        print(f"[billing/cancel] Error: {e}", flush=True)
        return RedirectResponse(f"/guild/{guild_id}/billing?error=cancel_failed", status_code=303)
    return RedirectResponse(f"/guild/{guild_id}/billing?cancelled=1", status_code=303)


@app.post("/plans/cancel")
async def plans_cancel(request: Request):
    """Cancel the user-level subscription at period end."""
    session, err = _require_session(request)
    if err: return err
    discord_user_id = session.get("uid", "")
    user_sub = await database.get_user_subscription(discord_user_id)
    if not user_sub:
        return RedirectResponse("/plans?error=no_subscription", status_code=303)
    sub_id = user_sub.get("stripe_subscription_id")
    if not sub_id:
        return RedirectResponse("/plans?error=no_subscription", status_code=303)
    s = _stripe_client()
    if not s:
        return RedirectResponse("/plans?error=stripe_error", status_code=303)
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
        await database.upsert_user_subscription(
            discord_user_id=discord_user_id,
            stripe_customer_id=user_sub.get("stripe_customer_id", ""),
            stripe_subscription_id=sub_id,
            status="cancelled",
            plan=user_sub.get("plan", ""),
            expires_at=user_sub.get("expires_at"),
        )
    except Exception as e:
        print(f"[plans/cancel] Error: {e}", flush=True)
        return RedirectResponse("/plans?error=cancel_failed", status_code=303)
    return RedirectResponse("/plans?cancelled=1", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Plans (subscribe without a server)
# ---------------------------------------------------------------------------

@app.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request, error: str = "", cancelled: str = ""):
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
        "cancelled": cancelled,
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
            interval = "annual" if sub["items"]["data"][0]["price"]["recurring"]["interval"] == "year" else "monthly"
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

        # Extract price info from subscription object
        items_data = obj.get("items", {}).get("data", [{}])
        price_obj  = items_data[0].get("price", {}) if items_data else {}
        price_id   = price_obj.get("id", "")
        interval   = price_obj.get("recurring", {}).get("interval", "month")
        plan_interval = "annual" if interval == "year" else "monthly"

        # Resolve tier from price_id (covers upgrades/downgrades via Stripe portal)
        tier_from_price = PRICE_TO_TIER.get(price_id)

        status     = obj.get("status", "inactive")
        expires_at = datetime.datetime.utcfromtimestamp(obj["current_period_end"]).isoformat()

        # Check if this is a user-level subscription
        user_sub = await database.get_user_by_stripe_customer(customer_id)
        if user_sub:
            # Use price_id → tier if available, else keep existing tier
            existing_tier = (user_sub.get("plan") or "starter").split("_")[0]
            tier = tier_from_price or existing_tier
            plan_str = f"{tier}_{plan_interval}"
            await database.upsert_user_subscription(
                discord_user_id=user_sub["discord_user_id"],
                stripe_customer_id=customer_id,
                stripe_subscription_id=obj["id"],
                status=status,
                plan=plan_str,
                expires_at=expires_at,
            )
            print(f"[webhook] user sub updated: {user_sub['discord_user_id']} → {plan_str} ({status})", flush=True)
        else:
            guild = await database.get_guild_by_stripe_customer(customer_id)
            if guild:
                # For guild subscriptions: tier_from_price or existing guild plan
                existing_guild_tier = (guild.get("subscription_plan") or "starter").split("_")[0]
                tier = tier_from_price or existing_guild_tier
                plan_str = f"{tier}_{plan_interval}"
                expires_at_val = datetime.datetime.utcfromtimestamp(obj["current_period_end"]).isoformat()
                await database.update_subscription(
                    guild_id=guild["guild_id"],
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=obj["id"],
                    status=status,
                    plan=plan_str,
                    expires_at=expires_at_val,
                )
                print(f"[webhook] guild sub updated: {guild['guild_id']} → {plan_str} ({status})", flush=True)

    elif event["type"] == "checkout.session.completed":
        # Handle user-level checkout completions (source=plans)
        meta = obj.get("metadata") or {}
        if meta.get("source") == "plans" and meta.get("discord_user_id"):
            discord_user_id = meta["discord_user_id"]
            tier = meta.get("tier", "starter")
            import datetime
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
                        price_id = sub["items"]["data"][0]["price"]["id"]
                        interval = sub["items"]["data"][0]["price"]["recurring"]["interval"]
                        # Prefer price_id→tier over metadata tier (single source of truth)
                        resolved_tier = PRICE_TO_TIER.get(price_id, tier)
                        plan_str = f"{resolved_tier}_{'annual' if interval == 'year' else 'monthly'}"
                        expires_at = datetime.datetime.utcfromtimestamp(sub.current_period_end).isoformat()
                    except Exception as e:
                        print(f"[webhook] checkout sub retrieve error: {e}", flush=True)
            await database.upsert_user_subscription(
                discord_user_id=discord_user_id,
                stripe_customer_id=obj.get("customer", ""),
                stripe_subscription_id=sub_id or "",
                status=status,
                plan=plan_str,
                expires_at=expires_at,
            )
            print(f"[webhook] checkout.completed: {discord_user_id} → {plan_str} ({status})", flush=True)
            # Lookup cached username for notification
            _uname = (await database.get_user_subscription(discord_user_id) or {}).get("discord_username", discord_user_id)
            asyncio.create_task(_notify(
                subject=f"💳 Neuer Käufer: {_uname} – {plan_str}",
                body=(
                    f"Ein Nutzer hat ein TravOps-Abonnement abgeschlossen.\n\n"
                    f"Discord-Name : {_uname}\n"
                    f"Discord-ID   : {discord_user_id}\n"
                    f"Plan         : {plan_str}\n"
                    f"Status       : {status}\n"
                    f"Stripe-Kunde : {obj.get('customer', '—')}\n"
                ),
            ))
        # Handle guild-level checkout completions
        elif meta.get("guild_id") and meta.get("owner_discord_id"):
            guild_id = meta["guild_id"]
            tier = meta.get("tier", "starter")
            import datetime
            sub_id = obj.get("subscription")
            if sub_id:
                s = _stripe_client()
                if s:
                    try:
                        sub = s.subscriptions.retrieve(sub_id)
                        price_id = sub["items"]["data"][0]["price"]["id"]
                        interval = sub["items"]["data"][0]["price"]["recurring"]["interval"]
                        resolved_tier = PRICE_TO_TIER.get(price_id, tier)
                        plan_str = f"{resolved_tier}_{'annual' if interval == 'year' else 'monthly'}"
                        expires_at = datetime.datetime.utcfromtimestamp(sub.current_period_end).isoformat()
                        await database.update_subscription(
                            guild_id=guild_id,
                            stripe_customer_id=obj.get("customer", ""),
                            stripe_subscription_id=sub_id,
                            status=sub.status,
                            plan=plan_str,
                            expires_at=expires_at,
                            owner_discord_id=meta.get("owner_discord_id"),
                        )
                        print(f"[webhook] guild checkout.completed: {guild_id} → {plan_str}", flush=True)
                        _owner = meta.get("owner_discord_id", "—")
                        asyncio.create_task(_notify(
                            subject=f"💳 Neuer Guild-Käufer: {guild_id} – {plan_str}",
                            body=(
                                f"Ein Server-Besitzer hat ein TravOps Guild-Abo abgeschlossen.\n\n"
                                f"Guild-ID     : {guild_id}\n"
                                f"Owner-ID     : {_owner}\n"
                                f"Plan         : {plan_str}\n"
                                f"Status       : {sub.status}\n"
                                f"Stripe-Kunde : {obj.get('customer', '—')}\n"
                            ),
                        ))
                    except Exception as e:
                        print(f"[webhook] guild checkout sub error: {e}", flush=True)

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
    Parse Travian village/troop overview copy-paste (Strg+A / Strg+C).

    Supports two formats:
    1. Troops overview: header "Dorfname\tTroop1\tTroop2..." with counts per column
    2. Village overview: header "Village\tAttacks\tBuilding\tTroops\tMerchants"
       with troops as "Nx TroopName Nx TroopName …" in the Troops column

    Coordinates are extracted from the sidebar section.
    """
    import re
    text = re.sub(r'[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]', '', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    TROOP_ALIASES = {
        "Theutates Blitz":     "Theutates-Blitz",
        "Theutates-Blitz":     "Theutates-Blitz",
        "Theutates Thunder":   "Theutates-Blitz",
        "Druidenreiter":       "Druidentreiter",
        "Haeduan":             "Haeduer",
        "Haeduaner":           "Haeduer",
        "Rammholz":            "Gallier-Rammbock",
        "Trebuchet":           "Gallier-Kata",
        "Kriegskatapult":      "Gallier-Kata",
        "Kundschafter":        "Späher",
        "Scout":               "Späher",
        "Pathfinder":          "Pathfinder",
        "Teutonen Reiter":     "Teut. Ritter",
        "Teutonic Knight":     "Teut. Ritter",
        "Ramme":               "Teutonen-Rammbock",
        "Battering Ram":       "Teutonen-Rammbock",
        "Katapult":            "Kriegsmaschine",
        "Catapult":            "Kriegsmaschine",
        "Stammesführer":       "Häuptling",
        "Chief":               "Häuptling",
        "Chieftain":           "Häuptling",
        "Legionnaire":         "Legionär",
        "Praetorian":          "Prätorianer",
        "Imperian":            "Imperianer",
        "Equites Legati":      "Equites Legati",
        "Equites Imperatoris": "Equites Imperatoris",
        "Equites Caesaris":    "Equites Caesaris",
        "Ram":                 "Rammbock",
        "Fire Catapult":       "Feuerkatapult",
        "Senator":             "Senator",
        "Phalanx":             "Phalanx",
        "Swordsman":           "Schwertkämpfer",
        "Druidrider":          "Druidentreiter",
        "Clubswinger":         "Keulenschwinger",
        "Spearman":            "Speerkämpfer",
        "Axeman":              "Axtkämpfer",
        "Paladin":             "Paladin",
        "Settler":             "Siedler",
        "Hero":                "Held",
    }

    def normalize(name: str) -> str:
        return TROOP_ALIASES.get(name.strip(), name.strip())

    lines = text.split('\n')
    coord_re = re.compile(r'\((-?\d+)\|(-?\d+)\)')

    # ── Step 1: Detect format and find header row ─────────────────────────
    header_idx           = None
    col_names            = []
    troops_col           = None
    fmt_village_overview = False

    for i, line in enumerate(lines):
        parts = [p.strip() for p in line.split('\t')]
        if not parts or not parts[0]:
            continue
        first = parts[0].lower()

        # Format 1a: German troops overview ("Dorfname" header)
        # Format 1b: English troops overview ("Village" header with troop-name cols)
        is_troop_col_header = (
            (first == 'dorfname' and len(parts) >= 3) or
            (first == 'village' and len(parts) >= 3 and not any(
                p.strip().lower() in ('attacks', 'angriffe', 'troops', 'truppen',
                                      'building', 'gebäude', 'merchants', 'händler')
                for p in parts[1:]))
        )
        if is_troop_col_header:
            header_idx = i
            col_names  = [normalize(p) for p in parts[1:]]
            is_gaul = any(n in col_names for n in (
                "Phalanx", "Theutates-Blitz", "Gallier-Rammbock",
                "Druidentreiter", "Haeduer", "Gallier-Kata"))
            if is_gaul:
                col_names = ["Pathfinder" if n == "Späher" else n for n in col_names]
            break

        if first in ('village', 'dorf') and any(
                p.strip().lower() in ('troops', 'truppen') for p in parts):
            header_idx           = i
            fmt_village_overview = True
            header_lower         = [p.strip().lower() for p in parts]
            troops_col           = next(
                (j for j, h in enumerate(header_lower) if h in ('troops', 'truppen')), None)
            break

    # ── Step 2: Parse village rows ────────────────────────────────────────
    table_villages: dict = {}
    stop_words = {'summe', 'sum', 'total', 'gesamt', 'task overview', 'homepage'}

    if header_idx is not None and not fmt_village_overview:
        for line in lines[header_idx + 1:]:
            parts = [p.strip() for p in line.split('\t')]
            if not parts or not parts[0]:
                continue
            vname = parts[0]
            if vname.lower() in stop_words:
                break
            nums = []
            for n in parts[1:]:
                nc = re.sub(r'[\s.,]', '', n)
                nums.append(int(nc) if re.match(r'^\d+$', nc) else None)
            if not any(isinstance(n, int) for n in nums):
                continue
            table_villages[vname] = {
                col_names[k]: n for k, n in enumerate(nums)
                if k < len(col_names) and isinstance(n, int) and n > 0
            }

    elif header_idx is not None and fmt_village_overview and troops_col is not None:
        troop_re = re.compile(
            r'(\d+)\s*x\s+([A-Za-z\u00c0-\u024f][A-Za-z\u00c0-\u024f\s\-\.]+?)' +
            r'(?=\s+\d+\s*x|\s*$)'
        )
        for line in lines[header_idx + 1:]:
            parts = [p.strip() for p in line.split('\t')]
            if not parts or not parts[0]:
                continue
            vname = parts[0]
            if vname.lower() in stop_words or len(parts) < 2:
                break
            troops_str = parts[troops_col] if troops_col < len(parts) else ""
            troops: dict = {}
            for m in troop_re.finditer(troops_str):
                count = int(m.group(1))
                tname = normalize(m.group(2))
                if count > 0:
                    troops[tname] = troops.get(tname, 0) + count
            table_villages[vname] = troops

    # ── Step 3: Parse sidebar for village coordinates ─────────────────────
    sidebar_coords: dict = {}
    for idx, line in enumerate(lines):
        line_s = line.strip()
        cm = coord_re.match(line_s)
        if cm:
            x, y = int(cm.group(1)), int(cm.group(2))
            if idx > 0:
                prev = lines[idx - 1].strip()
                if prev and not coord_re.search(prev) and '\t' not in prev:
                    sidebar_coords[prev] = (x, y)
        elif '\t' in line_s:
            for m in re.finditer(r'^([^\t]+)\t\((-?\d+)\|(-?\d+)\)', line_s):
                sidebar_coords[m.group(1).strip()] = (int(m.group(2)), int(m.group(3)))

    # ── Step 4: Merge ─────────────────────────────────────────────────────
    villages = []
    for vname, troops in table_villages.items():
        coords = sidebar_coords.get(vname)
        villages.append({
            "village_name": vname,
            "x":            coords[0] if coords else None,
            "y":            coords[1] if coords else None,
            "population":   0,
            "troops":       troops,
        })

    if not villages:
        for line in lines:
            cm = coord_re.search(line)
            if not cm:
                continue
            x, y   = int(cm.group(1)), int(cm.group(2))
            vname  = line[:cm.start()].strip().rstrip('\t').strip() or f"({x}|{y})"
            villages.append({"village_name": vname, "x": x, "y": y, "population": 0, "troops": {}})

    return villages


_CROP_MAP = {
    "Legionär": 1, "Prätorianer": 1, "Imperianer": 1,
    "Equites Legati": 2, "Equites Imperatoris": 3, "Equites Caesaris": 4,
    "Rammbock": 5, "Feuerkatapult": 6, "Senator": 5,
    "Keulenschwinger": 1, "Speerkämpfer": 1, "Axtkämpfer": 1,
    "Späher": 1, "Kundschafter": 1, "Paladin": 2, "Teut. Ritter": 3, "Teutonen Reiter": 3,
    "Häuptling": 4, "Stammesführer": 4, "Teutonen-Rammbock": 5, "Ramme": 5,
    "Kriegsmaschine": 6, "Katapult": 6,
    "Phalanx": 1, "Schwertkämpfer": 1, "Pathfinder": 2,
    "Theutates-Blitz": 2, "Druidentreiter": 2, "Haeduer": 3,
    "Stammesältester": 5, "Gallier-Rammbock": 5, "Gallier-Kata": 6,
    "Siedler": 1, "Held": 0,
}


def _enrich_own_villages(own_villages: list) -> list:
    """Attach parsed troops + crop total to each village row."""
    import json as _json
    for v in own_villages:
        try:
            v["troops"] = _json.loads(v.get("troops_json") or "{}")
        except Exception:
            v["troops"] = {}
        v["total_crop"] = sum(_CROP_MAP.get(t, 1) * c for t, c in v["troops"].items())
    return own_villages


@app.get("/guild/{guild_id}/mein-account", response_class=HTMLResponse)
async def mein_account_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err

    own_villages = _enrich_own_villages(await database.get_own_villages(guild_id))
    history      = await database.get_own_villages_history(guild_id)
    uploaded     = request.query_params.get("uploaded")
    cleared      = request.query_params.get("cleared")
    saved        = request.query_params.get("saved")

    # Totals for KPI strip
    total_off  = sum(v.get("off_score", 0) for v in own_villages)
    total_def  = sum(v.get("def_score", 0) for v in own_villages)
    total_crop = sum(v.get("total_crop", 0) for v in own_villages)

    sitters = await database.get_account_sitters(guild_id, session.get("uid", ""))
    dual_links = await database.get_dual_links_for_owner(guild_id, session.get("uid", ""))
    dual_created = request.query_params.get("dual_created")

    hospital_data    = await database.get_hospital_data(guild_id, session.get("uid", ""))
    hospital_uploaded = request.query_params.get("hospital_uploaded")
    hospital_cleared  = request.query_params.get("hospital_cleared")

    return templates.TemplateResponse("mein_account.html", {
        "request":            request,
        "guild":              guild,
        "own_villages":       own_villages,
        "history":            history,
        "uploaded":           uploaded,
        "cleared":            cleared,
        "saved":              saved,
        "total_off":          total_off,
        "total_def":          total_def,
        "total_crop":         total_crop,
        "sitters":            sitters,
        "dual_links":         dual_links,
        "dual_created":       dual_created,
        "hospital_data":      hospital_data,
        "hospital_uploaded":  hospital_uploaded,
        "hospital_cleared":   hospital_cleared,
    })


@app.post("/guild/{guild_id}/mein-account")
async def mein_account_upload(
    request: Request,
    guild_id: str,
    troop_text: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
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
        v["off_score"]    = off_s
        v["def_score"]    = def_s
        v["priority"]     = prio
    if parsed:
        await database.save_own_villages(guild_id, parsed, uploaded_by)
    return RedirectResponse(f"/guild/{guild_id}/mein-account?uploaded={len(parsed)}", status_code=303)


@app.post("/guild/{guild_id}/mein-account/clear")
async def mein_account_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    await database.delete_own_villages(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/mein-account?cleared=1", status_code=303)


@app.post("/guild/{guild_id}/mein-account/sitters")
async def save_sitters(
    request: Request,
    guild_id: str,
    sitter1_name: str = Form(""),
    sitter1_travian: str = Form(""),
    sitter2_name: str = Form(""),
    sitter2_travian: str = Form(""),
    sitting1_name: str = Form(""),
    sitting1_travian: str = Form(""),
    sitting2_name: str = Form(""),
    sitting2_travian: str = Form(""),
    is_shared: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.save_account_sitters(guild_id, session.get("uid", ""), {
        "sitter1_name": sitter1_name or None,
        "sitter1_travian": sitter1_travian or None,
        "sitter2_name": sitter2_name or None,
        "sitter2_travian": sitter2_travian or None,
        "sitting1_name": sitting1_name or None,
        "sitting1_travian": sitting1_travian or None,
        "sitting2_name": sitting2_name or None,
        "sitting2_travian": sitting2_travian or None,
        "is_shared": bool(is_shared),
    })
    return RedirectResponse(f"/guild/{guild_id}/mein-account?saved=1", status_code=303)


@app.get("/guild/{guild_id}/mein-account/kampfkraft", response_class=HTMLResponse)
async def kampfkraft_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err
    # Pass own villages so the calculator can pre-fill troop counts
    own_villages = _enrich_own_villages(await database.get_own_villages(guild_id))
    return templates.TemplateResponse("kampfkraft.html", {
        "request": request, "guild": guild, "own_villages": own_villages,
    })


# Legacy redirects — keep old /attacks/own-troops URLs working
@app.get("/guild/{guild_id}/attacks/own-troops")
async def _legacy_own_troops_get(guild_id: str):
    return RedirectResponse(f"/guild/{guild_id}/mein-account", status_code=301)


# ---------------------------------------------------------------------------
# Helper: alliance manager check
# ---------------------------------------------------------------------------

def _is_alliance_manager(session: dict, guild: dict) -> bool:
    """True if user is admin, guild owner, or has MANAGE_GUILD permission on this guild."""
    if session.get("type") == "admin":
        return True
    if session.get("uid") == (guild.get("owner_discord_id") or ""):
        return True
    # Check MANAGE_GUILD permission from guilds stored in session
    guild_id = guild.get("guild_id", "")
    # session doesn't carry per-guild permissions directly, but accessible guilds
    # require MANAGE_GUILD already (see auth_callback), so any accessible guild is managed.
    # For a more granular check we'd need the permission bits stored in session.
    # Simple approach: if user can access this guild, they have MANAGE_GUILD.
    return True  # Already gated by can_access_guild which requires MANAGE_GUILD


# ---------------------------------------------------------------------------
# Routes — Allianz Sitter-Liste (Feature 1)
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/allianz/sitter-liste", response_class=HTMLResponse)
async def allianz_sitter_liste(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    shared = await database.get_all_shared_sitters(guild_id)
    return templates.TemplateResponse("allianz_sitter.html", {
        "request": request,
        "guild": guild,
        "shared_sitters": shared,
    })


# ---------------------------------------------------------------------------
# Routes — Settle-Liste (Feature 2)
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/settle-list", response_class=HTMLResponse)
async def settle_list_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err
    entries = await database.get_settle_list(guild_id)
    is_manager = _is_alliance_manager(session, guild)
    return templates.TemplateResponse("settle_list.html", {
        "request": request,
        "guild": guild,
        "entries": entries,
        "uid": session.get("uid", ""),
        "is_manager": is_manager,
    })


@app.post("/guild/{guild_id}/settle-list")
async def settle_list_add(
    request: Request,
    guild_id: str,
    coordinates: str = Form(...),
    player_name: str = Form(""),
    note: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err
    coords = coordinates.strip()[:30]
    if not coords:
        return RedirectResponse(f"/guild/{guild_id}/settle-list?error=coords_required", status_code=303)
    await database.add_settle_entry(
        guild_id=guild_id,
        user_id=session.get("uid", ""),
        username=session.get("username", ""),
        player_name=player_name.strip()[:80] or None,
        coordinates=coords,
        note=note.strip()[:200] or None,
    )
    return RedirectResponse(f"/guild/{guild_id}/settle-list", status_code=303)


@app.post("/guild/{guild_id}/settle-list/{entry_id}/delete")
async def settle_list_delete(request: Request, guild_id: str, entry_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    is_manager = _is_alliance_manager(session, guild)
    await database.delete_settle_entry(entry_id, guild_id, session.get("uid", ""), is_manager)
    return RedirectResponse(f"/guild/{guild_id}/settle-list", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Dual-Link System (Feature 3)
# ---------------------------------------------------------------------------

@app.post("/guild/{guild_id}/mein-account/dual/create")
async def dual_create(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err
    token = await database.create_dual_invite(
        guild_id=guild_id,
        owner_id=session.get("uid", ""),
        owner_username=session.get("username", ""),
    )
    return RedirectResponse(f"/guild/{guild_id}/mein-account?dual_created={token}", status_code=303)


@app.post("/guild/{guild_id}/mein-account/dual/revoke")
async def dual_revoke(request: Request, guild_id: str, token: str = Form(...)):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.revoke_dual_link(token, session.get("uid", ""))
    return RedirectResponse(f"/guild/{guild_id}/mein-account", status_code=303)


@app.get("/dual/join/{token}", response_class=HTMLResponse)
async def dual_join_page(request: Request, token: str):
    session, err = _require_session(request)
    if err: return err
    link = await database.get_dual_link_by_token(token)
    if not link or link["status"] == "revoked":
        return templates.TemplateResponse("error.html", {
            "request": request,
            "emoji": "🔗",
            "code": "404",
            "message": "Dieser Einladungslink ist ungültig oder wurde widerrufen.",
            "detail": None,
        }, status_code=404)
    return templates.TemplateResponse("dual_join.html", {
        "request": request,
        "link": link,
        "token": token,
        "already_active": link["status"] == "active",
    })


@app.post("/dual/join/{token}")
async def dual_join_accept(request: Request, token: str):
    session, err = _require_session(request)
    if err: return err
    link = await database.get_dual_link_by_token(token)
    if not link or link["status"] != "pending":
        return RedirectResponse(f"/dual/join/{token}", status_code=303)
    await database.accept_dual_invite(token, session.get("uid", ""), session.get("username", ""))
    return RedirectResponse(f"/dual/join/{token}?accepted=1", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Farmlist-Analyst (Feature 4)
# ---------------------------------------------------------------------------

def parse_farmlist(text: str) -> list[dict]:
    """Parse Travian farmlist copy-paste (single or multi-list) into structured farm dicts.

    Schema per entry (after header line with village+pop+dist):
      Line +1: troops count (integer)
      Line +2: last raid time (HH:MM:SS) or date (yesterday / DD.MM.YYYY)
      Line +3: resources stolen last raid
      Line +4: total resources stolen (cumulative)
    """
    import re as _re

    # Strip all Unicode direction/formatting marks (LRE, PDF, LRM, etc.)
    _UNI = _re.compile(r'[‪‫‬‭‮‎‏⁨⁩‪‫‬‭‮‎‏]')
    clean = _UNI.sub('', text)
    lines = clean.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    # Skip-patterns for UI chrome lines (no tabs)
    _SKIP = _re.compile(
        r'being raided|^Start\s*\(|^Add target|No target selected|'
        r'^Farm list|^Create farm|^Start all|^Start farm|'
        r'^Farm lists:|^Villages\s+\d|^Village groups|^Population:|^Loyalty:|'
        r'^Spawn$|^\([-\d]+\|[-\d]+\)$|Capital$|Off Villa$|Sup \(|Oasis$|'
        r'Construction\)|Finished\)|^Task overview|^Homepage|^\© \d{4}|'
        r'Discord|Support|Game rules|Terms|Imprint|^Server time|^Alliance banner|'
        r'^Info box|^Link list|^Send Hero|^Recall|^Incoming|^Troop|^CP Over|'
        r'^Farm Builder|^Kirilloid|^Friso|^GT|^Elephant|^Cropper|^Smithy|^TravOps|'
        r'^top10|^Profile|^Rally|^Management|^Overview$|^Send troops|^Simulators|'
        r'^Switch to avatar|^Hero\d|Privacy settings|\d+/\d+$',
        _re.IGNORECASE
    )

    _TIME  = _re.compile(r'^\d{1,2}:\d{2}:\d{2}$')
    _DATE  = _re.compile(r'^(yesterday|\d{1,2}\.\d{1,2}\.\d{4})$', _re.IGNORECASE)
    # Group header: e.g. "00 Gen", "01 Exo", "02 Levi", "11 Chr"
    _GROUP = _re.compile(r'^\d{2}\s+\w+$')
    # Farmlist name: e.g. "N0 Gen Farmlist", "N1 Exo Farmlist"
    _LIST  = _re.compile(r'^N\d+\s+\w+\s+Farmlist\b', _re.IGNORECASE)

    farms = []
    current_group = "Unbekannte Gruppe"
    current_list  = "Unbekannte Liste"
    i = 0

    while i < len(lines):
        raw      = lines[i]
        stripped = raw.strip()

        # ── skip empty lines ──────────────────────────────────────────────
        if not stripped:
            i += 1
            continue

        # ── skip UI chrome (no-tab lines that match skip patterns) ────────
        if not raw.startswith('\t') and _SKIP.search(stripped):
            i += 1
            continue

        # ── farmlist name  e.g. "N0 Exo Farmlist" ────────────────────────
        if not raw.startswith('\t') and _LIST.match(stripped):
            current_list = stripped
            i += 1
            continue

        # ── group header  e.g. "00 Gen", "01 Exo" ────────────────────────
        if not raw.startswith('\t') and _GROUP.match(stripped):
            current_group = stripped
            i += 1
            continue

        # ── farm entry line: 2+ leading tabs, at least 3 tab-fields ──────
        if raw.startswith('\t\t') or (raw.startswith('\t') and raw.count('\t') >= 2):
            parts = [p.strip() for p in raw.split('\t')]
            while parts and not parts[0]:
                parts.pop(0)

            if len(parts) >= 3 and parts[0]:
                village_name = parts[0]

                try:
                    population = int(_re.sub(r'\D', '', parts[1])) if parts[1] else 0
                except Exception:
                    population = 0

                try:
                    ds = _re.sub(r'[^\d.,]', '', parts[2]).replace(',', '.')
                    distance = float(ds) if ds else 0.0
                except Exception:
                    distance = 0.0

                # --- state-machine: N×troops → time → res_last → res_total ----
                # Some farms have multiple troop-type lines before the time,
                # so we can't rely on fixed line positions.
                troops    = 0
                last_raid = ""
                res_last  = 0
                res_total = 0
                seen_time     = False
                after_time    = 0   # 0=res_last not yet, 1=res_total not yet
                last_j        = 0

                for j in range(1, 12):
                    if i + j >= len(lines):
                        break
                    raw_sub = lines[i + j]
                    nxt = _UNI.sub('', raw_sub).strip()
                    if not nxt:
                        break
                    if raw_sub.startswith('\t\t'):
                        break

                    last_j = j

                    if _TIME.match(nxt) or _DATE.match(nxt):
                        last_raid = nxt
                        seen_time = True
                    elif seen_time:
                        try:
                            val = int(_re.sub(r'\D', '', nxt)) if nxt else 0
                        except Exception:
                            val = 0
                        if after_time == 0:
                            res_last = val
                        else:
                            res_total = val
                            break
                        after_time += 1
                    else:
                        # Pre-time integer(s) = troops (sum multiple troop types)
                        try:
                            troops += int(_re.sub(r'\D', '', nxt)) if nxt else 0
                        except Exception:
                            pass

                # Rating based on last-raid loot and efficiency
                efficiency = round(res_last / distance, 1) if distance > 0 else 0.0
                if res_last >= 100:
                    rating = "gut"
                elif res_last > 0:
                    rating = "ok"
                else:
                    rating = "leer"   # empty last raid

                # Natars & oases always 0 resources → flag as "natar"
                is_natar = bool(_re.match(r'^Natars\s', village_name))
                is_oasis = 'oasis' in village_name.lower() or 'Occupied oasis' in village_name

                farms.append({
                    "group":       current_group,
                    "list_name":   current_list,
                    "village_name": village_name,
                    "population":  population,
                    "distance":    distance,
                    "troops":      troops,
                    "last_raid":   last_raid,
                    "res_last":    res_last,
                    "res_total":   res_total,
                    "efficiency":  efficiency,
                    "rating":      rating,
                    "abandoned":   population == 0 and not is_natar and not is_oasis,
                    "is_natar":    is_natar,
                    "is_oasis":    is_oasis,
                    # backwards-compat alias
                    "resources":   res_last,
                })
                i += last_j + 1
                continue

        i += 1

    return farms


@app.get("/guild/{guild_id}/farmlist-analyst", response_class=HTMLResponse)
async def farmlist_analyst_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err
    past = await database.get_farmlist_analyses(guild_id, session.get("uid", ""))
    return templates.TemplateResponse("farmlist_analyst.html", {
        "request":     request,
        "guild":       guild,
        "farms":       [],
        "stats":       None,
        "group_stats": [],
        "past":        past,
        "raw_text":    "",
    })


@app.post("/guild/{guild_id}/farmlist-analyst", response_class=HTMLResponse)
async def farmlist_analyst_post(
    request: Request,
    guild_id: str,
    farmlist_text: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err

    farms = parse_farmlist(farmlist_text)

    # ── overall stats ──────────────────────────────────────────────────────
    gut     = [f for f in farms if f["rating"] == "gut"]
    ok      = [f for f in farms if f["rating"] == "ok"]
    leer    = [f for f in farms if f["rating"] == "leer"]
    total_res_last  = sum(f["res_last"]  for f in farms)
    total_res_total = sum(f["res_total"] for f in farms)
    avg_res  = round(total_res_last  / len(farms), 1) if farms else 0
    avg_dist = round(sum(f["distance"] for f in farms) / len(farms), 1) if farms else 0

    # ── per-group stats ────────────────────────────────────────────────────
    from collections import defaultdict as _dd
    _grp_data: dict = _dd(list)
    for f in farms:
        _grp_data[f["group"]].append(f)

    group_stats = []
    for grp_name, grp_farms in _grp_data.items():
        non_natar = [f for f in grp_farms if not f["is_natar"] and not f["is_oasis"]]
        group_stats.append({
            "name":      grp_name,
            "total":     len(grp_farms),
            "gut":       sum(1 for f in grp_farms if f["rating"] == "gut"),
            "ok":        sum(1 for f in grp_farms if f["rating"] == "ok"),
            "leer":      sum(1 for f in grp_farms if f["rating"] == "leer"),
            "natars":    sum(1 for f in grp_farms if f["is_natar"]),
            "res_last":  sum(f["res_last"]  for f in grp_farms),
            "res_total": sum(f["res_total"] for f in grp_farms),
            "avg_dist":  round(sum(f["distance"] for f in non_natar) / len(non_natar), 1) if non_natar else 0,
            "lists":     sorted({f["list_name"] for f in grp_farms}),
        })

    stats = {
        "total":           len(farms),
        "gut":             len(gut),
        "ok":              len(ok),
        "leer":            len(leer),
        "avg_res":         avg_res,
        "avg_dist":        avg_dist,
        "total_res_last":  total_res_last,
        "total_res_total": total_res_total,
        "groups":          [g["name"] for g in group_stats],
    }

    # Save analysis to DB
    await database.save_farmlist_analysis(
        guild_id, session.get("uid", ""),
        session.get("username", ""),
        stats, group_stats, farms,
    )
    past = await database.get_farmlist_analyses(guild_id, session.get("uid", ""))

    return templates.TemplateResponse("farmlist_analyst.html", {
        "request":      request,
        "guild":        guild,
        "farms":        farms,
        "stats":        stats,
        "group_stats":  group_stats,
        "past":         past,
        "raw_text":     farmlist_text,
    })


@app.get("/guild/{guild_id}/farmlist-analyst/{analysis_id}/open", response_class=HTMLResponse)
async def farmlist_analysis_open(request: Request, guild_id: str, analysis_id: int):
    import json as _json
    session, err = _require_session(request)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_premium(guild, guild_id)
    if err: return err

    row = await database.get_farmlist_analysis(analysis_id, session.get("uid", ""))
    if not row:
        return RedirectResponse(f"/guild/{guild_id}/farmlist-analyst")

    farms      = _json.loads(row.get("farms_json") or "[]")
    group_stats = _json.loads(row.get("group_stats_json") or "[]")
    past       = await database.get_farmlist_analyses(guild_id, session.get("uid", ""))

    stats = {
        "total":          row["total_farms"],
        "gut":            row["gut"],
        "ok":             row["ok"],
        "leer":           row["leer"],
        "avg_res":        row["avg_res"],
        "total_res_last": row["total_res_last"],
        "total_res_total":row["total_res_total"],
        "groups":         _json.loads(row.get("groups_json") or "[]"),
        "avg_dist":       round(sum(f.get("distance", 0) for f in farms if not f.get("is_npc")) / max(1, sum(1 for f in farms if not f.get("is_npc"))), 1) if farms else 0,
    }

    return templates.TemplateResponse("farmlist_analyst.html", {
        "request":     request,
        "guild":       guild,
        "farms":       farms,
        "stats":       stats,
        "group_stats": group_stats,
        "past":        past,
        "raw_text":    "",
        "opened_id":   analysis_id,
    })


@app.post("/guild/{guild_id}/farmlist-analyst/{analysis_id}/delete")
async def farmlist_analysis_delete(request: Request, guild_id: str, analysis_id: int):
    session, err = _require_session(request)
    if err: return err
    await database.delete_farmlist_analysis(analysis_id, session.get("uid", ""))
    return RedirectResponse(f"/guild/{guild_id}/farmlist-analyst", status_code=303)


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
    tab: str = "inactive",
    q: str = "",
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    is_admin = session.get("guilds") is None

    # Auto-fetch first snapshot if none exists and world is configured
    tw_world = (guild.get("tw_world") or "").strip()
    auto_fetched = False
    auto_fetch_error = ""
    if tw_world:
        snap_count_pre = await database.get_snapshot_count(guild_id)
        if snap_count_pre == 0:
            try:
                await _fetch_and_save_snapshot(guild_id, tw_world)
                auto_fetched = True
            except Exception as e:
                auto_fetch_error = str(e)

    farm_stats = await database.get_farm_stats(guild_id)
    inactive_farms = await database.get_inactive_farms(guild_id, min_days=min_days, min_pop=min_pop, max_pop=max_pop)
    farm_list = await database.get_farm_list(guild_id)
    cross_reference = await database.get_farming_cross_reference(guild_id, min_days=min_days)
    cross_ref_coords = {(r["x"], r["y"]) for r in cross_reference}

    # Growth analysis
    growth_data = await database.get_player_growth(guild_id, limit=100)

    # Search — auto-fetch snapshot on-demand if none exists yet
    search_results = []
    search_error = ""
    snap_count_for_search = await database.get_snapshot_count(guild_id)
    if q.strip():
        if snap_count_for_search == 0:
            if tw_world:
                try:
                    await _fetch_and_save_snapshot(guild_id, tw_world)
                    snap_count_for_search = 1
                except Exception as e:
                    search_error = f"Snapshot konnte nicht geladen werden: {e}"
            else:
                search_error = "Keine Travian-Welt konfiguriert. Bitte erst eine Welt-URL unter Map-Einstellungen eintragen."
        if not search_error:
            search_results = await database.search_map_snapshot(guild_id, q.strip())

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
        "tab": tab,
        "q": q,
        "growth_data": growth_data,
        "search_results": search_results,
        "search_error": search_error,
        "auto_fetched": auto_fetched,
        "auto_fetch_error": auto_fetch_error,
        "snap_count_for_search": snap_count_for_search,
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


@app.post("/guild/{guild_id}/farming/snapshots/clear")
async def farming_snapshots_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.clear_all_snapshots(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/farming?saved=snapshots_cleared", status_code=303)


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


# ---------------------------------------------------------------------------
# Hospital (Lazarett) Parser
# ---------------------------------------------------------------------------

def parse_hospital(text: str) -> list[dict]:
    """Parse Travian hospital copy-paste into list of dicts.

    Each dict: {village_name, troop_name, count, heal_finish}

    Format:
      Village name
      TroopName\tCount\tHH:MM:SS  (or DD.MM.YYYY HH:MM:SS)
    """
    import re as _re

    # Strip Unicode direction/formatting marks
    text = _re.sub(r'[​-‏‪-‮⁦-⁩﻿]', '', text)
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    _SKIP = _re.compile(
        r'^Lazarett$|^Hospital$|^Krankenhaus$|^Heilung$|^Truppe$|^Anzahl$|'
        r'^Fertig$|^Troop$|^Count$|^Healing finish$|^Finish$|'
        r'^Dorf$|^Village$|^Overview$|^Übersicht$|'
        r'^Homepage|^\© \d{4}|Discord|Support|Game rules|Terms|Imprint|'
        r'^Server time|^TravOps|^Profile|^Rally|^Management|'
        r'^\s*Troop\s+Count\s+|^\s*Truppe\s+Anzahl',
        _re.IGNORECASE,
    )

    # Time-like patterns
    _TIME_ONLY = _re.compile(r'^\d{1,2}:\d{2}:\d{2}$')
    _DATETIME  = _re.compile(r'^\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}:\d{2}:\d{2}$')

    entries = []
    current_village = None

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue

        # Tab-separated data row?
        if '\t' in stripped:
            parts = [p.strip() for p in stripped.split('\t')]
            parts = [p for p in parts if p]
            # Expect: TroopName, Count, Time  (at least 2 parts, count is numeric)
            if len(parts) >= 2:
                troop_name = parts[0]
                # Skip if first part looks like a header word
                if _SKIP.match(troop_name):
                    continue
                try:
                    count = int(_re.sub(r'\D', '', parts[1]))
                except (ValueError, IndexError):
                    continue
                heal_finish = None
                if len(parts) >= 3:
                    t = parts[2].strip()
                    if _TIME_ONLY.match(t) or _DATETIME.match(t):
                        heal_finish = t
                    elif len(parts) >= 4:
                        # Maybe date and time split across columns
                        combined = f"{parts[2]} {parts[3]}".strip()
                        if _DATETIME.match(combined):
                            heal_finish = combined
                if current_village and troop_name:
                    entries.append({
                        "village_name": current_village,
                        "troop_name": troop_name,
                        "count": count,
                        "heal_finish": heal_finish,
                    })
            continue

        # Skip known UI chrome
        if _SKIP.search(stripped):
            continue

        # Otherwise treat as village name (non-tab line)
        current_village = stripped

    return entries


# ---------------------------------------------------------------------------
# Routes — Lazarett-Tracker (Hospital)
# ---------------------------------------------------------------------------

@app.post("/guild/{guild_id}/mein-account/hospital")
async def hospital_upload(
    request: Request,
    guild_id: str,
    hospital_text: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err

    entries = parse_hospital(hospital_text)
    await database.save_hospital_data(
        guild_id=guild_id,
        discord_user_id=session.get("uid", ""),
        discord_username=session.get("username"),
        entries=entries,
    )
    return RedirectResponse(
        f"/guild/{guild_id}/mein-account?hospital_uploaded={len(entries)}",
        status_code=303,
    )


@app.post("/guild/{guild_id}/mein-account/hospital/clear")
async def hospital_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err

    await database.delete_hospital_data(guild_id, session.get("uid", ""))
    return RedirectResponse(f"/guild/{guild_id}/mein-account?hospital_cleared=1", status_code=303)


@app.get("/guild/{guild_id}/allianz/hospital", response_class=HTMLResponse)
async def allianz_hospital(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err

    all_entries = await database.get_all_hospital_data(guild_id)

    # Group by discord_user_id
    from collections import OrderedDict
    grouped: dict[str, dict] = OrderedDict()
    for e in all_entries:
        uid = e["discord_user_id"]
        if uid not in grouped:
            grouped[uid] = {
                "discord_username": e.get("discord_username") or uid,
                "uploaded_at": e.get("uploaded_at", ""),
                "entries": [],
                "total": 0,
            }
        grouped[uid]["entries"].append(e)
        grouped[uid]["total"] += e["count"]
        # Keep latest upload time
        if e.get("uploaded_at", "") > grouped[uid]["uploaded_at"]:
            grouped[uid]["uploaded_at"] = e["uploaded_at"]

    return templates.TemplateResponse("allianz_hospital.html", {
        "request": request,
        "guild": guild,
        "grouped": list(grouped.values()),
        "all_entries": all_entries,
    })


# ── Allianz-Mitglieder ────────────────────────────────────────────────────────

def parse_alliance_members(text: str) -> list[dict]:
    """Parse Travian alliance members page copy-paste.

    The real Travian format is line-based (NOT tab-separated per row):
        1.                      ← rank line
        now online              ← status line
        TT.exe                  ← player name
           (optional icon/tag lines)
        13539\t16\t             ← points TAB villages

    Each block is separated by blank lines.
    Points come before villages in the numeric line.
    """
    import re as _re

    # Strip Unicode direction marks and zero-width characters
    text = _re.sub(r'[​-‏‪-‮⁠-⁩﻿]', '', text)
    # Normalise line endings
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    _RANK_LINE   = _re.compile(r'^\s*(\d+)\.\s*$')
    _STATUS_LINE = _re.compile(
        r'^(now online|max\.\s*\d+\s*h|offline|inaktiv|inactive|away)', _re.IGNORECASE
    )
    _NUM_LINE    = _re.compile(r'^\s*[\d\s.,]+(?:\t[\d\s.,]+)+\s*$')
    _NOISE       = _re.compile(
        r'^(Homepage|©|Discord|Support|Server time|Game rules|Terms|Imprint|'
        r'Switch to|Hero|Server|Alliance|Members|Overview|Profile|Attacks|Bonuses|'
        r'Forum|Options|Details|Tag:|Name:|Members|Ranking|Attacker|Defender|'
        r'Position|Description|Link list|Farm List|Recall|Incoming|Troop|CP |'
        r'Farm Builder|Kirilloid|Friso|GT |Elephant|Cropper|Smithy|TravOps|'
        r'top10|Spawn|Capital|Village groups|Task overview|Send Hero|'
        r'Population:|Loyalty:|Villages )',
        _re.IGNORECASE
    )

    def to_int(s: str) -> int:
        try:
            return int(_re.sub(r'[^\d]', '', s))
        except (ValueError, TypeError):
            return 0

    members = []
    rank = None
    name = None
    # State: looking for rank → status → name → numbers
    state = 'seek_rank'

    for raw in lines:
        stripped = raw.strip()

        # Skip noise lines always
        if _NOISE.search(stripped):
            rank = None; name = None; state = 'seek_rank'
            continue

        if state == 'seek_rank':
            m = _RANK_LINE.match(stripped)
            if m:
                rank = int(m.group(1))
                state = 'seek_status'

        elif state == 'seek_status':
            if _STATUS_LINE.match(stripped):
                state = 'seek_name'
            elif stripped and not _RANK_LINE.match(stripped):
                # Some formats skip the status line — treat this as name
                if stripped and len(stripped) <= 30 and not stripped.isdigit():
                    name = stripped
                    state = 'seek_nums'
                else:
                    rank = None; state = 'seek_rank'

        elif state == 'seek_name':
            if stripped and not _STATUS_LINE.match(stripped) and not _RANK_LINE.match(stripped):
                # skip obvious non-name lines (pure numbers / long noise)
                if not _NUM_LINE.match(stripped):
                    name = stripped
                    state = 'seek_nums'

        elif state == 'seek_nums':
            if not stripped:
                continue  # skip blank lines within a block
            if _NUM_LINE.match(stripped):
                parts = [to_int(p) for p in stripped.split('\t') if _re.sub(r'[^\d]', '', p)]
                if len(parts) >= 2 and name:
                    # Travian alliance page: Points  Villages
                    points   = parts[0]
                    villages = parts[1]
                    members.append({
                        "player_name": name,
                        "rank":        rank or len(members) + 1,
                        "points":      points,
                        "villages":    villages,
                        "population":  0,   # not shown on this page
                        "tribe":       "",
                    })
                rank = None; name = None; state = 'seek_rank'
            elif _RANK_LINE.match(stripped):
                # New rank block started — previous entry had no numbers, skip it
                rank = int(_RANK_LINE.match(stripped).group(1))
                name = None; state = 'seek_status'
            elif _NOISE.search(stripped) or len(stripped) > 40:
                rank = None; name = None; state = 'seek_rank'
            # else: icon / tag line between name and numbers — ignore

    return members


@app.get("/guild/{guild_id}/allianz/mitglieder", response_class=HTMLResponse)
async def alliance_members_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    members        = await database.get_alliance_members(guild_id)
    meta           = await database.get_alliance_members_meta(guild_id)
    alliance_name  = await database.get_tw_alliance_name(guild_id)

    return templates.TemplateResponse("alliance_members.html", {
        "request": request,
        "guild": guild,
        "members": members,
        "meta": meta,
        "alliance_name": alliance_name,
        "imported": request.query_params.get("imported"),
        "cleared": request.query_params.get("cleared"),
        "synced": request.query_params.get("synced"),
    })


@app.post("/guild/{guild_id}/allianz/mitglieder/set-alliance")
async def alliance_members_set_alliance(
    request: Request, guild_id: str,
    alliance_name: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.set_tw_alliance_name(guild_id, alliance_name)
    # Trigger immediate sync if snapshot exists
    count = await database.sync_alliance_members_from_snapshot(guild_id)
    return RedirectResponse(
        f"/guild/{guild_id}/allianz/mitglieder?synced={count}",
        status_code=303
    )


@app.post("/guild/{guild_id}/allianz/mitglieder")
async def alliance_members_import(
    request: Request,
    guild_id: str,
    members_text: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    parsed = parse_alliance_members(members_text)
    if parsed:
        uname = session.get("username", "unknown")
        await database.save_alliance_members(guild_id, parsed, uname)

    return RedirectResponse(
        f"/guild/{guild_id}/allianz/mitglieder?imported={len(parsed)}",
        status_code=303
    )


@app.post("/guild/{guild_id}/allianz/mitglieder/clear")
async def alliance_members_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    async with __import__('aiosqlite').connect(database.DB_PATH) as db:
        await db.execute("DELETE FROM alliance_members WHERE guild_id = ?", (guild_id,))
        await db.commit()
    return RedirectResponse(
        f"/guild/{guild_id}/allianz/mitglieder?cleared=1",
        status_code=303
    )
