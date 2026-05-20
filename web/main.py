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

STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_MONTHLY   = os.environ.get("STRIPE_PRICE_MONTHLY", "")
STRIPE_PRICE_ANNUAL    = os.environ.get("STRIPE_PRICE_ANNUAL", "")

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


def _require_guild(session: dict, guild_id: str):
    """Returns error_response if guild access denied."""
    if not SNOWFLAKE_RE.match(guild_id):
        return RedirectResponse("/dashboard", status_code=303)
    if not can_access_guild(session, guild_id):
        return RedirectResponse("/dashboard", status_code=303)
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
app.mount("/static", StaticFiles(directory="static"), name="static")

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

import json as _json
templates.env.filters["from_json"] = lambda s: _json.loads(s) if s else []


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
    state = request.cookies.get("oauth_state", secrets.token_urlsafe(32))
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
    })
    return RedirectResponse(f"https://discord.com/api/oauth2/authorize?{params}")


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", error: str = "", state: str = ""):
    if error or not code:
        return RedirectResponse("/login?error=Discord+authentication+cancelled")

    # Validate OAuth state to prevent CSRF
    expected_state = request.cookies.get("oauth_state", "")
    if not expected_state or not secrets.compare_digest(expected_state, state):
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
            return RedirectResponse("/login?error=Discord+authentication+failed.+Please+try+again.")
        access_token = r.json()["access_token"]

        user_r = await client.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_r.status_code != 200:
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

    if not accessible:
        return RedirectResponse(
            "/login?error=No+accessible+servers+found.+You+need+Admin+or+Manage+Server+permission."
        )

    username = user.get("global_name") or user.get("username", "Unknown")
    session_data = {
        "type": "discord",
        "uid": str(user["id"]),
        "username": username,
        "guilds": accessible,
    }
    token = create_session(session_data)
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
        f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions=536996880&scope=bot+applications.commands"
        if client_id else ""
    )
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "guilds": guilds, "invite_url": invite_url, "session": session},
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
    return templates.TemplateResponse(
        "guild.html",
        {"request": request, "guild": guild, "saved": saved, "roles": roles, "is_admin": is_admin},
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
        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "Scout", "type": 4})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=category_{r.status_code}", status_code=303)
        category_id = r.json()["id"]

        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "scout-requests", "type": 0, "parent_id": category_id})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=scout_ch_{r.status_code}", status_code=303)
        scout_channel_id = r.json()["id"]

        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={
            "name": "scout-archive", "type": 0, "parent_id": category_id,
            "permission_overwrites": [{"id": guild_id, "type": 0, "allow": "0", "deny": VIEW_CHANNEL}],
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
    # Validate: must be https://....travian.com or similar
    url = server_url.strip().rstrip("/")
    if url and not re.match(r"^https://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", url):
        return RedirectResponse(f"/guild/{guild_id}/map?error=invalid_url", status_code=303)
    await database.update_tw_world(guild_id, url)
    return RedirectResponse(f"/guild/{guild_id}/map", status_code=303)


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
    stripe_configured = bool(STRIPE_SECRET_KEY and STRIPE_PRICE_MONTHLY and STRIPE_PRICE_ANNUAL)
    return templates.TemplateResponse("billing.html", {
        "request": request,
        "guild": guild,
        "is_admin": is_admin,
        "stripe_pk": STRIPE_PUBLISHABLE_KEY,
        "stripe_configured": stripe_configured,
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@app.post("/guild/{guild_id}/billing/checkout")
async def billing_checkout(request: Request, guild_id: str, plan: str = Form(...)):
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

    price_id = STRIPE_PRICE_MONTHLY if plan == "monthly" else STRIPE_PRICE_ANNUAL
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
        metadata={"guild_id": guild_id},
    )
    if customer_id:
        checkout_kwargs["customer"] = customer_id
    else:
        checkout_kwargs["customer_creation"] = "always"

    checkout_session = s.checkout.Session.create(**checkout_kwargs)
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
        plan = "annual" if sub.items.data[0].price.recurring.interval == "year" else "monthly"
        import datetime
        expires_at = datetime.datetime.utcfromtimestamp(sub.current_period_end).isoformat()
        await database.update_subscription(
            guild_id=guild_id,
            stripe_customer_id=checkout.customer,
            stripe_subscription_id=sub.id,
            status="active",
            plan=plan,
            expires_at=expires_at,
        )
    except Exception:
        pass

    return RedirectResponse(f"/guild/{guild_id}/billing?saved=1", status_code=303)


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
        guild = await database.get_guild_by_stripe_customer(customer_id)
        if guild:
            status = obj.get("status", "inactive")  # active, trialing, past_due, etc.
            interval = obj.get("items", {}).get("data", [{}])[0].get("price", {}).get("recurring", {}).get("interval", "month")
            plan = "annual" if interval == "year" else "monthly"
            import datetime
            expires_at = datetime.datetime.utcfromtimestamp(obj["current_period_end"]).isoformat()
            await database.update_subscription(
                guild_id=guild["guild_id"],
                stripe_customer_id=customer_id,
                stripe_subscription_id=obj["id"],
                status=status,
                plan=plan,
                expires_at=expires_at,
            )

    elif event["type"] == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        guild = await database.get_guild_by_stripe_customer(customer_id)
        if guild:
            await database.set_subscription_status(guild["guild_id"], "cancelled")

    elif event["type"] == "invoice.payment_failed":
        customer_id = obj.get("customer")
        guild = await database.get_guild_by_stripe_customer(customer_id)
        if guild:
            await database.set_subscription_status(guild["guild_id"], "past_due")


# ---------------------------------------------------------------------------
# Routes — Attacks
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/attacks", response_class=HTMLResponse)
async def attacks_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    is_admin = session.get("type") == "admin"
    attack_reports = await database.get_attack_reports(guild_id, limit=50)
    attack_stats = await database.get_attack_stats(guild_id)
    return templates.TemplateResponse(
        "attacks.html",
        {
            "request": request,
            "guild": guild,
            "saved": saved,
            "is_admin": is_admin,
            "attack_reports": attack_reports,
            "attack_stats": attack_stats,
        },
    )


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
    attack_channel_id = sanitize_snowflake(attack_channel_id)
    await database.set_attack_channel_web(guild_id, attack_channel_id)
    return RedirectResponse(f"/guild/{guild_id}/attacks?saved=1", status_code=303)


@app.post("/guild/{guild_id}/attacks/delete/{report_id}")
async def attacks_delete(request: Request, guild_id: str, report_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
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
    await database.set_attack_channel_web(guild_id, "", "")
    return RedirectResponse(f"/guild/{guild_id}/attacks?saved=1", status_code=303)


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
