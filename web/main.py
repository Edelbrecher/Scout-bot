import io
import os
import zipfile
import asyncio
from pathlib import Path
import base64
import re
import secrets
import smtplib
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional, List
from email.mime.text import MIMEText
from urllib.parse import urlencode

import httpx
import stripe

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware

import database
import presets as blueprint_presets

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
SESSION_COOKIE = "scouter_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

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

# Pricing tiers: Player Pro / Starter / Clan / Alliance / Imperium
STRIPE_PRICES: dict[str, dict[str, str]] = {
    "player_pro": {
        "monthly": os.environ.get("STRIPE_PRICE_PLAYER_PRO_M", ""),
        "annual":  os.environ.get("STRIPE_PRICE_PLAYER_PRO_A", ""),
    },
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

# Tiers that include player/solo features (all paid tiers)
PLAYER_PRO_TIERS = {"player_pro", "starter", "clan", "alliance", "imperium"}
# Tiers that include alliance/discord features (all except player_pro)
ALLIANCE_TIERS = {"starter", "clan", "alliance", "imperium"}
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
    "player_pro": {"name": "Player Pro", "servers": 0, "monthly": 2.99, "annual": 23.99,
                   "desc": "Solo-Features für Einzelspieler — kein Discord-Server nötig",
                   "player_only": True},
    "starter":  {"name": "Starter",  "servers": 1, "monthly": 6.99,  "annual": 55.99},
    "clan":     {"name": "Clan",     "servers": 2, "monthly": 10.99, "annual": 87.99},
    "alliance": {"name": "Alliance", "servers": 3, "monthly": 14.99, "annual": 119.99},
    "imperium": {"name": "Imperium", "servers": 5, "monthly": 19.99, "annual": 159.99},
}

# Discord snowflake: 17-20 digit numeric string
SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")
WORKSPACE_RE = re.compile(r"^ws_[0-9a-f]{16}$")

def is_valid_guild_id(value: str) -> bool:
    """Accept both Discord snowflakes and personal workspace IDs."""
    return bool(SNOWFLAKE_RE.match(value)) or bool(WORKSPACE_RE.match(value))

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
    # Personal workspaces: owned by the session user
    if WORKSPACE_RE.match(guild_id):
        return session.get("uid") == session.get("_ws_owner_" + guild_id, session.get("uid"))
    return guild_id in session["guilds"]


async def can_access_guild_async(session: dict, guild_id: str) -> bool:
    """Like can_access_guild but also checks personal workspace ownership in DB."""
    if session.get("guilds") is None:
        return True  # super-admin
    if WORKSPACE_RE.match(guild_id):
        # Verify ownership against DB
        guild = await database.get_guild(guild_id)
        if not guild:
            return False
        return guild.get("workspace_owner_id") == session.get("uid")
    if guild_id in session["guilds"]:
        return True
    # Also grant access if user has joined an ally on this guild via invite link
    uid = session.get("uid", "")
    if uid:
        membership = await database.get_ally_membership(guild_id, uid)
        if membership:
            return True
    return False


_DISCORD_BOT_UA = re.compile(r'Discordbot|Twitterbot|facebookexternalhit|LinkedInBot|Slackbot', re.I)

def _og_preview_response(request: Request) -> Response:
    """Return a minimal HTML page with OG tags for link-preview bots."""
    url = str(request.url)
    html = f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8"/>
<meta property="og:site_name" content="TravOps"/>
<meta property="og:type" content="website"/>
<meta property="og:title" content="TravOps — Travian Allianz-Management"/>
<meta property="og:description" content="Scout-Tracking, Einsatzplanung, Defend-Koordination und mehr für Travian Legends."/>
<meta property="og:image" content="https://travops.online/static/logo.png"/>
<meta property="og:image:width" content="512"/>
<meta property="og:image:height" content="512"/>
<meta property="og:url" content="{url}"/>
<meta name="theme-color" content="#5865f2"/>
</head><body></body></html>"""
    return HTMLResponse(html)


def _require_session(request: Request):
    """Returns (session, error_response). error_response is set if auth fails."""
    # Let link-preview bots (Discord, Slack, …) see OG tags instead of OAuth redirect
    ua = request.headers.get("user-agent", "")
    if _DISCORD_BOT_UA.search(ua):
        return None, _og_preview_response(request)
    session = get_session(request)
    if not session:
        return None, RedirectResponse("/login", status_code=303)
    return session, None


def _get_session(request: Request) -> dict | None:
    """Returns session dict or None without redirecting (for public pages)."""
    return get_session(request)


def _require_guild(session: dict, guild_id: str):
    """Returns error_response if guild access denied."""
    if not is_valid_guild_id(guild_id):
        return RedirectResponse("/dashboard", status_code=303)
    # Personal workspace: sync check will be done async in route; pass here
    if WORKSPACE_RE.match(guild_id):
        return None  # async check done in route via _require_guild_async
    if not can_access_guild(session, guild_id):
        return RedirectResponse("/dashboard", status_code=303)


async def _require_guild_async(session: dict, guild_id: str):
    """Async version for routes that need to check personal workspace ownership."""
    if not is_valid_guild_id(guild_id):
        return RedirectResponse("/dashboard", status_code=303)
    if not await can_access_guild_async(session, guild_id):
        return RedirectResponse("/dashboard", status_code=303)


def is_guild_owner(session: dict, guild: dict) -> bool:
    """True if the logged-in user is the subscription owner of this guild."""
    if session.get("type") == "admin":
        return True
    return session.get("uid", "") == (guild.get("owner_discord_id") or "")


async def has_perm(request: Request, guild_id: str, flag: str) -> bool:
    """Check if the current user has a specific TravOps permission flag.
    Guild owners and admin sessions always pass. Returns False if not logged in."""
    session = get_session(request) or {}
    if not session.get("uid"):
        return False
    if session.get("type") == "admin":
        return True
    uid = session["uid"]
    # Check if guild subscription owner (they bypass everything)
    guild = await database.get_guild(guild_id)
    if guild and guild.get("owner_discord_id") == uid:
        return True
    # Delegate to alliance role permissions
    perms = await database.get_member_permissions(guild_id, uid)
    return flag in perms


PREMIUM_STATUSES = ("active", "trialing")


def _guild_plan(guild: dict) -> str:
    """Return the subscription plan key, normalising legacy values."""
    raw = (guild.get("subscription_plan") or "").lower()
    # strip interval suffixes like 'starter_monthly'
    for sep in ("_monthly", "_annual"):
        if raw.endswith(sep):
            raw = raw[: -len(sep)]
    return raw or "free"


def _has_player_pro(guild: dict) -> bool:
    """True if the guild/workspace has a paid subscription (any tier)."""
    status = guild.get("subscription_status") or "free"
    if status not in (*PREMIUM_STATUSES, "past_due"):
        return False
    return True  # any active paid plan unlocks player features


def _has_alliance_pro(guild: dict) -> bool:
    """True if the guild has a paid subscription that is NOT player_pro-only."""
    if not _has_player_pro(guild):
        return False
    plan = _guild_plan(guild)
    return plan in ALLIANCE_TIERS or plan == ""  # empty plan = legacy starter


async def _enrich_guild_subscription(guild: dict) -> dict:
    """For personal workspaces, inject the owner's user-subscription status into the guild dict
    so all feature-gates work correctly regardless of which route calls them."""
    if not guild:
        return guild
    if guild.get("workspace_type") == "personal":
        owner = guild.get("workspace_owner_id") or guild.get("owner_discord_id") or ""
        if owner:
            user_sub = await database.get_user_subscription(owner)
            if user_sub:
                guild = dict(guild)
                guild["subscription_status"] = user_sub.get("subscription_status", "free")
                guild["subscription_plan"]   = user_sub.get("plan", "")
    return guild


def _billing_url(guild: dict | None, guild_id: str, error: str) -> str:
    """Return the correct billing URL — /billing for personal workspaces, /guild/.../billing otherwise."""
    if guild and guild.get("workspace_type") == "personal":
        return f"/billing?error={error}"
    return f"/guild/{guild_id}/billing?error={error}"


async def _require_premium(guild: dict | None, guild_id: str):
    """Player-Pro gate: any paid plan is sufficient.
    Returns redirect if access denied, None if granted."""
    if guild is None:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=premium_required", status_code=303)
    guild = await _enrich_guild_subscription(guild)
    if not _has_player_pro(guild):
        return RedirectResponse(_billing_url(guild, guild_id, "premium_required"), status_code=303)
    return None


async def _require_alliance(guild: dict | None, guild_id: str):
    """Alliance gate: requires a Starter/Clan/Alliance/Imperium plan (not player_pro).
    Returns redirect if access denied, None if granted."""
    if guild is None:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=alliance_required", status_code=303)
    guild = await _enrich_guild_subscription(guild)
    if not _has_alliance_pro(guild):
        return RedirectResponse(_billing_url(guild, guild_id, "alliance_required"), status_code=303)
    return None


async def _require_ally_or_plan(guild: dict | None, guild_id: str, uid: str,
                                 perm_flag: str = "",
                                 redirect_path: str = "") -> "Response | None":
    """Alliance gate that also accepts approved ally members (with optional perm check).
    - Guild owner or admin → always pass
    - Guild has alliance plan → pass
    - User is an approved ally member (and optionally has perm_flag) → pass
    - If the current guild_id is a workspace (ws_*), look up the user's alliance guild
      and redirect them there so they access the correct alliance features.
    Returns redirect response if denied, None if granted."""
    # If this is a personal workspace and the plan check fails, try to redirect to alliance guild
    if WORKSPACE_RE.match(guild_id) and uid:
        ally_guilds = await database.get_guild_ids_for_discord_user(uid)
        # Find a non-workspace guild the user is an ally member of
        for ag in ally_guilds:
            if not WORKSPACE_RE.match(ag):
                ag_guild = await database.get_guild(ag)
                plan_err = await _require_alliance(ag_guild, ag)
                if plan_err is None:
                    # Redirect to the same page but on the alliance guild
                    target = redirect_path or f"/guild/{ag}"
                    target = target.replace(f"/guild/{guild_id}", f"/guild/{ag}")
                    return RedirectResponse(target, status_code=303)

    # Try plan gate first
    plan_err = await _require_alliance(guild, guild_id)
    if plan_err is None:
        return None  # plan is fine
    # Plan gate failed — check if the user is an approved ally member
    membership = await database.get_ally_membership(guild_id, uid) if uid else None
    if membership and membership.get("status") == "approved":
        # If a specific permission flag is required, check it
        if perm_flag:
            perms = await database.get_member_permissions(guild_id, uid)
            if perm_flag in perms:
                return None
            # No specific perm — but they are a member, still show the page
            # (most view pages are accessible to any approved member)
        else:
            return None
    return plan_err


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
_IS_NUM    = re.compile(r"^-?\d+(\.\d+)?$")


def _parse_map_sql(content: str) -> list[dict]:
    """Parse Travian map.sql — handles three known column layouts.

    Layout A — new T4.5+ format (village_id first, small):
        vid, x, y, tribe, pid, vname, pscore, pname, aid, aname, pop, ?, isCapital
        Detected by: v[0]>=0, v[7] is NOT numeric (it's player_name string)

    Layout B — old x-first (x/y coordinates first):
        x, y, type_id, tribe, vid, vname, pop, pname, pid, aname, aid, ?, isCapital
        Detected by: v[0] is coordinate (can be negative), v[7] is NOT numeric

    Layout C — old vid-first (large village_id first):
        vid, x, y, type_id, tribe, ?, vname, pop, pname, ?, aname, ?, ?, isCapital
        Detected by: v[0] is large (>800), v[7] IS numeric (population)

    The key discriminator between A and B: in layout A, v[2] is the y-coordinate
    (large |value|), while in layout B, v[2] is type_id (always 1, 2, or 3).
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
            f0 = float(v[0]) if v[0] not in ('NULL', '') else None
            if f0 is None:
                continue

            def _int(val, default=0):
                """Parse int from SQL value — handles NULL, FALSE, TRUE, floats."""
                if val in ('NULL', '', None):
                    return default
                if val.upper() == 'FALSE':
                    return 0
                if val.upper() == 'TRUE':
                    return 1
                return int(float(val))

            # Detect layout
            v7_is_num = len(v) > 7 and _IS_NUM.match(v[7].strip())

            if not v7_is_num and f0 >= 0 and len(v) > 10:
                # Layout A: new T4.5+ format
                # (vid, x, y, tribe, pid, vname, score?, pname, aid, aname, pop, NULL, is_capital, ...)
                x            = _int(v[1])
                y            = _int(v[2])
                tribe        = _int(v[3])
                vid          = v[0]
                pid          = v[4]
                vname        = v[5]
                pname        = v[7]
                aname        = v[9] if v[9] not in ('NULL', '') else ""
                pop          = _int(v[10])
                # is_capital: v[11] in old 13-col, v[12] in new 16-col (after extra NULL)
                # Detect: if v[11] is NULL and v[12] looks like boolean → new format
                if len(v) > 12 and v[11] in ('NULL', '') and v[12].upper() in ('TRUE','FALSE','0','1'):
                    is_capital   = _int(v[12])
                    village_type = _int(v[13]) if len(v) > 13 else 0
                else:
                    is_capital   = _int(v[11]) if len(v) > 11 else 0
                    village_type = _int(v[12]) if len(v) > 12 else 0

            elif not v7_is_num and abs(f0) <= 800:
                # Layout B: old x-first — x, y, type_id, tribe, vid, vname, pop, pname, pid, aname
                x            = _int(v[0])
                y            = _int(v[1])
                tribe        = _int(v[3]) if len(v) > 3 else 0
                vname        = v[5] if len(v) > 5 else ""
                pop          = _int(v[6]) if len(v) > 6 else 0
                pname        = v[7] if len(v) > 7 and v[7] not in ('NULL', '') else ""
                aname        = v[9] if len(v) > 9 and v[9] not in ('NULL', '') else ""
                pid          = v[8] if len(v) > 8 else ""
                vid          = v[4] if len(v) > 4 else ""
                village_type = _int(v[2]) if len(v) > 2 else 0
                is_capital   = _int(v[12]) if len(v) > 12 else 0

            else:
                # Layout C: old vid-first — vid, x, y, type_id, tribe, ?, vname, pop, pname, ?, aname
                x            = _int(v[1])
                y            = _int(v[2])
                tribe        = _int(v[4]) if len(v) > 4 else 0
                vname        = v[6] if len(v) > 6 else ""
                pop          = _int(v[7]) if len(v) > 7 else 0
                pname        = v[8] if len(v) > 8 and v[8] not in ('NULL', '') else ""
                aname        = v[10] if len(v) > 10 and v[10] not in ('NULL', '') else ""
                pid          = v[8] if len(v) > 8 else ""
                vid          = v[0]
                village_type = _int(v[3]) if len(v) > 3 else 0
                is_capital   = _int(v[13]) if len(v) > 13 else 0

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
            "alliance_id": "",
            "alliance_name": aname,
            "population": pop,
            "tribe": tribe,
            "is_capital": is_capital,
            "village_type": village_type,
        })
    return villages


async def _fetch_and_save_snapshot(guild_id: str, tw_world: str):
    """Fetch map.sql for a world and save it once (shared across all guilds on that world)."""
    tw_world = tw_world.rstrip("/")
    url = tw_world + "/map.sql"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url)
        r.raise_for_status()
    loop = asyncio.get_event_loop()
    villages = await loop.run_in_executor(None, _parse_map_sql, r.text)
    if not villages:
        return
    # Save ONCE for the world (world_snapshots table — shared across all guilds)
    await database.save_map_snapshot(guild_id, villages)
    # Per-guild post-processing for all guilds on the same world
    try:
        all_guilds = await database.get_all_guilds()
        for g in all_guilds:
            if (g.get("tw_world") or "").rstrip("/") == tw_world:
                try:
                    await database.sync_alliance_members_from_snapshot(g["guild_id"])
                except Exception:
                    pass
                asyncio.create_task(database.run_sector_scan(g["guild_id"]))
    except Exception:
        pass


# ── Snapshot background-task tracker ─────────────────────────────────────────
# Maps guild_id → {"running": bool, "error": str|None, "started_at": float}
_snapshot_status: dict = {}

async def _run_snapshot_background(guild_id: str, tw_world: str):
    """Fire-and-forget wrapper: run snapshot, update _snapshot_status."""
    import time as _time
    _snapshot_status[guild_id] = {"running": True, "error": None, "started_at": _time.time()}
    try:
        await _fetch_and_save_snapshot(guild_id, tw_world)
        _snapshot_status[guild_id]["running"] = False
    except Exception as e:
        _snapshot_status[guild_id]["running"] = False
        _snapshot_status[guild_id]["error"] = str(e)[:120]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    # Backfill missing attacker_discord_id in op_waves using member_troops lookup
    await database.backfill_op_wave_discord_ids()

    async def _snapshot_loop():
        import datetime as _datetime
        # Short delay to let app/DB fully boot
        await asyncio.sleep(15)
        while True:
            try:
                guilds = await database.get_all_guilds()

                # Group guilds by world URL — fetch each world once, write to all guilds on it
                world_map: dict[str, list] = {}
                for g in guilds:
                    tw_world = (g.get("tw_world") or "").strip().rstrip("/")
                    if tw_world:
                        world_map.setdefault(tw_world, []).append(g)

                for tw_world, world_guilds in world_map.items():
                    try:
                        # Check oldest snapshot across all guilds on this world
                        oldest_age = None
                        for g in world_guilds:
                            latest = await database.get_latest_snapshot_time(g["guild_id"])
                            if not latest:
                                oldest_age = 9999  # never fetched
                                break
                            age_min = (_datetime.datetime.utcnow() - _datetime.datetime.fromisoformat(latest)).total_seconds() / 60
                            if oldest_age is None or age_min > oldest_age:
                                oldest_age = age_min

                        if oldest_age is not None and oldest_age < 15:
                            continue  # all guilds on this world are fresh

                        # Fetch map.sql once for this world
                        url = tw_world + "/map.sql"
                        print(f"[scanner] fetching {tw_world} for {len(world_guilds)} guild(s)", flush=True)
                        async with httpx.AsyncClient(timeout=30) as client:
                            r = await client.get(url)
                            r.raise_for_status()
                        loop = asyncio.get_event_loop()
                        villages = await loop.run_in_executor(None, _parse_map_sql, r.text)
                        if not villages:
                            continue

                        # Save snapshot ONCE per world (stored in world_snapshots, shared across guilds)
                        try:
                            await database.save_map_snapshot(world_guilds[0]["guild_id"], villages)
                        except Exception as eg:
                            print(f"[scanner] ERROR saving snapshot for {tw_world}: {eg}", flush=True)

                        # Per-guild post-processing (alliance sync, sector scan)
                        for g in world_guilds:
                            try:
                                await database.sync_alliance_members_from_snapshot(g["guild_id"])
                            except Exception:
                                pass
                            asyncio.create_task(database.run_sector_scan(g["guild_id"]))

                        print(f"[scanner] {tw_world} → saved for {len(world_guilds)} guild(s)", flush=True)

                    except Exception as ew:
                        import traceback
                        print(f"[scanner] ERROR world {tw_world}: {ew}", flush=True)
                        traceback.print_exc()

            except Exception as e:
                print(f"[scanner] loop ERROR: {e}", flush=True)

            await asyncio.sleep(15 * 60)  # check every 15 min

    asyncio.create_task(_snapshot_loop())

    async def _trial_expiry_loop():
        """Every hour: expire trials and log."""
        while True:
            try:
                expired = await database.expire_overdue_trials()
                if expired:
                    print(f"[trials] Expired {len(expired)} guild trial(s): {expired}", flush=True)
                expired_users = await database.expire_overdue_user_trials()
                if expired_users:
                    print(f"[trials] Expired {len(expired_users)} user trial(s): {expired_users}", flush=True)
            except Exception as e:
                print(f"[trials] ERROR: {e}", flush=True)
            await asyncio.sleep(3600)

    asyncio.create_task(_trial_expiry_loop())
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(UserTrackingMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")
Path("/app/data/scout_images").mkdir(parents=True, exist_ok=True)
app.mount("/scout-images", StaticFiles(directory="/app/data/scout_images"), name="scout_images")

from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # API paths always get JSON error responses
    if "/api/" in request.url.path or request.url.path.endswith("/api"):
        return JSONResponse({"error": str(exc.detail) if exc.detail else str(exc.status_code)}, status_code=exc.status_code)
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
POSTHOG_KEY = os.environ.get("POSTHOG_KEY", "")
templates.env.globals["posthog_key"] = POSTHOG_KEY

VIEW_CHANNEL = "1024"  # Discord permission bit

async def _sync_private_channel_permissions(guild_id: str, private_channel_role_ids: str, allowed_role_ids: str):
    """Update all private channels in this guild to match the current role config.

    Roles in private_channel_role_ids (or fallback allowed_role_ids) get view+send access.
    All other role overwrites (that aren't @everyone or the bot) are removed from every
    private channel tracked in the DB.
    """
    token = os.environ.get("DISCORD_TOKEN", "")
    if not token:
        return

    # Determine which role IDs should have access
    role_source = private_channel_role_ids.strip() if private_channel_role_ids.strip() else allowed_role_ids
    granted_role_ids = {r.strip() for r in role_source.split(",") if r.strip()}

    VIEW_SEND = str(int("0x400", 16) | int("0x800", 16))  # VIEW_CHANNEL + SEND_MESSAGES = 3072
    VIEW_CHANNEL_DENY = str(int("0x400", 16))  # 1024

    # Fetch all private channels for this guild
    channels = await database.get_all_private_channels_for_guild(guild_id)
    if not channels:
        return

    async with httpx.AsyncClient(timeout=10) as client:
        for rec in channels:
            channel_id = rec["channel_id"]
            owner_id   = rec["owner_id"]

            # Fetch current channel overwrites to preserve the owner's entry
            try:
                resp = await client.get(
                    f"https://discord.com/api/v10/channels/{channel_id}",
                    headers={"Authorization": f"Bot {token}"},
                )
                if resp.status_code != 200:
                    continue
                channel_data = resp.json()
            except Exception:
                continue

            # Build new overwrites:
            # 1. @everyone → deny view
            # 2. owner (member) → allow view+send
            # 3. each granted role → allow view+send
            # Keep any member-type overwrites that aren't the owner (manually granted users)
            existing_overwrites = channel_data.get("permission_overwrites", [])
            new_overwrites = []

            # Carry over member overwrites (type=1, individual users) unchanged
            for ow in existing_overwrites:
                if ow.get("type") == 1:  # member overwrite
                    new_overwrites.append(ow)

            # @everyone deny
            new_overwrites.append({"id": guild_id, "type": 0, "allow": "0", "deny": VIEW_CHANNEL_DENY})
            # Granted roles allow
            for rid in granted_role_ids:
                new_overwrites.append({"id": rid, "type": 0, "allow": VIEW_SEND, "deny": "0"})

            try:
                await client.patch(
                    f"https://discord.com/api/v10/channels/{channel_id}",
                    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                    json={"permission_overwrites": new_overwrites},
                )
            except Exception:
                pass


async def _sync_archive_permissions(guild_id: str, archive_channel_id: str, allowed_role_ids: str, archive_role_ids: str = "") -> tuple[int, str]:
    """Set archive channel visibility via individual PUT/DELETE per overwrite.
    @everyone gets VIEW_CHANNEL denied. Effective roles get VIEW_CHANNEL allowed.
    Returns (last_status_code, summary_text)."""
    token = os.environ.get("DISCORD_TOKEN", "")
    if not token:
        return 0, "no token"

    # Use dedicated archive roles if set, otherwise fall back to scout roles
    effective_roles = [r.strip() for r in (archive_role_ids.strip() or allowed_role_ids).split(",") if r.strip()]

    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    base = f"https://discord.com/api/v10/channels/{archive_channel_id}/permissions"
    last_status = 204
    last_body = ""

    async with httpx.AsyncClient(timeout=10) as client:
        # 1) Deny @everyone VIEW_CHANNEL
        r = await client.put(
            f"{base}/{guild_id}",
            headers=headers,
            json={"type": 0, "allow": "0", "deny": VIEW_CHANNEL},
        )
        last_status, last_body = r.status_code, r.text

        # 2) Allow each permitted role to view the channel
        for role_id in effective_roles:
            r = await client.put(
                f"{base}/{role_id}",
                headers=headers,
                json={"type": 0, "allow": VIEW_CHANNEL, "deny": "0"},
            )
            if r.status_code not in (200, 204):
                last_status, last_body = r.status_code, r.text

        # 3) Read current overwrites and DELETE any role overwrites NOT in effective_roles
        ch = await client.get(
            f"https://discord.com/api/v10/channels/{archive_channel_id}",
            headers=headers,
        )
        if ch.status_code == 200:
            keep = {guild_id} | set(effective_roles)
            bot_data = await client.get("https://discord.com/api/v10/users/@me", headers=headers)
            if bot_data.status_code == 200:
                keep.add(bot_data.json()["id"])
            for ow in ch.json().get("permission_overwrites", []):
                if ow["id"] not in keep:
                    await client.delete(f"{base}/{ow['id']}", headers=headers)

    return last_status, last_body


async def _sync_scout_channel_permissions(guild_id: str, allowed_role_ids: str):
    """Update all open scout channels to match current allowed_role_ids."""
    token = os.environ.get("DISCORD_TOKEN", "")
    if not token:
        return
    granted = {r.strip() for r in allowed_role_ids.split(",") if r.strip()}
    VIEW_SEND = str(0x400 | 0x800)  # 3072
    VIEW_CHANNEL_DENY = str(0x400)

    channels = await database.get_scout_channels(guild_id)
    if not channels:
        return

    async with httpx.AsyncClient(timeout=10) as client:
        for rec in channels:
            channel_id = rec["channel_id"]
            # Fetch current overwrites to preserve member-level entries
            try:
                resp = await client.get(
                    f"https://discord.com/api/v10/channels/{channel_id}",
                    headers={"Authorization": f"Bot {token}"},
                )
                if resp.status_code != 200:
                    continue
                existing = resp.json().get("permission_overwrites", [])
            except Exception:
                continue

            new_overwrites = [ow for ow in existing if ow.get("type") == 1]  # keep member overwrites
            new_overwrites.append({"id": guild_id, "type": 0, "allow": "0", "deny": VIEW_CHANNEL_DENY})
            for rid in granted:
                new_overwrites.append({"id": rid, "type": 0, "allow": VIEW_SEND, "deny": "0"})

            try:
                await client.patch(
                    f"https://discord.com/api/v10/channels/{channel_id}",
                    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                    json={"permission_overwrites": new_overwrites},
                )
            except Exception:
                pass


async def _sync_defend_channel_permissions(guild_id: str, defend_role_ids: str, allowed_role_ids: str):
    """Update all open defend channels to match current defend_role_ids (fallback: allowed_role_ids)."""
    token = os.environ.get("DISCORD_TOKEN", "")
    if not token:
        return
    role_source = defend_role_ids.strip() if defend_role_ids.strip() else allowed_role_ids
    granted = {r.strip() for r in role_source.split(",") if r.strip()}
    VIEW_SEND = str(0x400 | 0x800)
    VIEW_CHANNEL_DENY = str(0x400)

    channels = await database.get_defend_channels(guild_id)
    if not channels:
        return

    async with httpx.AsyncClient(timeout=10) as client:
        for rec in channels:
            if rec.get("status") == "closed":
                continue
            channel_id = rec["channel_id"]
            try:
                resp = await client.get(
                    f"https://discord.com/api/v10/channels/{channel_id}",
                    headers={"Authorization": f"Bot {token}"},
                )
                if resp.status_code != 200:
                    continue
                existing = resp.json().get("permission_overwrites", [])
            except Exception:
                continue

            new_overwrites = [ow for ow in existing if ow.get("type") == 1]
            new_overwrites.append({"id": guild_id, "type": 0, "allow": "0", "deny": VIEW_CHANNEL_DENY})
            for rid in granted:
                new_overwrites.append({"id": rid, "type": 0, "allow": VIEW_SEND, "deny": "0"})

            try:
                await client.patch(
                    f"https://discord.com/api/v10/channels/{channel_id}",
                    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                    json={"permission_overwrites": new_overwrites},
                )
            except Exception:
                pass


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


def _get_nav_guild(request: Request) -> dict | None:
    """Extract guild_id from current URL path and return basic guild info for navbar."""
    try:
        path = request.url.path
        # Match /guild/{guild_id}/...
        m = re.match(r"^/guild/([^/]+)", path)
        if not m:
            return None
        gid = m.group(1)
        # Try to get from session's guild list (fast path, no DB)
        session = get_session(request)
        if not session:
            return None
        # Return minimal info — full guild data loaded async per route, not here
        # Store guild info in request.state if already loaded by the route
        guild = getattr(request.state, "_nav_guild", None)
        if guild:
            return guild
        return {"guild_id": gid, "guild_name": gid, "tw_world": ""}
    except Exception:
        return None


templates.env.globals["get_nav_guild"] = _get_nav_guild

import json as _json
import datetime as _dt_global
templates.env.filters["from_json"] = lambda s: _json.loads(s) if s else []
templates.env.filters["fmt_duration"] = lambda s: _fmt_duration(int(s or 0))
templates.env.filters["build_seconds"] = lambda troops: _build_seconds(troops or {})
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
    response.set_cookie("oauth_state", state, max_age=300, httponly=True, samesite="lax", secure=True)
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


import hmac as _hmac
import hashlib as _hashlib

def _make_oauth_state(next_url: str = "") -> str:
    """Generate a self-validating HMAC state with optional next_url — no cookie needed."""
    import base64 as _b64
    nonce = secrets.token_hex(20)
    payload = nonce
    if next_url:
        # Encode next_url safely into the state
        encoded = _b64.urlsafe_b64encode(next_url.encode()).decode().rstrip("=")
        payload = f"{nonce}~{encoded}"
    sig = _hmac.new(SECRET_KEY.encode(), payload.encode(), _hashlib.sha256).hexdigest()[:24]
    return f"{payload}.{sig}"

def _verify_oauth_state(state: str) -> tuple[bool, str]:
    """Verify a self-validating HMAC state. Returns (valid, next_url)."""
    import base64 as _b64
    try:
        payload, sig = state.rsplit(".", 1)
        expected = _hmac.new(SECRET_KEY.encode(), payload.encode(), _hashlib.sha256).hexdigest()[:24]
        if not secrets.compare_digest(expected, sig):
            return False, ""
        if "~" in payload:
            _, encoded = payload.split("~", 1)
            # Re-pad base64
            padding = 4 - len(encoded) % 4
            next_url = _b64.urlsafe_b64decode(encoded + "=" * padding).decode()
            # Sanitize: only allow relative paths
            if next_url.startswith("/") and not next_url.startswith("//"):
                return True, next_url
        return True, ""
    except Exception:
        return False, ""


@app.get("/auth/discord")
async def auth_discord(request: Request):
    client_id = get_client_id()
    if not client_id or not DISCORD_CLIENT_SECRET:
        return RedirectResponse("/login?error=Discord+OAuth2+not+configured")
    next_url = request.query_params.get("next", "")
    state = _make_oauth_state(next_url=next_url)
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
    _ip = request.client.host if request.client else ""
    if error or not code:
        await database.log_auth(status="cancelled", ip=_ip, detail=error or "no_code")
        return RedirectResponse("/login?error=Discord+authentication+cancelled")

    # Validate OAuth state (HMAC-signed, no cookie needed)
    state_valid, next_url = _verify_oauth_state(state) if state else (False, "")
    if not state or not state_valid:
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

    # If this user is a dual, merge the anchor's owned/member guilds into their session
    dual_anchor_id = await database.get_dual_anchor(discord_id)
    if dual_anchor_id and session_type != "admin":
        # Guilds where anchor is subscription owner or ally member
        anchor_guilds = await database.get_guild_ids_for_discord_user(dual_anchor_id)
        if anchor_guilds:
            accessible = list(set(accessible) | set(anchor_guilds))

    session_data = {
        "type": session_type,
        "uid": discord_id,
        "username": username,
        "guilds": None if session_type == "admin" else accessible,
        "dual_anchor": dual_anchor_id,  # store for display purposes
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

    redirect_to = next_url if next_url else "/dashboard"
    response = RedirectResponse(redirect_to, status_code=303)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    response.delete_cookie("oauth_state")
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Demo-Login: Magic-Link ohne Discord OAuth
# GET /demo-login?token=DEMO_SECRET_TOKEN
# Loggt als fiktiver Demo-Nutzer ein, der Zugriff auf die Demo-Guild hat.
# ---------------------------------------------------------------------------
DEMO_LOGIN_TOKEN = os.environ.get("DEMO_LOGIN_TOKEN", "")
DEMO_GUILD_ID    = "1509975187276435528"
DEMO_USER_ID     = "999999999999999001"   # fiktive Discord-ID für Demo-User

@app.get("/demo-login")
async def demo_login(request: Request, token: str = ""):
    # Token muss gesetzt und korrekt sein
    if not DEMO_LOGIN_TOKEN or token != DEMO_LOGIN_TOKEN:
        return HTMLResponse("<h2>Ungültiger Demo-Token.</h2>", status_code=403)

    # Session als Demo-Nutzer mit Zugriff auf die Demo-Guild
    session_data = {
        "type": "discord",
        "uid": DEMO_USER_ID,
        "username": "DemoUser",
        "avatar": None,
        "guilds": [DEMO_GUILD_ID],
    }
    session_token = create_session(session_data)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(SESSION_COOKIE, session_token,
                        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return response


# ---------------------------------------------------------------------------
# Routes — dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, flash: str = ""):
    session = get_session(request)
    if not session:
        return RedirectResponse("/login")

    owner_discord_id = session.get("uid", "")
    username = session.get("username", "Spieler")

    # ── Personal workspaces ──
    personal_workspaces = await database.get_personal_workspaces(owner_discord_id) if owner_discord_id else []

    # Auto-create a default personal workspace on first visit if user has no guilds at all
    all_guilds_db = await database.get_all_guilds()
    if session["guilds"] is not None:
        allowed = set(session["guilds"])
        discord_guilds = [g for g in all_guilds_db if g["guild_id"] in allowed and g.get("workspace_type", "discord") == "discord"]
    else:
        discord_guilds = [g for g in all_guilds_db if g.get("workspace_type", "discord") == "discord"]

    if not discord_guilds and not personal_workspaces and owner_discord_id:
        ws_id = await database.get_or_create_default_workspace(owner_discord_id, username)
        return RedirectResponse(f"/guild/{ws_id}/setup?new=1", status_code=303)

    # Auto-skip dashboard for workspace-only users: if they have exactly one workspace
    # and no Discord guilds, go straight into the workspace (dashboard is useless to them)
    if not discord_guilds and len(personal_workspaces) == 1 and not request.query_params.get("select"):
        ws_id = personal_workspaces[0]["guild_id"]
        return RedirectResponse(f"/guild/{ws_id}", status_code=302)

    # Merge: personal workspaces first, then discord guilds
    guilds = personal_workspaces + discord_guilds

    client_id = get_client_id()
    # Signed invite URL — encodes the inviting user so they become workspace owner
    if client_id and owner_discord_id:
        invite_token = signer.dumps({"uid": owner_discord_id}, salt="bot-invite")
        base_url = os.environ.get("BASE_URL", str(request.base_url).rstrip("/"))
        callback_url = f"{base_url}/bot-invite/callback"
        invite_url = (
            f"https://discord.com/oauth2/authorize?client_id={client_id}"
            f"&permissions=805432336&scope=bot+applications.commands"
            f"&redirect_uri={callback_url}&response_type=code&state={invite_token}"
        )
    elif client_id:
        invite_url = (
            f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions=805432336&scope=bot+applications.commands"
        )
    else:
        invite_url = ""

    # Archived workspaces
    archived_workspaces = await database.get_archived_workspaces(owner_discord_id) if owner_discord_id else []

    # Server-slot limits for this user
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
            "user_id": owner_discord_id,
            "slots_used": slots_used,
            "slots_max": slots_max,
            "slots_full": slots_full,
            "flash": flash,
            "archived_workspaces": archived_workspaces,
        },
    )


@app.get("/workspace/new", response_class=HTMLResponse)
async def workspace_new_page(request: Request):
    session, err = _require_session(request)
    if err:
        return err
    return templates.TemplateResponse("workspace_new.html", {"request": request, "session": session})


@app.post("/workspace/create")
async def workspace_create(request: Request, name: str = Form(...)):
    session, err = _require_session(request)
    if err:
        return err
    owner_discord_id = session.get("uid", "")
    if not owner_discord_id:
        return RedirectResponse("/dashboard", status_code=303)
    name = name.strip()[:64] or "Mein Workspace"
    ws_id = await database.create_personal_workspace(owner_discord_id, name)
    return RedirectResponse(f"/guild/{ws_id}/setup?new=1", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Dashboard: remove server
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes — Bot invite callback (invite-as-owner)
# ---------------------------------------------------------------------------

@app.get("/bot-invite/callback")
async def bot_invite_callback(request: Request, guild_id: str = "", state: str = "", code: str = ""):
    """Discord redirects here after bot invite. We use state to identify the inviting user."""
    uid = None
    if state:
        try:
            data = signer.loads(state, salt="bot-invite", max_age=3600)
            uid = data.get("uid")
        except Exception:
            pass

    if not uid:
        # Fallback: if user is logged in, use their session
        session = get_session(request)
        if session:
            uid = session.get("uid")

    if guild_id and uid:
        # Try to fetch real guild name from bot API
        real_name = guild_id  # fallback
        bot_api = os.environ.get("BOT_API_URL", "http://bot:7777")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(f"{bot_api}/api/guild-info", json={"guild_id": guild_id})
                if r.status_code == 200:
                    real_name = r.json().get("name", guild_id)
        except Exception:
            pass

        await database.upsert_guild_name(guild_id, real_name, owner_discord_id=uid)
        import aiosqlite as _aio
        async with _aio.connect(database.DB_PATH) as db:
            await db.execute(
                """UPDATE guild_configs SET workspace_status='active', owner_discord_id=?,
                   guild_name=? WHERE guild_id=?""",
                (uid, real_name, guild_id),
            )
            await db.commit()
        print(f"[bot-invite] Guild {guild_id} ('{real_name}') claimed by {uid}", flush=True)

    return RedirectResponse(f"/dashboard?invited=1&new_guild={guild_id}", status_code=303)


@app.post("/dashboard/rename-guild")
async def dashboard_rename_guild(request: Request, guild_id: str = Form(""), name: str = Form("")):
    """Rename a guild/workspace from the dashboard."""
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    name = name.strip()[:64]
    if not name or not guild_id or not is_valid_guild_id(guild_id):
        return RedirectResponse("/dashboard", status_code=303)
    import aiosqlite as _aio
    async with _aio.connect(database.DB_PATH) as db:
        db.row_factory = _aio.Row
        async with db.execute("SELECT owner_discord_id, workspace_owner_id FROM guild_configs WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
        if not row or (row["owner_discord_id"] != uid and row["workspace_owner_id"] != uid):
            return RedirectResponse("/dashboard", status_code=303)
        await db.execute("UPDATE guild_configs SET guild_name=? WHERE guild_id=?", (name, guild_id))
        await db.commit()
    return RedirectResponse("/dashboard?renamed=1", status_code=303)


@app.post("/dashboard/remove-server")
async def dashboard_remove_server(request: Request, guild_id: str = Form("")):
    """Archive a server/workspace and make the bot leave Discord."""
    session, err = _require_session(request)
    if err: return err
    if not guild_id or not is_valid_guild_id(guild_id):
        return RedirectResponse("/dashboard?error=invalid", status_code=303)

    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    # Also check archived guilds
    if not guild:
        import aiosqlite as _aio
        async with _aio.connect(database.DB_PATH) as db:
            db.row_factory = _aio.Row
            async with db.execute("SELECT * FROM guild_configs WHERE guild_id=?", (guild_id,)) as cur:
                row = await cur.fetchone()
                guild = dict(row) if row else None
    if not guild:
        return RedirectResponse("/dashboard?error=not_found", status_code=303)

    is_personal = guild.get("workspace_type") == "personal"
    owner_field = "workspace_owner_id" if is_personal else "owner_discord_id"
    if guild.get(owner_field) != uid:
        return RedirectResponse("/dashboard?error=not_owner", status_code=303)

    if not is_personal:
        # Tell the bot to leave Discord
        bot_api = os.environ.get("BOT_API_URL", "http://bot:7777")
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(f"{bot_api}/api/leave-guild", json={"guild_id": guild_id})
        except Exception as e:
            print(f"[remove-server] Bot leave error for {guild_id}: {e}", flush=True)

    # Archive instead of delete — data is preserved
    await database.archive_workspace(guild_id)
    return RedirectResponse("/dashboard?removed=1", status_code=303)


@app.post("/dashboard/restore-server")
async def dashboard_restore_server(request: Request, guild_id: str = Form("")):
    """Restore an archived workspace back to active."""
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    import aiosqlite as _aio
    async with _aio.connect(database.DB_PATH) as db:
        db.row_factory = _aio.Row
        async with db.execute("SELECT * FROM guild_configs WHERE guild_id=?", (guild_id,)) as cur:
            row = await cur.fetchone()
            guild = dict(row) if row else None
    if not guild:
        return RedirectResponse("/dashboard?error=not_found", status_code=303)
    is_personal = guild.get("workspace_type") == "personal"
    owner_field = "workspace_owner_id" if is_personal else "owner_discord_id"
    if guild.get(owner_field) != uid:
        return RedirectResponse("/dashboard?error=not_owner", status_code=303)
    await database.restore_workspace(guild_id)
    return RedirectResponse("/dashboard?restored=1", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Trial links
# ---------------------------------------------------------------------------

@app.get("/trial/{code}", response_class=HTMLResponse)
async def activate_trial(request: Request, code: str):
    """Activate a 14-day trial for the current user's default workspace."""
    session, err = _require_session(request)
    if err: return err
    owner_discord_id = session.get("uid", "")
    if not owner_discord_id:
        return RedirectResponse("/login")
    link = await database.get_trial_link(code)
    if not link:
        return templates.TemplateResponse("error.html", {
            "request": request, "error": "Ungültiger oder abgelaufener Trial-Link."
        }, status_code=404)
    if link.get("activated_guild_id"):
        return templates.TemplateResponse("error.html", {
            "request": request, "error": "Dieser Trial-Link wurde bereits eingelöst."
        }, status_code=410)
    username = session.get("username") or session.get("discord_username") or "User"
    guild_id = await database.get_or_create_default_workspace(owner_discord_id, username)
    ok = await database.activate_trial_link(code, guild_id)
    if not ok:
        return templates.TemplateResponse("error.html", {
            "request": request, "error": "Trial konnte nicht aktiviert werden."
        }, status_code=400)
    return RedirectResponse(f"/guild/{guild_id}?trial_activated=1", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Referral system
# ---------------------------------------------------------------------------

@app.get("/ref/{code}")
async def referral_redirect(request: Request, code: str):
    """Store ref code in cookie and redirect to dashboard/login."""
    owner = await database.get_referral_code_owner(code)
    response = RedirectResponse("/dashboard", status_code=302)
    if owner:
        response.set_cookie("_ref", code, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax")
    return response


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """User profile page with referral stats and TravOps-Points."""
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    ref_stats = await database.get_referral_stats(uid) if uid else {"code": "", "points": 0, "referred_count": 0}
    user_sub = await database.get_user_subscription(uid) if uid else None
    dual_info = await database.get_dual_info(uid) if uid else {}
    redeemed = request.query_params.get("redeemed")
    dual_flash = request.query_params.get("dual_flash")
    # Resolve display names for duals
    dual_names: dict[str, str] = {}
    if dual_info.get("duals"):
        for d in dual_info["duals"]:
            dual_names[d["dual_discord_id"]] = d["dual_discord_id"]
    if dual_info.get("anchor_id"):
        dual_names[dual_info["anchor_id"]] = dual_info["anchor_id"]
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "session": session,
        "ref_stats": ref_stats,
        "user_sub": user_sub,
        "dual_info": dual_info,
        "dual_names": dual_names,
        "redeemed": redeemed,
        "dual_flash": dual_flash,
        "base_url": str(request.base_url).rstrip("/"),
    })


@app.post("/profile/redeem")
async def redeem_points(request: Request):
    """Redeem 10 TravOps-Points for 1 month Pro."""
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    ok = await database.redeem_travops_points(uid)
    if ok:
        return RedirectResponse("/profile?redeemed=1", status_code=303)
    return RedirectResponse("/profile?redeemed=0", status_code=303)


@app.post("/profile/dual/link")
async def dual_link(request: Request, code: str = Form(...)):
    """Link current user as a dual using another user's dual code."""
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    result = await database.link_dual(code.strip().upper(), uid)
    if result["ok"]:
        return RedirectResponse("/profile?dual_flash=linked", status_code=303)
    err_map = {
        "invalid_code": "Ungültiger Code.",
        "self_link": "Du kannst dich nicht mit dir selbst verlinken.",
        "already_anchor": "Du hast bereits Duals – du kannst nicht gleichzeitig Dual eines anderen sein.",
        "already_dual": "Du bist bereits als Dual verlinkt.",
        "max_duals": "Dieser Account hat bereits 10 Duals (Maximum).",
        "already_linked": "Diese Verbindung besteht bereits.",
    }
    msg = err_map.get(result.get("error", ""), "Fehler beim Verlinken.")
    return RedirectResponse(f"/profile?dual_flash={result.get('error','error')}", status_code=303)


@app.post("/profile/dual/unlink/{target_id}")
async def dual_unlink(request: Request, target_id: str):
    """Remove a dual link."""
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    await database.unlink_dual(uid, target_id)
    return RedirectResponse("/profile?dual_flash=unlinked", status_code=303)


@app.post("/profile/dual/regenerate")
async def dual_regenerate_code(request: Request):
    """Generate a fresh dual code (invalidates old one for new links)."""
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    import secrets as _sec
    new_code = "D-" + _sec.token_urlsafe(8).upper()[:10]
    async with __import__('aiosqlite').connect(database.DB_PATH) as db:
        await db.execute(
            "INSERT INTO dual_codes (discord_user_id, code, created_at) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET code=excluded.code, created_at=excluded.created_at",
            (uid, new_code),
        )
        await db.commit()
    return RedirectResponse("/profile?dual_flash=code_reset", status_code=303)


# ---------------------------------------------------------------------------
# Routes — user billing (Player Pro, no Discord server required)
# ---------------------------------------------------------------------------

@app.get("/billing", response_class=HTMLResponse)
async def user_billing_page(request: Request):
    """Standalone billing page for Player Pro (personal workspace users)."""
    session, err = _require_session(request)
    if err: return err
    owner_discord_id = session.get("uid", "")
    user_sub = await database.get_user_subscription(owner_discord_id) if owner_discord_id else None
    stripe_configured = bool(STRIPE_SECRET_KEY)
    # Compute trial days remaining
    trial_days_left = None
    if user_sub and user_sub.get("subscription_status") == "trialing" and user_sub.get("expires_at"):
        from datetime import datetime as _dt
        try:
            exp = _dt.fromisoformat(user_sub["expires_at"])
            delta = (exp - _dt.utcnow()).days
            trial_days_left = max(0, delta)
        except Exception:
            pass
    return templates.TemplateResponse("user_billing.html", {
        "request": request,
        "session": session,
        "user_sub": user_sub,
        "stripe_pk": STRIPE_PUBLISHABLE_KEY,
        "stripe_configured": stripe_configured,
        "tier_meta": TIER_META,
        "error": request.query_params.get("error", ""),
        "saved": request.query_params.get("saved", ""),
        "trial_days_left": trial_days_left,
        "free_trial_used": bool((user_sub or {}).get("free_trial_used")),
    })


@app.post("/billing/checkout")
async def user_billing_checkout(
    request: Request,
    plan: str = Form("monthly"),
    tier: str = Form("player_pro"),
):
    """Checkout for Player Pro — attaches to user, not a guild."""
    session, err = _require_session(request)
    if err: return err
    s = _stripe_client()
    if not s:
        return RedirectResponse("/billing?error=stripe_not_configured", status_code=303)
    owner_discord_id = session.get("uid", "")
    if not owner_discord_id:
        return RedirectResponse("/billing?error=not_logged_in", status_code=303)

    if tier not in ("player_pro",):
        tier = "player_pro"
    interval = "monthly" if plan == "monthly" else "annual"
    price_id = STRIPE_PRICES[tier][interval]
    if not price_id:
        return RedirectResponse("/billing?error=price_not_configured", status_code=303)

    user_sub = await database.get_user_subscription(owner_discord_id)
    customer_id = (user_sub or {}).get("stripe_customer_id") or None

    base_url = os.environ.get("BASE_URL", str(request.base_url).rstrip("/"))
    checkout_kwargs = dict(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        allow_promotion_codes=True,
        subscription_data={"trial_period_days": 7},
        success_url=f"{base_url}/billing?saved=1",
        cancel_url=f"{base_url}/billing?error=cancelled",
        metadata={
            "owner_discord_id": owner_discord_id,
            "tier": tier,
            "personal": "1",
            "ref_code": request.cookies.get("_ref", ""),
        },
    )
    if customer_id:
        checkout_kwargs["customer"] = customer_id

    try:
        checkout_session = s.checkout.Session.create(**checkout_kwargs)
    except Exception as e:
        return RedirectResponse(f"/billing?error={str(e)[:80].replace(chr(32), '+')}", status_code=303)
    return RedirectResponse(checkout_session.url, status_code=303)


@app.post("/billing/portal")
async def user_billing_portal(request: Request):
    """Stripe Customer Portal for Player Pro users."""
    session, err = _require_session(request)
    if err: return err
    s = _stripe_client()
    if not s:
        return RedirectResponse("/billing?error=stripe_not_configured", status_code=303)
    owner_discord_id = session.get("uid", "")
    user_sub = await database.get_user_subscription(owner_discord_id) if owner_discord_id else None
    customer_id = (user_sub or {}).get("stripe_customer_id")
    if not customer_id:
        return RedirectResponse("/billing?error=no_subscription", status_code=303)
    base_url = os.environ.get("BASE_URL", str(request.base_url).rstrip("/"))
    portal = s.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{base_url}/billing",
    )
    return RedirectResponse(portal.url, status_code=303)


@app.post("/billing/start-free-trial")
async def user_start_free_trial(request: Request, plan: str = Form("player_pro")):
    """Activate the one-time 30-day free trial for Player Pro (no credit card required)."""
    session, err = _require_session(request)
    if err: return err
    owner_discord_id = session.get("uid", "")
    if not owner_discord_id:
        return RedirectResponse("/billing?error=not_logged_in", status_code=303)
    if plan not in ("player_pro",):
        plan = "player_pro"
    ok = await database.start_user_free_trial(owner_discord_id, plan)
    if not ok:
        return RedirectResponse("/billing?error=trial_already_used", status_code=303)
    return RedirectResponse("/billing?saved=trial_started", status_code=303)


# ---------------------------------------------------------------------------
# Routes — guild
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}", response_class=HTMLResponse)
async def guild_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    is_personal = guild.get("workspace_type") == "personal"
    is_admin = session.get("type") == "admin"
    is_owner = is_guild_owner(session, guild) or (is_personal and guild.get("workspace_owner_id") == session.get("uid"))

    roles = []
    perm_issues = []
    if not is_personal:
        token = os.environ.get("DISCORD_TOKEN", "")
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/roles",
                headers={"Authorization": f"Bot {token}"},
            )
            if r.status_code == 200:
                roles = sorted(r.json(), key=lambda x: -x.get("position", 0))
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    "http://bot:7777/api/check-permissions",
                    json={"guild_id": guild_id},
                )
                if resp.status_code == 200:
                    perm_issues = resp.json().get("issues", [])
        except Exception:
            pass

    request_hub = await database.get_request_hub(guild_id)
    hero_scout_channel = await _get_hero_scout_channel(guild_id)

    # For personal workspaces, check the user's own subscription instead of the guild's
    if is_personal:
        owner_discord_id = session.get("uid", "")
        user_sub = await database.get_user_subscription(owner_discord_id) if owner_discord_id else None
        user_sub_status = (user_sub or {}).get("status", "free")
        # Inject subscription info into guild dict so template logic works unchanged
        guild = dict(guild)
        guild["subscription_status"] = user_sub_status

    # Admin preview mode: override subscription plan/status for UI simulation
    preview_info = None
    preview = session.get("preview") if is_admin else None
    if preview and preview.get("guild_id") == guild_id:
        guild = dict(guild)
        pplan = preview.get("plan", "starter")
        if pplan == "free":
            guild["subscription_status"] = "free"
            guild["subscription_plan"] = ""
        else:
            guild["subscription_status"] = "active"
            guild["subscription_plan"] = pplan
        preview_info = pplan

    uid = session.get("uid", "")
    unread_notif, my_waves, farm_stats, recent_battles, recent_attacks, player_growth = await asyncio.gather(
        database.count_unread_notifications(guild_id, uid),
        database.get_my_op_waves(guild_id, uid),
        database.get_farm_stats(guild_id),
        database.get_battle_reports(guild_id, limit=6),
        database.get_attack_reports(guild_id, limit=6),
        database.get_player_growth(guild_id, limit=8),
    )
    pending_waves = sum(1 for w in my_waves if not w.get("confirm_status") and w.get("send_time"))

    return templates.TemplateResponse(
        "guild.html",
        {"request": request, "guild": guild, "saved": saved, "roles": roles,
         "is_admin": is_admin, "is_owner": is_owner, "request_hub": request_hub,
         "hero_scout_channel": hero_scout_channel,
         "perm_issues": perm_issues,
         "is_personal": is_personal,
         "trial_expires_at": guild.get("trial_expires_at"),
         "preview_plan": preview_info,
         "unread_notif": unread_notif,
         "pending_waves": pending_waves,
         "farm_stats": farm_stats,
         "recent_battles": recent_battles,
         "recent_attacks": recent_attacks,
         "player_growth": player_growth,
         },
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
    err = await _require_alliance(guild, guild_id)
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
    err = await _require_alliance(guild, guild_id)
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
    bot_language: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    category_id = sanitize_snowflake(category_id)
    archive_channel_id = sanitize_snowflake(archive_channel_id)
    scout_channel_id = sanitize_snowflake(scout_channel_id)
    normalized_roles = sanitize_snowflake_list(allowed_role_ids)
    if bot_language not in ("de", "en"):
        bot_language = ""

    await database.update_guild_config(
        guild_id=guild_id,
        category_id=category_id,
        archive_channel_id=archive_channel_id,
        allowed_role_ids=normalized_roles,
        scout_channel_id=scout_channel_id,
        bot_language=bot_language,
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
    if field not in {"allowed_role_ids", "res_manager_role_ids", "private_channel_role_ids", "defend_role_ids", "archive_role_ids"}:
        return JSONResponse({"error": "invalid field"}, status_code=400)
    if not SNOWFLAKE_RE.match(role_id):
        return JSONResponse({"error": "invalid role_id"}, status_code=400)
    added = await database.toggle_role_in_field(guild_id, role_id, field)
    guild_row = await database.get_guild(guild_id)
    allowed_ids = (guild_row or {}).get("allowed_role_ids") or ""
    defend_ids  = (guild_row or {}).get("defend_role_ids") or ""
    priv_ids    = (guild_row or {}).get("private_channel_role_ids") or ""
    archive_ids = (guild_row or {}).get("archive_role_ids") or ""

    archive_sync_status = None
    if field == "allowed_role_ids":
        if guild_row and guild_row.get("archive_channel_id"):
            sc, _ = await _sync_archive_permissions(guild_id, guild_row["archive_channel_id"], allowed_ids, archive_ids)
            archive_sync_status = sc
        asyncio.create_task(_sync_scout_channel_permissions(guild_id, allowed_ids))
        asyncio.create_task(_sync_defend_channel_permissions(guild_id, defend_ids, allowed_ids))

    if field == "archive_role_ids":
        if guild_row and guild_row.get("archive_channel_id"):
            sc, body = await _sync_archive_permissions(guild_id, guild_row["archive_channel_id"], allowed_ids, archive_ids)
            archive_sync_status = sc

    if field == "defend_role_ids":
        asyncio.create_task(_sync_defend_channel_permissions(guild_id, defend_ids, allowed_ids))

    if field in ("allowed_role_ids", "private_channel_role_ids"):
        asyncio.create_task(_sync_private_channel_permissions(guild_id, priv_ids, allowed_ids))

    return JSONResponse({"added": added, "archive_sync": archive_sync_status})


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
    return RedirectResponse(f"/guild/{guild_id}/res-push?flash=status_changed", status_code=303)


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
    """Fix bot permissions on the archive channel and re-sync role visibility."""
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
        # 1) Fix bot's own permissions
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
            return RedirectResponse(f"/guild/{guild_id}/settings?flash=⚠️+Fehler:+bot_perms_{r.status_code}", status_code=303)

    # 2) Re-sync role visibility (@everyone deny + allowed roles allow)
    allowed_ids = guild.get("allowed_role_ids") or ""
    archive_ids = guild.get("archive_role_ids") or ""
    sc, body = await _sync_archive_permissions(guild_id, archive_channel_id, allowed_ids, archive_ids)
    if sc not in (200, 201, 204):
        return RedirectResponse(f"/guild/{guild_id}/settings?flash=⚠️+Bot-Perms+ok,+Rollen-Sync+Fehler+{sc}", status_code=303)

    return RedirectResponse(f"/guild/{guild_id}/settings?flash=✅+Berechtigungen+erfolgreich+repariert", status_code=303)


@app.get("/guild/{guild_id}/stats", response_class=HTMLResponse)
async def guild_stats(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err

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
    """Main res-push board — shows all requests with contribution breakdown."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    show = request.query_params.get("show", "active")
    all_requests = await database.get_res_requests(guild_id)
    contributions = await database.get_res_contributions_per_request(guild_id)

    ACTIVE_STATUSES   = {"pending", "accepted", "hold"}
    DONE_STATUSES     = {"completed", "rejected", "inactive"}
    if show == "active":
        requests = [r for r in all_requests if r.get("status") in ACTIVE_STATUSES]
    elif show == "done":
        requests = [r for r in all_requests if r.get("status") in DONE_STATUSES]
    else:
        requests = all_requests

    total_active = sum(1 for r in all_requests if r.get("status") in ACTIVE_STATUSES)
    total_done   = sum(1 for r in all_requests if r.get("status") in DONE_STATUSES)

    # Pre-compute parsed totals and goals per request for progress bars
    import re as _re

    def _parse_res(s: str) -> int:
        """Parse resource amounts. Resources are always whole numbers.
        Rules:
          - Commas are ALWAYS thousands separators → removed (19,500 → 19500)
          - Spaces between digits → removed (49 140 → 49140)
          - Dots: thousands sep when followed by 3 digits; decimal only with k/m suffix
          - Supports: 500k, 1m, 1.5m, 19.500, 19,500, 49 140, 1 mil ress, 1.500.000
        """
        s = (s or "").strip()
        # Detect suffix (k, m, mil, million) attached to a digit
        suffix = ""
        mult   = 1
        sm = _re.search(r"(\d)\s*(mil(?:lion)?|m(?!\w)|k(?!\w))", s, _re.I)
        if sm:
            suffix = sm.group(2).lower()
            s = s[:sm.start(1) + 1]

        # Remove spaces between digits (49 140 → 49140)
        s = _re.sub(r"(\d)\s+(\d)", r"\1\2", s.strip())
        # Remove all commas — always thousands separators (19,500 → 19500)
        s = s.replace(",", "")
        # Dots: thousands sep if followed by exactly 3 digits
        dot_groups = _re.findall(r"\.(\d+)", s)
        if dot_groups and all(len(g) == 3 for g in dot_groups):
            s = s.replace(".", "")
        # else leave dot as decimal (e.g. 1.5m)

        m = _re.search(r"\d+(?:\.\d+)?", s)
        if not m: return 0
        try:
            num = float(m.group())
        except Exception:
            return 0

        if suffix.startswith("mil") or suffix == "m": mult = 1_000_000
        elif suffix == "k":                           mult = 1_000
        return int(num * mult)

    contribution_totals: dict[str, int] = {}
    for rid, contribs in contributions.items():
        contribution_totals[rid] = sum(_parse_res(c.get("amount", "")) for c in contribs)

    # Parse push_height goals server-side (handles "1 mil ress", "500k", "50000")
    goal_totals: dict[str, int] = {}
    for r in all_requests:
        goal_totals[str(r["id"])] = _parse_res(r.get("push_height", ""))

    return templates.TemplateResponse("res_push_board.html", {
        "request": request,
        "guild": guild,
        "requests": requests,
        "contributions": contributions,
        "contribution_totals": contribution_totals,
        "goal_totals": goal_totals,
        "show": show,
        "total_active": total_active,
        "total_done": total_done,
        "flash": request.query_params.get("flash", ""),
    })


@app.get("/guild/{guild_id}/res-push/settings", response_class=HTMLResponse)
async def res_push_settings_page(request: Request, guild_id: str, saved: str = ""):
    """Res-push configuration page (channel IDs, roles)."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    res_requests = await database.get_res_requests(guild_id)
    return templates.TemplateResponse("res_push.html", {
        "request": request, "guild": guild,
        "res_requests": res_requests, "saved": saved,
    })


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
    return RedirectResponse(f"/guild/{guild_id}/res-push/settings?saved=1", status_code=303)


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
        return RedirectResponse(f"/guild/{guild_id}/res-push?flash=status_changed", status_code=303)
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
    return RedirectResponse(f"/guild/{guild_id}/res-push?flash=status_changed", status_code=303)


@app.get("/guild/{guild_id}/res-push/stats", response_class=HTMLResponse)
async def res_push_stats(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    stats = await database.get_res_stats(guild_id)
    return templates.TemplateResponse("res_push_stats.html", {"request": request, "guild": guild, "stats": stats})


async def _call_res_archive(guild_id: str, channel_id: str) -> str:
    """Call bot to archive a res-push channel. Returns 'archived'|'no_channel'|'err:...'"""
    if not channel_id:
        return "no_channel"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post("http://bot:7777/api/archive-res-push-channel",
                                  json={"guild_id": guild_id, "channel_id": channel_id})
            d = r.json()
            return "archived" if d.get("ok") else f"err:{d.get('error','?')[:60]}"
    except Exception as e:
        return f"err:{str(e)[:60]}"


async def _call_res_unarchive(guild_id: str, channel_id: str, requester_id: str = "") -> str:
    if not channel_id:
        return "no_channel"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post("http://bot:7777/api/unarchive-res-push-channel",
                                  json={"guild_id": guild_id, "channel_id": channel_id,
                                        "requester_id": requester_id})
            d = r.json()
            return "unarchived" if d.get("ok") else f"err:{d.get('error','?')[:60]}"
    except Exception as e:
        return f"err:{str(e)[:60]}"


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/inactive")
async def res_request_inactive(request: Request, guild_id: str, request_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    req = await database.get_res_request_by_id_web(request_id)
    if not req or req.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/res-push", status_code=303)
    await database.set_res_request_status_by_id(request_id, "inactive")
    bot = await _call_res_archive(guild_id, req.get("push_channel_id") or "")
    from urllib.parse import quote
    return RedirectResponse(f"/guild/{guild_id}/res-push?flash=status_changed&bot={quote(bot)}", status_code=303)


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/archive")
async def res_request_archive(request: Request, guild_id: str, request_id: int):
    """Explicit archive: move to Archive-Pushes without changing status."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    req = await database.get_res_request_by_id_web(request_id)
    if not req or req.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/res-push", status_code=303)
    await database.set_res_request_status_by_id(request_id, "inactive")
    bot = await _call_res_archive(guild_id, req.get("push_channel_id") or "")
    from urllib.parse import quote
    return RedirectResponse(f"/guild/{guild_id}/res-push?flash=status_changed&bot={quote(bot)}", status_code=303)


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/activate")
async def res_request_activate(request: Request, guild_id: str, request_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    req = await database.get_res_request_by_id_web(request_id)
    if not req or req.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/res-push", status_code=303)
    await database.set_res_request_status_by_id(request_id, "accepted")
    bot = await _call_res_unarchive(guild_id, req.get("push_channel_id") or "", req.get("user_id",""))
    from urllib.parse import quote
    return RedirectResponse(f"/guild/{guild_id}/res-push?flash=status_changed&bot={quote(bot)}", status_code=303)


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


@app.get("/guild/{guild_id}/scout/report/{report_id}/card")
async def scout_share_card(request: Request, guild_id: str, report_id: int):
    """Return a PNG share card for a single scout report."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    report = await database.get_scout_report_by_id(report_id, guild_id)
    if not report:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        from scout_card import generate_scout_card
        png_bytes = generate_scout_card(report)
        from fastapi.responses import Response as _Resp
        return _Resp(content=png_bytes, media_type="image/png",
                     headers={"Content-Disposition": f'attachment; filename="scout_{report_id}.png"'})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
    import json as _json
    # Pre-load roles + battle groups for label resolution
    _ally_group_pre = await database.get_ally_group_for_guild(guild_id)
    _roles_map = {}
    _bgroups_map = {}
    if _ally_group_pre:
        for r in await database.get_ally_roles(_ally_group_pre["id"]):
            _roles_map[r["id"]] = r
        for bg in await database.get_battle_groups(_ally_group_pre["id"]):
            _bgroups_map[bg["id"]] = bg
    for p in polls:
        responses = await database.get_poll_responses(p["id"])
        p["count_available"]   = sum(1 for r in responses if r["response"] == "available")
        p["count_maybe"]       = sum(1 for r in responses if r["response"] == "maybe")
        p["count_unavailable"] = sum(1 for r in responses if r["response"] == "unavailable")
        p["responses"]         = responses
        try:
            p["target_ids_list"] = _json.loads(p.get("target_ids") or "[]")
        except Exception:
            p["target_ids_list"] = []
        # Resolve target labels
        tt = p.get("target_type", "all")
        tids = p["target_ids_list"]
        if tt == "role":
            p["target_labels"] = [_roles_map[i]["role_name"] for i in tids if i in _roles_map]
        elif tt == "wing":
            wing_n = {1: _ally_group_pre.get("wing1_name") or "Wing 1", 2: _ally_group_pre.get("wing2_name") or "Wing 2"} if _ally_group_pre else {}
            p["target_labels"] = [wing_n.get(i, f"Wing {i}") for i in tids]
        elif tt == "battlegroup":
            p["target_labels"] = [_bgroups_map[i]["name"] for i in tids if i in _bgroups_map]
        else:
            p["target_labels"] = []
    is_admin = session.get("type") == "admin"
    can_manage = is_admin or await has_perm(request, guild_id, "poll_manage")
    can_view   = can_manage or await has_perm(request, guild_id, "poll_view")
    # Load ally group + roles for targeting
    ally_group  = await database.get_ally_group_for_guild(guild_id)
    ally_roles  = await database.get_ally_roles(ally_group["id"]) if ally_group else []
    ally_members = await database.get_ally_members(ally_group["id"]) if ally_group else []
    wings = sorted({m["wing"] for m in ally_members if m.get("wing") is not None})
    wing_names = {}
    if ally_group:
        wing_names = {
            1: ally_group.get("wing1_name") or "Wing 1",
            2: ally_group.get("wing2_name") or "Wing 2",
        }
    battle_groups = await database.get_battle_groups(ally_group["id"]) if ally_group else []
    return templates.TemplateResponse("polls.html", {
        "request": request, "guild": guild, "polls": polls, "saved": saved,
        "is_admin": is_admin, "can_manage": can_manage, "can_view": can_view,
        "ally_roles": ally_roles, "ally_group": ally_group,
        "wings": wings, "wing_names": wing_names,
        "battle_groups": battle_groups,
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
    target_type: str = Form("all"),
    target_ids: str = Form("[]"),
    poll_type: str = Form("availability"),
    event_end_datetime: str = Form(""),
):
    import json as _json
    # Poll type definitions
    POLL_TYPES = {
        "availability": [("available","✅ Going",3),("maybe","⏰ Maybe",1),("unavailable","❌ Not going",4)],
        "offensive":    [("available","⚔️ Ready to attack",4),("maybe","🛡️ Support only",1),("unavailable","❌ Not available",2)],
        "farming":      [("available","🌾 I'll farm",3),("maybe","⏳ Maybe",1),("unavailable","⛔ Skipping",2)],
        "interest":     [("available","👍 Interested",3),("maybe","🤔 Unsure",1),("unavailable","👎 Not interested",4)],
        "donation":     [("available","💰 Can donate",3),("maybe","📦 Partial",1),("unavailable","❌ Can't donate",4)],
        "yesno":        [("available","✅ Yes",3),("maybe","🤷 Abstain",2),("unavailable","❌ No",4)],
    }
    type_opts = POLL_TYPES.get(poll_type, POLL_TYPES["availability"])
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)

    title       = title.strip()[:100]
    description = description.strip()[:500]
    try:
        target_ids_list = _json.loads(target_ids)
        if not isinstance(target_ids_list, list):
            target_ids_list = []
    except Exception:
        target_ids_list = []
    target_ids_json = _json.dumps(target_ids_list)

    poll_id = await database.create_poll_targeted(
        guild_id, title, description, event_datetime,
        target_type=target_type, target_ids=target_ids_json, poll_type=poll_type,
        event_end_datetime=event_end_datetime.strip() or None,
    )

    token = os.environ.get("DISCORD_TOKEN", "")
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}

    is_private = target_type != "all"
    # Members to @mention (role/wing filtered)
    target_members = []
    if is_private:
        target_members = await database.get_poll_target_members(guild_id, target_type, target_ids_json)
    all_ally_members = []  # no longer needed

    # Build embed
    target_label = "All"
    if is_private and target_members:
        target_label = f"{len(target_members)} members pinged"
    time_value = event_datetime.replace("T", " ")
    if event_end_datetime:
        time_value += f" → {event_end_datetime.replace('T', ' ')}"
    embed = {
        "title": f"📅 {title}",
        "description": description or "",
        "color": 0x6366f1 if is_private else 0x58b9e0,
        "fields": [
            {"name": "🕐 Date & Time", "value": time_value, "inline": True},
            {"name": "👥 Target Group", "value": target_label, "inline": True},
        ],
        "footer": {"text": f"Poll #{poll_id} · Click a button to indicate your availability"},
    }
    components = [{"type": 1, "components": [
        *[{"type": 2, "style": s, "label": lbl, "custom_id": f"poll_{key}_{poll_id}"} for key, lbl, s in type_opts],
    ]}]

    # Route: private polls → #polls (hidden), public → #polls-public
    if not guild:
        return RedirectResponse(f"/guild/{guild_id}/polls?error=no_channel", status_code=303)

    # Verify channels still exist in Discord, clear stale IDs
    async with httpx.AsyncClient() as _vc:
        for _field, _col in [("poll_channel_id", "poll_channel_id"), ("poll_public_channel_id", "poll_public_channel_id")]:
            _cid = guild.get(_field)
            if _cid:
                _r = await _vc.get(f"https://discord.com/api/v10/channels/{_cid}", headers=headers)
                if _r.status_code == 404:
                    print(f"[polls] Channel {_cid} ({_field}) gone, clearing", flush=True)
                    if _field == "poll_channel_id":
                        await database.update_poll_channel(guild_id, "")
                    else:
                        await database.update_poll_channel(guild_id, guild.get("poll_channel_id") or "", "")
                    guild = await database.get_guild(guild_id)

    # Auto-create channels if missing
    if not guild.get("poll_channel_id") or not guild.get("poll_public_channel_id"):
        async with httpx.AsyncClient() as _c:
            _me = await _c.get("https://discord.com/api/v10/users/@me", headers=headers)
            _bot_id = _me.json().get("id") if _me.status_code == 200 else None
            _ALLOW_BOT = str(0x400 | 0x800 | 0x4000 | 0x8000 | 0x10000000)
            _DENY_VIEW = str(0x400)

            # Find hub category position for placement
            _hub_pos = 0
            if guild.get("category_id"):
                _cat_r = await _c.get(f"https://discord.com/api/v10/channels/{guild['category_id']}", headers=headers)
                if _cat_r.status_code == 200:
                    _hub_pos = _cat_r.json().get("position", 0)

            # Find existing Polls category + channels or create them
            _cat_id = _priv_id = _pub_id = None
            _guild_chs = await _c.get(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers)
            if _guild_chs.status_code == 200:
                _all_chs = _guild_chs.json()
                for _ch in _all_chs:
                    if _ch.get("type") == 4 and _ch.get("name","").lower() == "polls":
                        _cat_id = _ch["id"]
                if _cat_id:
                    for _ch in _all_chs:
                        if _ch.get("parent_id") == _cat_id and _ch.get("type") == 0:
                            if _ch.get("name","").lower() == "polls":
                                _priv_id = _ch["id"]
                            elif _ch.get("name","").lower() == "polls-public":
                                _pub_id = _ch["id"]
            if not _cat_id:
                _cat = await _c.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                    headers=headers, json={"name": "Polls", "type": 4, "position": _hub_pos + 1})
                _cat_id = _cat.json().get("id") if _cat.status_code in (200,201) else None
            # Save found channel IDs to DB immediately
            if _priv_id and not guild.get("poll_channel_id"):
                await database.update_poll_channel(guild_id, _priv_id)
            if _pub_id and not guild.get("poll_public_channel_id"):
                await database.update_poll_channel(guild_id, guild.get("poll_channel_id") or _priv_id or "", _pub_id)

            if not (guild.get("poll_channel_id") or _priv_id) and _cat_id:
                # polls: visible read-only for everyone (needed so bot can add thread members)
                _priv_ow = [{"id": guild_id, "type": 0, "allow": str(0x400), "deny": str(0x800)}]
                if _bot_id: _priv_ow.append({"id": _bot_id, "type": 1, "allow": _ALLOW_BOT, "deny": "0"})
                _pr = await _c.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                    headers=headers, json={"name": "polls", "type": 0, "parent_id": _cat_id,
                                          "permission_overwrites": _priv_ow})
                if _pr.status_code in (200,201):
                    await database.update_poll_channel(guild_id, _pr.json()["id"])

            if not (guild.get("poll_public_channel_id") or _pub_id) and _cat_id:
                _pub_ow = [{"id": guild_id, "type": 0, "allow": str(0x400), "deny": str(0x800)}]
                if _bot_id: _pub_ow.append({"id": _bot_id, "type": 1, "allow": _ALLOW_BOT, "deny": "0"})
                _pb = await _c.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                    headers=headers, json={"name": "polls-public", "type": 0, "parent_id": _cat_id,
                                          "permission_overwrites": _pub_ow})
                if _pb.status_code in (200,201):
                    await database.update_poll_channel(guild_id, guild.get("poll_channel_id") or "", _pb.json()["id"])

        # Reload guild config after auto-setup
        guild = await database.get_guild(guild_id)

    if is_private:
        channel_id = guild.get("poll_channel_id") if guild else None
    else:
        channel_id = (guild.get("poll_public_channel_id") or guild.get("poll_channel_id")) if guild else None

    if not channel_id:
        return RedirectResponse(f"/guild/{guild_id}/polls?error=no_channel", status_code=303)

    thread_id = None

    async with httpx.AsyncClient() as client:
        if is_private and target_members:
            # Create a private thread in the poll channel
            thread_name = title[:100]
            tr = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/threads",
                headers=headers,
                json={"name": thread_name, "type": 12, "invitable": False},  # 12 = GUILD_PRIVATE_THREAD
            )
            if tr.status_code in (200, 201):
                thread_id = tr.json()["id"]
                # Only invite the targeted role/wing members
                invite_ids = [m["discord_id"] for m in target_members if m.get("discord_id")]
                if invite_ids:
                    await database.queue_thread_invites(thread_id, guild_id, invite_ids)
                mentions = " ".join(f"<@{m['discord_id']}>" for m in target_members[:50] if m.get("discord_id"))
                content = ("📊 New poll! Please vote 👇\n" + mentions) if mentions else "📊 New poll!"
                resp = await client.post(
                    f"https://discord.com/api/v10/channels/{thread_id}/messages",
                    headers=headers,
                    json={"content": content, "embeds": [embed], "components": components},
                )
            else:
                print(f"[polls] Private thread creation failed {tr.status_code}: {tr.text}", flush=True)
                # Fallback: post to channel normally
                resp = await client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers=headers,
                    json={"embeds": [embed], "components": components},
                )
        else:
            resp = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=headers,
                json={"embeds": [embed], "components": components},
            )

    if resp.status_code in (200, 201):
        await database.set_poll_thread(poll_id, channel_id, thread_id, resp.json()["id"])
        return RedirectResponse(f"/guild/{guild_id}/polls?saved=1", status_code=303)
    print(f"[polls] Discord error {resp.status_code}: {resp.text}", flush=True)
    return RedirectResponse(f"/guild/{guild_id}/polls?error=discord_{resp.status_code}", status_code=303)


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
    # Get bot user id for overwrite
    async with httpx.AsyncClient() as client:
        me = await client.get("https://discord.com/api/v10/users/@me", headers=headers)
        bot_id = me.json().get("id") if me.status_code == 200 else None
        ALLOW_BOT = str(0x400 | 0x800 | 0x4000 | 0x8000 | 0x10000000)  # view+send+embed+attach+manage_threads
        DENY_ALL  = str(0x400)  # deny VIEW_CHANNEL for @everyone

        # Category
        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers, json={"name": "Polls", "type": 4})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/polls?error=category_{r.status_code}", status_code=303)
        category_id = r.json()["id"]

        # #polls — read-only for everyone (so bot can add thread members), no sending
        private_overwrites = [{"id": guild_id, "type": 0, "allow": str(0x400), "deny": str(0x800)}]
        if bot_id:
            private_overwrites.append({"id": bot_id, "type": 1, "allow": ALLOW_BOT, "deny": "0"})
        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers, json={"name": "polls", "type": 0, "parent_id": category_id,
                                   "permission_overwrites": private_overwrites})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/polls?error=channel_{r.status_code}", status_code=303)
        poll_channel_id = r.json()["id"]

        # #polls-public — read-only for @everyone (no send), bot can send
        public_overwrites = [{"id": guild_id, "type": 0, "allow": str(0x400), "deny": str(0x800)}]  # view yes, send no
        if bot_id:
            public_overwrites.append({"id": bot_id, "type": 1, "allow": ALLOW_BOT, "deny": "0"})
        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers, json={"name": "polls-public", "type": 0, "parent_id": category_id,
                                   "permission_overwrites": public_overwrites})
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}/polls?error=public_ch_{r.status_code}", status_code=303)
        poll_public_channel_id = r.json()["id"]

    await database.update_poll_channel(guild_id, poll_channel_id, poll_public_channel_id)
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
    if not (session.get("type") == "admin" or await has_perm(request, guild_id, "poll_manage")):
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
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
    if not (session.get("type") == "admin" or await has_perm(request, guild_id, "poll_manage")):
        return RedirectResponse(f"/guild/{guild_id}/polls", status_code=303)
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
    # If not logged in → Discord auth with redirect back to this URL (preserving share params)
    session = _get_session(request)
    if not session:
        next_url = str(request.url.path)
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(f"/auth/discord?next={next_url}", status_code=303)

    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    # Check if this is a shared-link access (has share params) vs normal member access
    is_share_link = bool(request.query_params.get("cx") or request.query_params.get("v"))
    is_member = can_access_guild(session, guild_id) or await can_access_guild_async(session, guild_id)

    if not is_member and not is_share_link:
        return RedirectResponse("/dashboard", status_code=303)

    # Map is free for all members — no premium gate
    is_admin = session.get("type") == "admin"
    scouted = await database.get_scouted_coordinates(guild_id)

    # Ally-specific data only for actual guild members — hide from external share viewers
    ally_group = await database.get_ally_group_for_guild(guild_id) if is_member else None

    # Meta-alliances (quick-filter buttons) are a premium feature
    has_premium = _has_alliance_pro(await _enrich_guild_subscription(guild)) if guild else False
    meta_alliances = await database.get_meta_alliances(guild_id) if (is_member and has_premium) else []
    meta_groups    = await database.get_meta_groups(guild_id)    if (is_member and has_premium) else []

    return templates.TemplateResponse("map.html", {
        "request": request,
        "guild": guild,
        "scouted": scouted,
        "is_admin": is_admin,
        "ally_group": ally_group,
        "meta_alliances": meta_alliances,
        "meta_groups": meta_groups,
        "is_share_viewer": not is_member,
        "has_meta_premium": has_premium,
    })


@app.post("/guild/{guild_id}/map/share")
async def map_create_share(request: Request, guild_id: str):
    """Save map state and return a short share ID (member-only or public)."""
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    import json as _json
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    is_public = bool(body.pop("_public", False))
    state_json = _json.dumps(body)
    short_id = await database.create_map_share(
        guild_id, state_json, created_by=session.get("username", ""), is_public=is_public
    )
    base = str(request.base_url).rstrip("/")
    if is_public:
        url = f"{base}/map/open/{short_id}"
    else:
        url = f"{base}/guild/{guild_id}/map/s/{short_id}"
    return JSONResponse({"short_id": short_id, "url": url, "is_public": is_public})


@app.get("/guild/{guild_id}/map/presets")
async def map_presets_list(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    presets = await database.get_map_presets(guild_id)
    return JSONResponse({"presets": presets})


@app.post("/guild/{guild_id}/map/presets")
async def map_presets_save(request: Request, guild_id: str):
    import json as _json
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    state = body.get("state", {})
    preset_json_str = _json.dumps(state)
    new_id = await database.save_map_preset(
        guild_id, name, session.get("username", ""), preset_json_str
    )
    return JSONResponse({"id": new_id, "name": name})


@app.post("/guild/{guild_id}/map/presets/{preset_id}/delete")
async def map_presets_delete(request: Request, guild_id: str, preset_id: int):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    ok = await database.delete_map_preset(guild_id, preset_id)
    return JSONResponse({"ok": ok})


@app.post("/guild/{guild_id}/map/presets/{preset_id}/rename")
async def map_presets_rename(request: Request, guild_id: str, preset_id: int):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    ok = await database.update_map_preset_name(guild_id, preset_id, name)
    return JSONResponse({"ok": ok})


@app.get("/guild/{guild_id}/map/s/{short_id}", response_class=HTMLResponse)
async def map_share_view(request: Request, guild_id: str, short_id: str):
    """Load a shared map state and render the map with it embedded."""
    share = await database.get_map_share(short_id)
    if not share or share["guild_id"] != guild_id:
        return HTMLResponse("<h2>Link ungültig oder abgelaufen.</h2>", status_code=404)

    # Auth: require login, but allow non-members (like normal share links)
    session = _get_session(request)
    if not session:
        return RedirectResponse(f"/auth/discord?next=/guild/{guild_id}/map/s/{short_id}", status_code=303)

    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    is_member = can_access_guild(session, guild_id) or await can_access_guild_async(session, guild_id)
    is_admin  = session.get("type") == "admin"
    scouted   = await database.get_scouted_coordinates(guild_id)
    ally_group     = await database.get_ally_group_for_guild(guild_id) if is_member else None
    meta_alliances = await database.get_meta_alliances(guild_id) if is_member else []
    meta_groups    = await database.get_meta_groups(guild_id) if is_member else []

    return templates.TemplateResponse("map.html", {
        "request": request,
        "guild": guild,
        "scouted": scouted,
        "is_admin": is_admin,
        "ally_group": ally_group,
        "meta_alliances": meta_alliances,
        "meta_groups": meta_groups,
        "is_share_viewer": not is_member,
        "share_state_json": share["state_json"],  # embedded in template
    })


@app.get("/map/open/{short_id}", response_class=HTMLResponse)
async def map_public_view(request: Request, short_id: str):
    """Public read-only map — no auth required. Only works for links created with is_public=True."""
    share = await database.get_map_share(short_id)
    if not share or not share.get("is_public"):
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:2rem;color:#ccc;background:#0f172a;'>"
            "<h2>🔒 This link is not publicly accessible.</h2>"
            "<p>Ask the creator to share a public link.</p></body></html>",
            status_code=403,
        )
    guild = await database.get_guild(share["guild_id"])
    if not guild:
        return HTMLResponse("<h2>Not found.</h2>", status_code=404)

    scouted = await database.get_scouted_coordinates(share["guild_id"])
    return templates.TemplateResponse("map_public.html", {
        "request":          request,
        "guild":            guild,
        "scouted":          scouted,
        "share_state_json": share["state_json"],
        "public_token":     short_id,
    })


@app.get("/guild/{guild_id}/map/world-settings", response_class=HTMLResponse)
async def guild_world_settings_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    saved = request.query_params.get("saved")
    return templates.TemplateResponse("world_settings.html", {
        "request": request, "guild": guild, "session": session, "saved": saved,
    })


@app.post("/guild/{guild_id}/map/world")
async def guild_map_set_world(request: Request, guild_id: str, server_url: str = Form(""), next_url: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    url = server_url.strip().rstrip("/")
    if url and not re.match(r"^https://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", url):
        return RedirectResponse(f"/guild/{guild_id}/map?error=invalid_url", status_code=303)
    await database.update_tw_world(guild_id, url)
    # Auto-kick snapshot in background when world URL is saved
    if url:
        asyncio.create_task(_run_snapshot_background(guild_id, url))
    if next_url:
        return RedirectResponse(next_url, status_code=303)
    return RedirectResponse(f"/guild/{guild_id}/map/world-settings?loading=1", status_code=303)


@app.post("/guild/{guild_id}/map/trigger-snapshot")
async def guild_map_trigger_snapshot(request: Request, guild_id: str, next_url: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    tw_world = (guild.get("tw_world") or "").strip() if guild else ""
    if tw_world:
        status = _snapshot_status.get(guild_id, {})
        if not status.get("running"):
            asyncio.create_task(_run_snapshot_background(guild_id, tw_world))
    # Redirect immediately — client polls /api/snapshot-status/:guild_id
    redirect_to = next_url or f"/guild/{guild_id}/map/world-settings?loading=1"
    return RedirectResponse(redirect_to, status_code=303)


@app.get("/api/snapshot-status/{guild_id}")
async def api_snapshot_status(request: Request, guild_id: str):
    """Poll endpoint — returns current snapshot loading state for a guild."""
    session = _get_session(request)
    if not session or not (can_access_guild(session, guild_id) or await can_access_guild_async(session, guild_id)):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    import time as _time
    st = _snapshot_status.get(guild_id, {})
    last_snap = await database.get_latest_snapshot_time(guild_id)
    return JSONResponse({
        "running":    st.get("running", False),
        "error":      st.get("error"),
        "started_at": st.get("started_at"),
        "elapsed":    round(_time.time() - st["started_at"], 1) if st.get("started_at") else None,
        "has_data":   bool(last_snap),
        "last_snapshot": last_snap,
    })


@app.post("/guild/{guild_id}/map/world-timezone")
async def guild_map_set_timezone(request: Request, guild_id: str,
                                  server_utc_offset: int = Form(60)):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    offset = max(-720, min(840, server_utc_offset))  # clamp to valid UTC range
    await database.update_guild_config_fields(guild_id, server_utc_offset=offset)
    return RedirectResponse(f"/guild/{guild_id}/map/world-settings?saved=1", status_code=303)


@app.get("/guild/{guild_id}/map/sector-monitor", response_class=HTMLResponse)
async def sector_monitor_page(request: Request, guild_id: str):
    """Show sector monitor config + alert list."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    has_player_pro = _has_player_pro(guild)
    monitor = await database.get_sector_monitor(guild_id) or {} if has_player_pro else {}
    alerts = await database.get_sector_alerts(guild_id, include_dismissed=False, limit=100) if has_player_pro else []
    meta_groups = await database.get_meta_groups(guild_id) if has_player_pro else []
    available_alliances = await database.get_alliance_names_from_snapshot(guild_id) if has_player_pro else []
    # Parse stored monitored_alliances into a set for template
    monitored_set = set()
    if monitor:
        raw = str(monitor.get("monitored_alliances") or "")
        monitored_set = {a.strip() for a in raw.split(",") if a.strip()}
    # Decode extra JSON in alerts
    import json as _json
    for a in alerts:
        if isinstance(a.get("extra"), str):
            try: a["extra"] = _json.loads(a["extra"])
            except Exception: a["extra"] = {}
        elif not isinstance(a.get("extra"), dict):
            a["extra"] = {}
    scanned = request.query_params.get("scanned")
    new_count = int(request.query_params.get("new_count", 0))
    # Count stats
    stats = {
        "total": len(alerts),
        "new_village": sum(1 for a in alerts if a["alert_type"] == "new_village"),
        "nobling": sum(1 for a in alerts if a["alert_type"] == "nobling"),
        "fast_growth": sum(1 for a in alerts if a["alert_type"] == "fast_growth"),
    }
    billing_url = _billing_url(guild, guild_id, "premium_required")
    stats["capital_change"] = sum(1 for a in alerts if a["alert_type"] == "capital_change")
    return templates.TemplateResponse("sector_monitor.html", {
        "request": request,
        "guild": guild,
        "has_player_pro": has_player_pro,
        "billing_url": billing_url,
        "monitor": monitor,
        "alerts": alerts,
        "meta_groups": meta_groups,
        "available_alliances": available_alliances,
        "monitored_set": monitored_set,
        "stats": stats,
        "scanned": scanned,
        "new_count": new_count,
    })


@app.post("/guild/{guild_id}/map/sector-monitor/save")
async def sector_monitor_save(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    form = await request.form()
    def _fi(k, d):
        try: return int(form.get(k, d))
        except: return d
    # Collect multi-value monitored_alliances checkboxes
    monitored_alliances = ",".join(form.getlist("monitored_alliances"))
    await database.upsert_sector_monitor(
        guild_id,
        enabled              = _fi("enabled", 0),
        x1=_fi("x1",-50), y1=_fi("y1",-50), x2=_fi("x2",50), y2=_fi("y2",50),
        watch_new_village    = _fi("watch_new_village", 0),
        watch_nobling        = _fi("watch_nobling", 0),
        watch_fast_growth    = _fi("watch_fast_growth", 0),
        watch_capital_change = _fi("watch_capital_change", 0),
        growth_threshold     = _fi("growth_threshold", 200),
        nobling_threshold    = _fi("nobling_threshold", 500),
        sectors              = str(form.get("sectors", "")),
        monitored_alliances  = monitored_alliances,
    )
    return RedirectResponse(f"/guild/{guild_id}/map/sector-monitor?saved=1", status_code=303)


@app.post("/guild/{guild_id}/map/sector-monitor/scan")
async def sector_monitor_scan(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    new_alerts = await database.run_sector_scan(guild_id)
    return RedirectResponse(
        f"/guild/{guild_id}/map/sector-monitor?scanned=1&new_count={len(new_alerts)}",
        status_code=303,
    )


@app.post("/guild/{guild_id}/map/sector-monitor/dismiss/{alert_id}")
async def sector_monitor_dismiss(request: Request, guild_id: str, alert_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.dismiss_sector_alert(guild_id, alert_id)
    return RedirectResponse(f"/guild/{guild_id}/map/sector-monitor", status_code=303)


@app.post("/guild/{guild_id}/map/sector-monitor/dismiss-all")
async def sector_monitor_dismiss_all(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.dismiss_all_sector_alerts(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/map/sector-monitor", status_code=303)


@app.get("/guild/{guild_id}/map/data")
async def guild_map_data(request: Request, guild_id: str, public_token: str = ""):
    """Proxy Travian map.sql to avoid CORS issues. Allow logged-in share viewers and public token holders."""
    session = _get_session(request)
    # Allow unauthenticated access via valid public share token
    if not session:
        if public_token:
            share = await database.get_map_share(public_token)
            if not share or not share.get("is_public") or share["guild_id"] != guild_id:
                return JSONResponse({"error": "unauthorized"}, status_code=403)
            # Valid public token — continue without session
        else:
            return JSONResponse({"error": "unauthorized"}, status_code=403)
    # Allow access if guild member OR share viewer (session may be None for public token)
    if session and not can_access_guild(session, guild_id):
        if not await can_access_guild_async(session, guild_id):
            pass  # share viewer — still allow map data, just no ally context
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


_map_player_cache: dict = {}  # guild_id → {ts, players: [{name, alliance, villages:[{name,x,y,pop}]}]}
_MAP_PLAYER_CACHE_TTL = 600   # 10 minutes

@app.get("/guild/{guild_id}/map/player-search")
async def map_player_search(request: Request, guild_id: str, q: str = ""):
    """Return players + villages matching query, parsed from map.sql (cached 10 min)."""
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = await _require_guild_async(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)

    import time as _time
    q = q.strip().lower()
    if not q or len(q) < 2:
        return JSONResponse({"players": []})

    cache = _map_player_cache.get(guild_id)
    if not cache or (_time.time() - cache["ts"]) > _MAP_PLAYER_CACHE_TTL:
        # Fetch + parse map.sql
        guild = await database.get_guild(guild_id)
        server_url = (guild or {}).get("tw_world", "")
        if not server_url:
            return JSONResponse({"error": "no server configured"}, status_code=400)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(f"{server_url}/map.sql")
                if r.status_code != 200:
                    return JSONResponse({"error": "map unavailable"}, status_code=502)
                raw = r.text
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=502)

        # Parse map.sql — build player → {alliance, villages} index
        import re as _re2
        player_index: dict = {}
        def _is_num(s): return bool(_re2.match(r'^-?\d+(\.\d+)?$', (s or '').strip()))
        for line in raw.split('\n'):
            m = _re2.search(r'VALUES\s*\((.+)\);?\s*$', line, _re2.I)
            if not m: continue
            # Minimal CSV split (handles quoted strings)
            parts = []
            buf, in_q = '', False
            for ch in m.group(1):
                if ch == "'" and not in_q: in_q = True; continue
                if ch == "'" and in_q:     in_q = False; continue
                if ch == ',' and not in_q: parts.append(buf); buf = ''; continue
                buf += ch
            parts.append(buf)
            if len(parts) < 10: continue
            try:
                f0 = float(parts[0])
            except Exception:
                continue
            v7num = _is_num(parts[7])
            # Layout A: vid, x, y, tribe, row_uid, vname, player_id, pname, aid, aname, pop
            if not v7num and f0 >= 0 and len(parts) > 10:
                x,y,vname = int(float(parts[1])),int(float(parts[2])),parts[5]
                pname,aname,pop = parts[7],parts[9],int(float(parts[10] or 0))
            elif not v7num and abs(f0) <= 800:
                x,y,vname = int(f0),int(float(parts[1])),parts[5]
                pname,aname,pop = parts[7],parts[9],int(float(parts[6] or 0))
            elif v7num and f0 >= 0:
                x,y,vname = int(float(parts[1])),int(float(parts[2])),parts[6]
                pname,aname,pop = parts[8],parts[10],int(float(parts[7] or 0))
            else:
                continue
            if not pname or pname in ('NULL','','Natars'): continue
            if pname not in player_index:
                player_index[pname] = {"alliance": aname or "", "villages": []}
            player_index[pname]["villages"].append({"name": vname, "x": x, "y": y, "pop": pop})

        players_list = [
            {"name": k, "alliance": v["alliance"],
             "villages": sorted(v["villages"], key=lambda vv: -vv["pop"])}
            for k, v in player_index.items()
        ]
        _map_player_cache[guild_id] = {"ts": _time.time(), "players": players_list}
        cache = _map_player_cache[guild_id]

    # Filter by query (name or alliance)
    results = [
        p for p in cache["players"]
        if q in p["name"].lower() or q in p["alliance"].lower()
    ]
    # Sort: exact prefix match first
    results.sort(key=lambda p: (0 if p["name"].lower().startswith(q) else 1, p["name"].lower()))
    return JSONResponse({"players": results[:15]})


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
    err = await _require_guild_async(session, guild_id)
    if err: return err
    req = await database.get_res_request_by_id_web(request_id)
    if not req or req.get("guild_id") != guild_id:
        return RedirectResponse(f"/guild/{guild_id}/res-push", status_code=303)
    if req.get("push_channel_id"):
        try:
            bot_token = os.environ.get("DISCORD_TOKEN", "")
            async with httpx.AsyncClient(timeout=5) as client:
                await client.delete(
                    f"https://discord.com/api/v10/channels/{req['push_channel_id']}",
                    headers={"Authorization": f"Bot {bot_token}"},
                )
        except Exception:
            pass
    await database.delete_res_request(request_id)
    return RedirectResponse(f"/guild/{guild_id}/res-push?flash=removed", status_code=303)


# ---------------------------------------------------------------------------
# Billing routes
# ---------------------------------------------------------------------------

def _stripe_client():
    if not STRIPE_SECRET_KEY:
        return None
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


@app.post("/guild/{guild_id}/setup/api/save-world")
async def setup_save_world(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)
    body = await request.json()
    url = (body.get("server_url") or "").strip().rstrip("/")
    if not url:
        return JSONResponse({"ok": False, "error": "URL is required"})
    if not re.match(r"^https://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", url):
        return JSONResponse({"ok": False, "error": f"Invalid URL format: {url!r}. Use e.g. https://ts1.travian.com"})
    await database.update_tw_world(guild_id, url)
    return JSONResponse({"ok": True, "saved": url})


@app.post("/guild/{guild_id}/setup/api/trigger-snapshot")
async def setup_trigger_snapshot(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)
    guild = await database.get_guild(guild_id)
    tw_world = (guild.get("tw_world") or "").strip() if guild else ""
    if not tw_world:
        return JSONResponse({"ok": False, "error": "No world URL set — complete step 1 first"})
    try:
        await _fetch_and_save_snapshot(guild_id, tw_world)
        count = await database.get_snapshot_count(guild_id)
        return JSONResponse({"ok": True, "snapshot_count": count})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/guild/{guild_id}/setup", response_class=HTMLResponse)
async def guild_setup_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    snapshot_count = await database.get_snapshot_count(guild_id)
    own_villages   = await database.get_own_villages(guild_id)
    skip2 = request.query_params.get("skip2") == "1"

    step1_done = bool((guild.get("tw_world") or "").strip())
    step2_done = bool(own_villages) or skip2
    step3_done = snapshot_count > 0

    return templates.TemplateResponse("setup_wizard.html", {
        "request":        request,
        "guild":          guild,
        "session":        session,
        "step1_done":     step1_done,
        "step2_done":     step2_done,
        "step3_done":     step3_done,
        "snapshot_count": snapshot_count,
        "tw_world":       guild.get("tw_world") or "",
        "skip2":          skip2,
    })


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


@app.get("/guild/{guild_id}/upgrade")
async def upgrade_page(request: Request, guild_id: str, plan: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    guild = await _enrich_guild_subscription(guild)
    has_player = _has_player_pro(guild)
    has_alliance = _has_alliance_pro(guild)
    billing_url = f"/guild/{guild_id}/billing"
    # If they already have what they need, send to billing for management
    if plan == "player_pro" and has_player:
        return RedirectResponse(billing_url, status_code=303)
    if plan == "alliance" and has_alliance:
        return RedirectResponse(billing_url, status_code=303)
    return templates.TemplateResponse("upgrade.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
        "plan": plan,  # "player_pro" or "alliance" or "" (show both)
        "has_player_pro": has_player,
        "has_alliance_pro": has_alliance,
        "billing_url": billing_url,
        "plans": TIER_META,
    })


@app.get("/guild/{guild_id}/tutorial", response_class=HTMLResponse)
async def tutorial_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("tutorial.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
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
    # Compute trial days remaining for guild
    trial_days_left = None
    if guild.get("subscription_status") == "trialing" and guild.get("trial_expires_at"):
        from datetime import datetime as _dt
        try:
            exp = _dt.fromisoformat(guild["trial_expires_at"])
            delta = (exp - _dt.utcnow()).days
            trial_days_left = max(0, delta)
        except Exception:
            pass
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
        "trial_days_left": trial_days_left,
        "free_trial_used": bool(guild.get("free_trial_used")),
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


@app.post("/guild/{guild_id}/billing/start-free-trial")
async def guild_start_free_trial(request: Request, guild_id: str, plan: str = Form("starter")):
    """Activate the one-time 30-day free trial for a guild (no credit card required)."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not is_guild_owner(session, guild) and session.get("type") != "admin":
        return RedirectResponse(f"/guild/{guild_id}/billing?error=not_owner", status_code=303)
    valid_plans = ("starter", "clan", "alliance", "imperium")
    if plan not in valid_plans:
        plan = "starter"
    ok = await database.start_guild_free_trial(guild_id, plan)
    if not ok:
        return RedirectResponse(f"/guild/{guild_id}/billing?error=trial_already_used", status_code=303)
    return RedirectResponse(f"/guild/{guild_id}/billing?saved=trial_started", status_code=303)


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

@app.get("/fuer-allianz-leader", response_class=HTMLResponse)
async def alliance_leader_page(request: Request):
    """Dedicated landing page for alliance leaders."""
    session = _get_session(request)
    return templates.TemplateResponse("alliance_leader.html", {
        "request": request,
        "session": session,
        "base_url": str(request.base_url).rstrip("/"),
    })


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
        "free_trial_used": bool((user_sub or {}).get("free_trial_used")),
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
        # Handle user-level checkout completions (source=plans OR personal Player Pro)
        meta = obj.get("metadata") or {}
        _is_user_checkout = (
            (meta.get("source") == "plans" and meta.get("discord_user_id"))
            or (meta.get("personal") == "1" and meta.get("owner_discord_id"))
        )
        # Normalise discord_user_id field
        if not meta.get("discord_user_id") and meta.get("owner_discord_id"):
            meta = dict(meta, discord_user_id=meta["owner_discord_id"])
        if _is_user_checkout and meta.get("discord_user_id"):
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
            # Referral: award point to referrer if this user was referred
            ref_code = meta.get("ref_code", "")
            if ref_code and discord_user_id:
                referrer_id = await database.get_referral_code_owner(ref_code)
                if referrer_id and referrer_id != discord_user_id:
                    awarded = await database.award_referral_point(referrer_id, discord_user_id)
                    if awarded:
                        print(f"[referral] +1 point to {referrer_id} for referring {discord_user_id}", flush=True)
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

def classify_own_village(troops: dict, troop_roles: dict | None = None) -> tuple:
    """Returns (village_type, off_score, def_score, priority).
    troop_roles: mapping troop_name -> 'off'|'def'|'both'|'scout'|'siege'|'ignore'
    Falls back to built-in defaults when None.
    """
    roles = troop_roles if troop_roles is not None else database.TROOP_ROLE_DEFAULTS

    # Attack and defense values from Travian T4.4 game data
    # Off weight = attack value; Def weight = avg(defI, defC)
    _OFF_WEIGHT = {
        # Römer
        "Legionär": 40, "Prätorianer": 30, "Imperianer": 70,
        "Equites Imperatoris": 120, "Equites Caesaris": 180,
        "Rammbock": 60, "Feuerkatapult": 75, "Senator": 50,
        # Gallier
        "Phalanx": 15, "Schwertkämpfer": 65,
        "Theutates-Blitz": 90, "Druidentreiter": 45, "Haeduer": 140,
        "Gallier-Rammbock": 50, "Gallier-Kata": 70, "Häuptling": 40,
        # Teutonen
        "Keulenschwinger": 40, "Speerkämpfer": 10, "Axtkämpfer": 55,
        "Paladin": 55, "Teut. Ritter": 150,
        "Teutonen-Rammbock": 65, "Kriegsmaschine": 50,
        # Ägypter
        "Schleuderer": 30, "Ägyptischer Reiter": 100,
        "Khopesh-Krieger": 60, "Anhur-Wächter": 10, "Resheph-Streitwagen": 180,
        # Hunnen
        "Soldat": 40, "Lanzenkämpfer": 10, "Marauder": 80,
        "Boyar": 45, "Hunnischer Reiter": 160,
        # Spartaner
        "Hoplite": 35, "Sentinel": 0,
    }
    _DEF_WEIGHT = {
        # Römer — avg(defI, defC)
        "Legionär": 43, "Prätorianer": 50, "Imperianer": 33,
        "Equites Legati": 15, "Equites Imperatoris": 58, "Equites Caesaris": 93,
        # Gallier
        "Phalanx": 45, "Schwertkämpfer": 28,
        "Pathfinder": 15, "Druidentreiter": 85, "Haeduer": 113,
        # Teutonen
        "Speerkämpfer": 48, "Paladin": 70,
        # Ägypter
        "Schleuderer": 48, "Anhur-Wächter": 155,
        # Hunnen
        "Lanzenkämpfer": 80, "Boyar": 128,
        # Spartaner
        "Hoplite": 100,
    }

    off_score = 0
    def_score = 0
    scout_count = 0
    siege_count = 0

    for t, c in troops.items():
        role = roles.get(t, "ignore")
        if role in ("off", "both"):
            off_score += _OFF_WEIGHT.get(t, 50) * c
        if role in ("def", "both"):
            def_score += _DEF_WEIGHT.get(t, 50) * c
        if role == "scout":
            scout_count += c
        if role == "siege":
            siege_count += c

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


TRIBE_COLUMN_ORDER: dict[str, list[str]] = {
    "römer":    ["Legionär","Prätorianer","Imperianer","Equites Legati","Equites Imperatoris","Equites Caesaris","Rammbock","Feuerkatapult","Senator","Siedler","Held"],
    "teutonen": ["Keulenschwinger","Speerkämpfer","Axtkämpfer","Späher","Paladin","Teut. Ritter","Teutonen-Rammbock","Kriegsmaschine","Häuptling","Siedler","Held"],
    "gallier":  ["Phalanx","Schwertkämpfer","Pathfinder","Theutates-Blitz","Druidentreiter","Haeduer","Gallier-Rammbock","Gallier-Kata","Stammesführer","Siedler","Held"],
    "ägypter":  ["Schleuderer","Ägyptischer Reiter","Khopesh-Krieger","Sopdu-Erkunder","Anhur-Wächter","Resheph-Streitwagen","Siedler","Held"],
    "hunnen":   ["Soldat","Lanzenkämpfer","Marauder","Ammende Nomadin","Boyar","Hunnischer Reiter","Siedler","Held"],
    "spartaner":["Hoplite","Sentinel","Siedler","Held"],
    # EN tribe names (server locale)
    "romans":   ["Legionär","Prätorianer","Imperianer","Equites Legati","Equites Imperatoris","Equites Caesaris","Rammbock","Feuerkatapult","Senator","Siedler","Held"],
    "teutons":  ["Keulenschwinger","Speerkämpfer","Axtkämpfer","Späher","Paladin","Teut. Ritter","Teutonen-Rammbock","Kriegsmaschine","Häuptling","Siedler","Held"],
    "gauls":    ["Phalanx","Schwertkämpfer","Pathfinder","Theutates-Blitz","Druidentreiter","Haeduer","Gallier-Rammbock","Gallier-Kata","Stammesführer","Siedler","Held"],
    "egyptians":["Schleuderer","Ägyptischer Reiter","Khopesh-Krieger","Sopdu-Erkunder","Anhur-Wächter","Resheph-Streitwagen","Siedler","Held"],
    "huns":     ["Soldat","Lanzenkämpfer","Marauder","Ammende Nomadin","Boyar","Hunnischer Reiter","Siedler","Held"],
    "spartans": ["Hoplite","Sentinel","Siedler","Held"],
}


def parse_own_villages(text: str, tribe: str = "") -> list:
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
    text = text.replace('\u2212', '-').replace('\u2013', '-')  # Unicode minus / en-dash \u2192 ASCII
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    TROOP_ALIASES = {
        # ══ Römer / Romans ════════════════════════════════════════════════════
        # DE
        "Legionär": "Legionär", "Prätorianer": "Prätorianer", "Imperianer": "Imperianer",
        "Equites Legati": "Equites Legati", "Equites Imperatoris": "Equites Imperatoris",
        "Equites Caesaris": "Equites Caesaris",
        "Rammbock": "Rammbock", "Feuerkatapult": "Feuerkatapult", "Senator": "Senator",
        # EN
        "Legionnaire": "Legionär", "Praetorian": "Prätorianer", "Imperian": "Imperianer",
        "Ram": "Rammbock", "Fire Catapult": "Feuerkatapult",
        # FR
        "Légionnaire": "Legionär", "Prétorien": "Prätorianer", "Impérien": "Imperianer",
        "Bélier": "Rammbock", "Catapulte à feu": "Feuerkatapult", "Sénateur": "Senator",
        # PL
        "Legionista": "Legionär", "Pretorianin": "Prätorianer", "Imperianin": "Imperianer",
        "Taran": "Rammbock", "Katapulta ognia": "Feuerkatapult", "Senator": "Senator",
        # CS/SK
        "Legionář": "Legionär", "Prétorian": "Prätorianer", "Imperián": "Imperianer",
        "Beran": "Rammbock", "Ohnivá katapulta": "Feuerkatapult",
        # RU
        "Легионер": "Legionär", "Преторианец": "Prätorianer", "Империец": "Imperianer",
        "Таран": "Rammbock", "Огненная катапульта": "Feuerkatapult", "Сенатор": "Senator",
        # TR
        "Lejyoner": "Legionär", "Pretorian": "Prätorianer", "İmparatorcu": "Imperianer",
        "Koç": "Rammbock", "Ateş Mancınığı": "Feuerkatapult", "Senatör": "Senator",

        # ══ Teutonen / Teutons ════════════════════════════════════════════════
        # DE
        "Keulenschwinger": "Keulenschwinger", "Speerkämpfer": "Speerkämpfer",
        "Axtkämpfer": "Axtkämpfer", "Späher": "Späher", "Paladin": "Paladin",
        "Teut. Ritter": "Teut. Ritter", "Teutonischer Ritter": "Teut. Ritter",
        "Teutonen Reiter": "Teut. Ritter",
        "Teutonen-Rammbock": "Teutonen-Rammbock", "Ramme": "Teutonen-Rammbock",
        "Kriegsmaschine": "Kriegsmaschine", "Katapult": "Kriegsmaschine",
        "Häuptling": "Häuptling",          # Teutonen-Häuptling
        # EN
        "Clubswinger": "Keulenschwinger", "Spearman": "Speerkämpfer",
        "Axeman": "Axtkämpfer", "Scout": "Späher", "Teutonic Knight": "Teut. Ritter",
        "Battering Ram": "Teutonen-Rammbock", "Catapult": "Kriegsmaschine",
        "Chief": "Häuptling",              # Teuton chief EN
        # FR
        "Frondeur de massue": "Keulenschwinger", "Lancier": "Speerkämpfer",
        "Hacheur": "Axtkämpfer", "Éclaireur": "Späher",
        "Paladin": "Paladin", "Chevalier teuton": "Teut. Ritter",
        "Bélier teuton": "Teutonen-Rammbock", "Machine de guerre": "Kriegsmaschine",
        "Chef": "Häuptling",               # Teuton chief FR
        # PL
        "Maczugowiec": "Keulenschwinger", "Włócznik": "Speerkämpfer",
        "Toporznik": "Axtkämpfer", "Zwiadowca": "Späher",
        "Rycerz Teutońscy": "Teut. Ritter", "Taran teutońscy": "Teutonen-Rammbock",
        "Machina wojenna": "Kriegsmaschine", "Wódz": "Häuptling",
        # RU
        "Дубинщик": "Keulenschwinger", "Копьеносец": "Speerkämpfer",
        "Топорщик": "Axtkämpfer", "Разведчик": "Späher", "Паладин": "Paladin",
        "Тевтонский рыцарь": "Teut. Ritter", "Таран тевтонский": "Teutonen-Rammbock",
        "Боевая машина": "Kriegsmaschine", "Вождь": "Häuptling",

        # ══ Gallier / Gauls ═══════════════════════════════════════════════════
        # DE
        "Phalanx": "Phalanx", "Schwertkämpfer": "Schwertkämpfer",
        "Kundschafter": "Pathfinder", "Pathfinder": "Pathfinder",
        "Theutates-Blitz": "Theutates-Blitz", "Theutates Blitz": "Theutates-Blitz",
        "Druidentreiter": "Druidentreiter", "Druidenreiter": "Druidentreiter",
        "Haeduer": "Haeduer", "Haeduaner": "Haeduer",
        "Stammesführer": "Stammesführer",  # Gallier-Häuptling (different from Teuton Häuptling!)
        "Gallier-Rammbock": "Gallier-Rammbock", "Rammholz": "Gallier-Rammbock",
        "Gallier-Kata": "Gallier-Kata", "Kriegskatapult": "Gallier-Kata",
        # EN
        "Swordsman": "Schwertkämpfer", "Theutates Thunder": "Theutates-Blitz",
        "Druidrider": "Druidentreiter", "Haeduan": "Haeduer",
        "Chieftain": "Stammesführer",      # Gaul chief EN (distinct from Teuton "Chief")
        "Gaul Ram": "Gallier-Rammbock", "Trebuchet": "Gallier-Kata",
        # FR
        "Phalangiste": "Phalanx", "Épéiste": "Schwertkämpfer",
        "Éclaireur gaulois": "Pathfinder", "Éclair de Teut.": "Theutates-Blitz",
        "Chevaucheur druide": "Druidentreiter", "Haeduan": "Haeduer",
        "Bélier gaulois": "Gallier-Rammbock", "Trébuchet": "Gallier-Kata",
        # PL
        "Falangista": "Phalanx", "Szermierz": "Schwertkämpfer",
        "Druid jeździec": "Druidentreiter", "Galijczyk": "Haeduer",
        "Taran galijski": "Gallier-Rammbock", "Trebusz": "Gallier-Kata",
        # RU
        "Фалангист": "Phalanx", "Мечник": "Schwertkämpfer",
        "Следопыт": "Pathfinder", "Удар Теутатеса": "Theutates-Blitz",
        "Друид-всадник": "Druidentreiter", "Эдуан": "Haeduer",
        "Галльский таран": "Gallier-Rammbock", "Требушет": "Gallier-Kata",

        # ══ Ägypter / Egyptians ═══════════════════════════════════════════════
        "Schleuderer": "Schleuderer", "Slinger": "Schleuderer",
        "Frondeur": "Schleuderer",
        "Ägyptischer Reiter": "Ägyptischer Reiter",
        "Egyptian Cavalry": "Ägyptischer Reiter", "Cavalier égyptien": "Ägyptischer Reiter",
        "Khopesh-Krieger": "Khopesh-Krieger", "Khopesh Warrior": "Khopesh-Krieger",
        "Guerrier Khopesh": "Khopesh-Krieger",
        "Sopdu-Erkunder": "Sopdu-Erkunder", "Sopdu Explorer": "Sopdu-Erkunder",
        "Explorateur Sopdu": "Sopdu-Erkunder",
        "Anhur-Wächter": "Anhur-Wächter", "Anhur Guard": "Anhur-Wächter",
        "Garde Anhur": "Anhur-Wächter",
        "Resheph-Streitwagen": "Resheph-Streitwagen", "Resheph Chariot": "Resheph-Streitwagen",
        "Char Resheph": "Resheph-Streitwagen",

        # ══ Hunnen / Huns ═════════════════════════════════════════════════════
        "Soldat": "Soldat", "Soldiery": "Soldat", "Soldat hun": "Soldat",
        "Lanzenkämpfer": "Lanzenkämpfer", "Lancer": "Lanzenkämpfer",
        "Marauder": "Marauder", "Maraudeur": "Marauder",
        "Ammende Nomadin": "Ammende Nomadin", "Nomad": "Ammende Nomadin",
        "Nomade": "Ammende Nomadin",
        "Boyar": "Boyar",
        "Hunnischer Reiter": "Hunnischer Reiter", "Hunnic Rider": "Hunnischer Reiter",
        "Cavalier hun": "Hunnischer Reiter",

        # ══ Spartaner / Spartans ══════════════════════════════════════════════
        "Hoplite": "Hoplite", "Hopliten": "Hoplite", "Hoplite spartiate": "Hoplite",
        "Sentinel": "Sentinel", "Wächter": "Sentinel", "Sentinelle": "Sentinel",

        # ══ Allgemein / General ═══════════════════════════════════════════════
        "Siedler": "Siedler", "Settler": "Siedler", "Colon": "Siedler",
        "Colonist": "Siedler", "Colono": "Siedler",
        "Held": "Held", "Hero": "Held", "Héros": "Held", "Heros": "Held",
        "Герой": "Held", "Bohater": "Held",
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

        # Format 1a: German troops overview ("Dorfname" / "Dorf" header)
        # Format 1b: English troops overview ("Village" header with troop-name cols)
        _excl = {'attacks', 'angriffe', 'troops', 'truppen', 'building',
                 'gebäude', 'merchants', 'händler', 'bevölkerung', 'population',
                 'loyalität', 'loyalty', 'aktivität', 'activity'}
        is_troop_col_header = (
            (first in ('dorfname', 'dorf') and len(parts) >= 3 and not any(
                p.strip().lower() in _excl for p in parts[1:])) or
            (first == 'village' and len(parts) >= 3 and not any(
                p.strip().lower() in _excl for p in parts[1:]))
        )
        if is_troop_col_header:
            header_idx = i
            col_names  = [normalize(p) for p in parts[1:]]
            # Mobile: header row has empty column names → use tribe fallback
            if all(c == "" for c in col_names) and tribe:
                col_names = TRIBE_COLUMN_ORDER.get(tribe.lower(), col_names)
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
                nc = re.sub(r'[\s\.\,]', '', n)  # strip spaces, dots (DE thousands), commas
                nums.append(int(nc) if re.match(r'^\d+$', nc) else None)
            if not any(isinstance(n, int) for n in nums):
                continue
            table_villages[vname] = {
                col_names[k]: n for k, n in enumerate(nums)
                if k < len(col_names) and isinstance(n, int) and n > 0
            }

    elif header_idx is not None and fmt_village_overview and troops_col is not None:
        troop_re = re.compile(
            r'([\d\.]+)\s*[x\u00d7]\s*([A-Za-z\u00c0-\u024f][A-Za-z\u00c0-\u024f\s\-\.]+?)' +
            r'(?=\s+[\d\.]+\s*[x\u00d7]|\s*$)'
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
                count = int(m.group(1).replace('.', '').replace(',', ''))
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
    "Gallier-Rammbock": 5, "Gallier-Kata": 6,
    "Siedler": 1, "Held": 0,
}


def _enrich_own_villages(own_villages: list) -> list:
    """Attach parsed troops + crop/unit totals to each village row."""
    import json as _json
    for v in own_villages:
        try:
            troops = _json.loads(v.get("troops_json") or "{}")
            # Drop empty-key entries from failed parses
            troops = {t: c for t, c in troops.items() if t}
            v["troops"] = troops
        except Exception:
            v["troops"] = {}
        v["total_crop"]  = v.get("total_crop") or sum(_CROP_MAP.get(t, 1) * c for t, c in v["troops"].items())
        v["total_units"] = v.get("total_units") or sum(v["troops"].values())
    return own_villages


@app.get("/guild/{guild_id}/my-account", response_class=HTMLResponse)
async def my_account_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    discord_id   = session.get("uid", "") or session.get("discord_id", "")
    own_villages = _enrich_own_villages(await database.get_own_villages(guild_id, discord_id))
    ts_levels      = await database.get_village_ts_levels(discord_id)
    crop_overrides = await database.get_village_crop_overrides(discord_id)
    scout_village = await database.get_scout_village(guild_id, discord_id)
    history      = await database.get_own_villages_history(guild_id, discord_id)
    my_troops    = await database.get_member_troops_single(guild_id, discord_id)
    uploaded     = request.query_params.get("uploaded")
    cleared      = request.query_params.get("cleared")
    saved        = request.query_params.get("saved")

    # Re-classify villages with CURRENT troop roles (so role changes take effect immediately)
    troop_roles = await database.get_troop_roles(guild_id)
    for v in own_villages:
        vtype, off_s, def_s, prio = classify_own_village(v.get("troops", {}), troop_roles)
        v["village_type"] = vtype
        v["off_score"]    = off_s
        v["def_score"]    = def_s
        v["priority"]     = prio

    # Totals for KPI strip
    total_off  = sum(v.get("off_score", 0) for v in own_villages)
    total_def  = sum(v.get("def_score", 0) for v in own_villages)
    total_crop = sum(v.get("total_crop", 0) for v in own_villages)

    # Population for TQ calculation (from map snapshot, sum per travian_name)
    my_travian_name_val = (await database.get_member_troops_single(guild_id, discord_id) or {}).get("travian_name", "")
    my_population = 0
    if my_travian_name_val:
        player = await database.get_player_from_snapshot(guild_id, my_travian_name_val)
        my_population = (player or {}).get("total_pop", 0) or 0
    # Alliance TQ requirement
    ally_group_info = await database.get_ally_group_for_guild(guild_id)
    tq_min = (ally_group_info or {}).get("tq_min", 0) or 0
    lock_travian_name = bool((ally_group_info or {}).get("lock_travian_name"))
    # Is the current user an editor (lead or HC)?
    _is_account_editor = (
        (ally_group_info or {}).get("owner_discord_id") == discord_id
        or await has_perm(request, guild_id, "ally_manage")
    )

    sitters = await database.get_account_sitters(guild_id, session.get("uid", ""))
    dual_links = await database.get_dual_links_for_owner(guild_id, session.get("uid", ""))
    dual_created = request.query_params.get("dual_created")

    hospital_data    = await database.get_hospital_data(guild_id, session.get("uid", ""))
    hospital_uploaded = request.query_params.get("hospital_uploaded")
    hospital_cleared  = request.query_params.get("hospital_cleared")

    my_waves = await database.get_my_op_waves(guild_id, discord_id)
    march_settings = await database.get_march_settings(guild_id, discord_id)

    return templates.TemplateResponse("my_account.html", {
        "request":            request,
        "guild":              guild,
        "own_villages":       own_villages,
        "ts_levels":          ts_levels,
        "crop_overrides":     crop_overrides,
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
        "my_travian_name":    my_travian_name_val or (my_troops or {}).get("travian_name", ""),
        "my_waves":           my_waves,
        "my_population":      my_population,
        "tq_min":             tq_min,
        "lock_travian_name":  lock_travian_name and not _is_account_editor,
        "scout_village":      scout_village,
        "march_settings":     march_settings,
        "march_saved":        request.query_params.get("march_saved"),
        "troop_roles":        troop_roles,
    })


@app.post("/guild/{guild_id}/my-account")
async def my_account_upload(
    request: Request,
    guild_id: str,
    travian_name: str = Form(""),
    troop_text: str = Form(""),
    tribe: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    discord_id  = session.get("uid","")
    uploaded_by = session.get("username") or session.get("discord_username") or "unknown"
    tname       = travian_name.strip()

    # If the alliance has lock_travian_name=1, members may not change their stored name
    ally_group_info = await database.get_ally_group_for_guild(guild_id)
    if ally_group_info and ally_group_info.get("lock_travian_name"):
        is_editor = await has_perm(request, guild_id, "ally_manage") or \
                    (ally_group_info.get("owner_discord_id") == discord_id)
        if not is_editor:
            # Ignore the submitted travian_name — keep whatever is stored
            existing = await database.get_member_troops_single(guild_id, discord_id)
            tname = (existing or {}).get("travian_name", "") or ""

    # Auto-detect tribe from map data if not submitted
    _TRIBE_ID_MAP = {1:"römer", 2:"teutonen", 3:"gallier", 5:"ägypter", 6:"hunnen", 7:"spartaner"}
    if not tribe and tname:
        tribe = await database.get_player_tribe_from_map(guild_id, tname)
    elif not tribe and not tname:
        existing = existing if 'existing' in dir() else await database.get_member_troops_single(guild_id, discord_id)
        stored_name = (existing or {}).get("travian_name", "")
        if stored_name:
            tribe = await database.get_player_tribe_from_map(guild_id, stored_name)

    # Persist travian_name to member_troops even without a troop upload
    parsed = parse_own_villages(troop_text, tribe=tribe)
    troop_roles = await database.get_troop_roles(guild_id)
    _CROP_MAP = {
        "Legionär":1,"Prätorianer":1,"Imperianer":1,
        "Equites Legati":2,"Equites Imperatoris":3,"Equites Caesaris":4,
        "Rammbock":5,"Feuerkatapult":6,"Senator":5,
        "Keulenschwinger":1,"Speerkämpfer":1,"Axtkämpfer":1,
        "Späher":1,"Paladin":2,"Teut. Ritter":3,
        "Häuptling":4,"Stammesführer":4,"Teutonen-Rammbock":5,"Kriegsmaschine":6,
        "Phalanx":1,"Schwertkämpfer":1,"Pathfinder":2,
        "Theutates-Blitz":2,"Druidentreiter":2,"Haeduer":3,
        "Gallier-Rammbock":5,"Gallier-Kata":6,"Siedler":1,"Held":0,
    }
    for v in parsed:
        vtype, off_s, def_s, prio = classify_own_village(v.get("troops", {}), troop_roles)
        v["village_type"] = vtype
        v["off_score"]    = off_s
        v["def_score"]    = def_s
        v["priority"]     = prio
        v["total_crop"]   = sum(_CROP_MAP.get(t, 1) * c for t, c in v.get("troops", {}).items())
        v["total_units"]  = sum(c for c in v.get("troops", {}).values())
    if parsed:
        await database.save_own_villages(guild_id, parsed, uploaded_by, discord_id)
    troop_roles_scout = {t for t, r in troop_roles.items() if r == "scout"}
    total_off    = sum(v.get("off_score",0) for v in parsed)
    total_def    = sum(v.get("def_score",0) for v in parsed)
    total_crop   = sum(v.get("total_crop",0) for v in parsed)
    total_units  = sum(v.get("total_units",0) for v in parsed)
    total_scouts = sum(c for v in parsed for t, c in v.get("troops",{}).items() if t in troop_roles_scout)
    # Always upsert member_troops so travian_name is stored (even if no troop data yet)
    if tname or parsed:
        await database.upsert_member_troops(
            guild_id, discord_id, uploaded_by, tname or uploaded_by,
            [{k: val for k, val in vill.items()
              if k in ("village_name","x","y","troops","off_score","def_score","village_type","total_crop","total_units")}
             for vill in parsed],
            tribe=tribe, total_off=total_off, total_def=total_def,
            total_crop=total_crop, total_units=total_units, total_scouts=total_scouts,
        )
    return RedirectResponse(f"/guild/{guild_id}/my-account?uploaded={len(parsed)}", status_code=303)


@app.post("/guild/{guild_id}/my-account/set-scout-village")
async def my_account_set_scout_village(
    request: Request, guild_id: str,
    x: int = Form(...), y: int = Form(...),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    discord_id = session.get("uid", "") or session.get("discord_id", "")
    await database.set_scout_village(guild_id, discord_id, x, y)
    return RedirectResponse(f"/guild/{guild_id}/my-account?saved=scout_village", status_code=303)


@app.post("/guild/{guild_id}/my-account/march-settings")
async def save_march_settings(
    request: Request, guild_id: str,
    tournament_square: int = Form(0),
    boots: str = Form("none:0"),
    server_speed: float = Form(1.0),
    home_x: float = Form(0.0),
    home_y: float = Form(0.0),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    discord_id = session.get("uid", "")
    await database.save_march_settings(
        guild_id, discord_id,
        tournament_square=int(tournament_square),
        boots=boots.strip() or "none:0",
        server_speed=float(server_speed),
        home_x=float(home_x), home_y=float(home_y),
    )
    return RedirectResponse(f"/guild/{guild_id}/my-account?march_saved=1#march-settings", status_code=303)


@app.post("/guild/{guild_id}/my-account/clear")
async def my_account_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    discord_id = session.get("uid", "")
    await database.delete_own_villages(guild_id, discord_id)
    return RedirectResponse(f"/guild/{guild_id}/my-account?cleared=1", status_code=303)


@app.post("/guild/{guild_id}/my-account/sitters")
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
    return RedirectResponse(f"/guild/{guild_id}/my-account?saved=1", status_code=303)


@app.get("/guild/{guild_id}/my-account/kampfkraft", response_class=HTMLResponse)
async def kampfkraft_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    # Pass own villages so the calculator can pre-fill troop counts
    own_villages = _enrich_own_villages(await database.get_own_villages(guild_id, session.get("uid","")))
    troop_roles = await database.get_troop_roles(guild_id)
    for v in own_villages:
        vtype, off_s, def_s, prio = classify_own_village(v.get("troops", {}), troop_roles)
        v["village_type"] = vtype; v["off_score"] = off_s; v["def_score"] = def_s
    return templates.TemplateResponse("kampfkraft.html", {
        "request": request, "guild": guild, "own_villages": own_villages,
    })


# Legacy redirects — keep old /attacks/own-troops URLs working
@app.get("/guild/{guild_id}/attacks", response_class=HTMLResponse)
async def attacks_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    attack_stats = await database.get_attack_stats(guild_id)
    attack_reports = await database.get_attack_reports(guild_id, limit=50)
    is_admin = session.get("type") == "admin" or uid == guild.get("owner_discord_id")
    perms = await database.get_member_permissions(guild_id, uid)
    can_label   = is_admin or "ally_manage" in perms or "defend_manage" in perms or "attack_manage" in perms
    can_see_all = can_label or "ally_view" in perms or "defend_view" in perms
    return templates.TemplateResponse("attacks.html", {
        "request": request, "guild": guild, "guild_id": guild_id,
        "attack_stats": attack_stats, "attack_reports": attack_reports,
        "is_admin": is_admin, "saved": saved,
        "can_label": can_label,
        "can_see_all": can_see_all,
    })


@app.post("/guild/{guild_id}/attacks/config")
async def attacks_config(request: Request, guild_id: str,
                          attack_channel_id: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.set_attack_channel_web(guild_id, attack_channel_id.strip())
    return RedirectResponse(f"/guild/{guild_id}/attacks?saved=1", status_code=303)


@app.post("/guild/{guild_id}/attacks/auto-setup")
async def attacks_auto_setup(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post("http://bot:7777/api/create-report-channel",
                                     json={"guild_id": guild_id})
            data = resp.json() if resp.status_code == 200 else {}
            if data.get("channel_id"):
                await database.set_attack_channel_web(
                    guild_id, data["channel_id"],
                    data.get("message_id", ""))
    except Exception as e:
        print(f"[attacks-auto-setup] error: {e}")
    return RedirectResponse(f"/guild/{guild_id}/attacks?saved=1", status_code=303)


@app.post("/guild/{guild_id}/attacks/reset")
async def attacks_reset(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.set_attack_channel_web(guild_id, "")
    return RedirectResponse(f"/guild/{guild_id}/attacks?saved=1", status_code=303)


@app.get("/guild/{guild_id}/attacks/{report_id}/analyse", response_class=HTMLResponse)
async def attack_analysis_page(request: Request, guild_id: str, report_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    report = await database.get_attack_report(guild_id, report_id)
    if not report:
        return RedirectResponse(f"/guild/{guild_id}/attacks")
    return templates.TemplateResponse("attack_analysis.html", {
        "request": request, "guild": guild, "guild_id": guild_id, "report": report,
    })


@app.post("/guild/{guild_id}/attacks/report/delete/{report_id}")
async def attacks_delete_report(request: Request, guild_id: str, report_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    is_admin = session.get("type") == "admin" or session.get("uid") == (guild or {}).get("owner_discord_id")
    if not is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.delete_attack_report(report_id)
    return RedirectResponse(f"/guild/{guild_id}/attacks", status_code=303)


@app.get("/guild/{guild_id}/attacks/own-troops")
async def _legacy_own_troops_get(guild_id: str):
    return RedirectResponse(f"/guild/{guild_id}/my-account", status_code=301)


# ---------------------------------------------------------------------------
# Attack Detection — API routes
# ---------------------------------------------------------------------------

async def _attack_access(request, guild_id):
    """Shared access check for attack API routes: session + async guild + ally-or-plan."""
    session, err = _require_session(request)
    if err:
        return None, JSONResponse({"error": "unauthorized"}, status_code=401)
    err = await _require_guild_async(session, guild_id)
    if err:
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    guild = await database.get_guild(guild_id)
    if not guild:
        return None, JSONResponse({"error": "not_found"}, status_code=404)
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err:
        return None, JSONResponse({"error": "alliance_required"}, status_code=403)
    return session, None


@app.post("/guild/{guild_id}/attacks/import-rally")
async def attacks_import_rally(request: Request, guild_id: str):
    import json as _json
    session, err = await _attack_access(request, guild_id)
    if err: return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    attacks = body.get("attacks", [])
    if not isinstance(attacks, list):
        return JSONResponse({"error": "attacks must be a list"}, status_code=400)

    # Re-compute fake scores server-side using enemy artifacts from DB
    for atk in attacks:
        player = atk.get("attacker_player", "")
        artifacts = await database.get_enemy_artifacts(guild_id, player) if player else []
        result = _compute_fake_score_server(atk, artifacts)
        atk["fake_score"] = result["score"]
        atk["fake_reasons"] = result["reasons"]

    discord_id = session.get("uid", "")
    discord_name = session.get("username", "")
    saved, skipped = await database.save_incoming_attacks(guild_id, attacks, discord_id, discord_name)
    return JSONResponse({"saved": saved, "skipped": skipped})


def _compute_fake_score_server(atk: dict, artifacts: list) -> dict:
    if atk.get("attack_type") == "support":
        return {"score": 0, "reasons": ["Support troops — not an attack"]}
    score = 50
    reasons = []
    troops_hidden = bool(atk.get("troops_hidden"))
    troop_count = atk.get("troop_count", 0) or 0
    attack_type = atk.get("attack_type", "attack")

    if not troops_hidden:
        has_unique_scout = any(
            a.get("artifact_type") == "scout" and a.get("artifact_size") == "unique"
            for a in artifacts
        )
        if has_unique_scout:
            reasons.append("Truppen sichtbar (Unique Späher-Artefakt vorhanden - kein Fake-Signal)")
            score = 40
        else:
            score = 98
            reasons.append("Truppen sichtbar ohne Unique Späher = sehr wahrscheinlich Fake")
        if troop_count == 1:
            score = 99
            reasons.append("Nur 1 Einheit = Fake")
        elif troop_count <= 5:
            score = max(score, 92)
            reasons.append(f"Nur {troop_count} Einheiten sichtbar")
    else:
        score = 40
        reasons.append("Truppen verdeckt (≥20) - möglicherweise echter Angriff")

    if attack_type == "raid":
        score = min(100, score + 15)
        reasons.append("Raubzug-Typ (+15% Fake-Wahrscheinlichkeit)")

    return {"score": min(100, max(0, score)), "reasons": reasons}


@app.get("/guild/{guild_id}/attacks/api/incoming")
async def attacks_api_incoming(
    request: Request, guild_id: str,
    x: int = None, y: int = None,
    atk_name: str = "", atk_alliance: str = "", atk_village: str = "",
    def_alliance: str = "", def_village: str = "",
):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    is_admin = session.get("type") == "admin" or uid == (guild or {}).get("owner_discord_id", "")
    perms = await database.get_member_permissions(guild_id, uid)
    can_see_all = is_admin or any(p in perms for p in ("ally_manage","defend_manage","attack_manage","ally_view","defend_view"))
    # Users without rights only see their own imports
    own_id = None if can_see_all else uid
    attacks = await database.get_incoming_attacks(
        guild_id, x, y,
        own_discord_id=own_id,
        attacker_name=atk_name or None,
        attacker_alliance=atk_alliance or None,
        attacker_village=atk_village or None,
        defender_alliance=def_alliance or None,
        defender_village=def_village or None,
    )
    return JSONResponse(attacks)


@app.post("/guild/{guild_id}/attacks/test-data")
async def attacks_insert_test_data(request: Request, guild_id: str):
    """Insert test attacks for development/demo purposes."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return JSONResponse({"error": "not_found"}, status_code=404)
    uid = session.get("uid", "")
    is_admin = session.get("type") == "admin" or uid == guild.get("owner_discord_id", "")
    perms = await database.get_member_permissions(guild_id, uid)
    if not (is_admin or "ally_manage" in perms):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    count = await database.insert_attack_test_data(guild_id, uid, session.get("username", "Tester"))
    return JSONResponse({"inserted": count})


@app.post("/guild/{guild_id}/attacks/{attack_id}/defense-call")
async def attack_create_defense_call(request: Request, guild_id: str, attack_id: int):
    """Create a Discord defend channel for a specific incoming attack."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    if not guild: return JSONResponse({"error": "not_found"}, status_code=404)
    is_admin = session.get("type") == "admin" or uid == guild.get("owner_discord_id", "")
    perms = await database.get_member_permissions(guild_id, uid)
    can_label = is_admin or any(p in perms for p in ("ally_manage","defend_manage","attack_manage"))
    if not can_label:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    # Load the attack
    attacks = await database.get_incoming_attacks(guild_id)
    attack = next((a for a in attacks if a["id"] == attack_id), None)
    if not attack:
        return JSONResponse({"error": "attack_not_found"}, status_code=404)

    body = await request.json()
    troop_goal = body.get("troop_goal", "")
    ratio      = body.get("ratio", "")
    notes      = body.get("notes", "")

    # Build fields for the bot
    defender = f"{attack.get('own_village_name','?')} ({attack.get('own_village_x','?')}|{attack.get('own_village_y','?')})"
    attacker_txt = f"{attack.get('attacker_player','?')} — {attack.get('attacker_village_name','?')} ({attack.get('attacker_x','?')}|{attack.get('attacker_y','?')})"
    coords = f"{attack.get('own_village_x','?')}|{attack.get('own_village_y','?')}"
    arrival = attack.get("arrival_time","")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("http://bot:7777/api/create-defend-channel", json={
                "guild_id": guild_id,
                "defender": defender,
                "attacker": attacker_txt,
                "coords": coords,
                "arrival_time": arrival,
                "troop_goal": troop_goal,
                "ratio": ratio,
                "notes": notes,
                "requested_by_id": uid,
                "requested_by_name": session.get("username","Dashboard"),
                "attack_id": str(attack_id),
            })
            data = resp.json()
            if not data.get("ok"):
                return JSONResponse({"error": data.get("error","bot_error")}, status_code=500)
            # Label attack as need_def if no label yet
            if not attack.get("label"):
                await database.label_attack(attack_id, guild_id, "need_def", uid)
            return JSONResponse({"ok": True, "channel_id": data.get("channel_id"), "channel_mention": data.get("channel_mention")})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/guild/{guild_id}/attacks/api/def-calls")
async def attacks_api_def_calls(request: Request, guild_id: str):
    """Return all open defend channels with troop fill status."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    calls = await database.get_active_def_calls(guild_id)
    return JSONResponse(calls)


@app.get("/guild/{guild_id}/attacks/{attack_id}/def-status")
async def attack_def_status(request: Request, guild_id: str, attack_id: int):
    """Return defend channel status + sent troops for a specific attack."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    status = await database.get_defend_status_for_attack(guild_id, str(attack_id))
    return JSONResponse(status)


@app.get("/guild/{guild_id}/attacks/api/alliance")
async def attacks_api_alliance(request: Request, guild_id: str):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    attacks = await database.get_incoming_attacks_alliance(guild_id)
    return JSONResponse(attacks)


@app.get("/guild/{guild_id}/attacks/api/member-status")
async def attacks_api_member_status(request: Request, guild_id: str):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    status = await database.get_attack_member_status(guild_id)
    return JSONResponse(status)


@app.get("/guild/{guild_id}/attacks/alliance-overview", response_class=HTMLResponse)
async def attacks_alliance_overview(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    is_admin = session.get("type") == "admin" or uid == guild.get("owner_discord_id")
    perms = await database.get_member_permissions(guild_id, uid)
    can_label = is_admin or "ally_manage" in perms or "defend_manage" in perms or "attack_manage" in perms
    return templates.TemplateResponse("alliance_attacks.html", {
        "request": request, "guild": guild, "guild_id": guild_id,
        "can_label": can_label,
    })


@app.post("/guild/{guild_id}/attacks/dismiss/{attack_id}")
async def attacks_dismiss(request: Request, guild_id: str, attack_id: int):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    await database.dismiss_attack(attack_id, guild_id)
    return JSONResponse({"ok": True})


def _attacks_can_manage(session: dict, guild: dict, perms: list) -> bool:
    uid = session.get("uid", "")
    is_admin = session.get("type") == "admin"
    is_owner = guild and guild.get("owner_discord_id") == uid
    return is_admin or is_owner or "ally_manage" in perms or "defend_manage" in perms


@app.post("/guild/{guild_id}/attacks/archive/{attack_id}")
async def attacks_archive(request: Request, guild_id: str, attack_id: int):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    perms = await database.get_member_permissions(guild_id, uid)
    if not _attacks_can_manage(session, guild, perms):
        return JSONResponse({"error": "no_permission"}, status_code=403)
    ok = await database.archive_attack(attack_id, guild_id)
    return JSONResponse({"ok": ok})


@app.post("/guild/{guild_id}/attacks/bulk-archive")
async def attacks_bulk_archive(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False}, status_code=401)
    if not await has_perm(request, guild_id, "label_attack"):
        return JSONResponse({"ok": False}, status_code=403)
    count = await database.bulk_archive_attacks(guild_id)
    return JSONResponse({"ok": True, "count": count})


@app.post("/guild/{guild_id}/attacks/bulk-delete")
async def attacks_bulk_delete(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False}, status_code=401)
    if not await has_perm(request, guild_id, "label_attack"):
        return JSONResponse({"ok": False}, status_code=403)
    count = await database.bulk_delete_attacks(guild_id)
    return JSONResponse({"ok": True, "count": count})


@app.get("/guild/{guild_id}/attacks/api/analysis")
async def attacks_api_analysis(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"ok": False}, status_code=403)
    data = await database.get_attack_analysis(guild_id)
    return JSONResponse({"ok": True, **data})


@app.post("/guild/{guild_id}/attacks/delete/{attack_id}")
async def attacks_delete(request: Request, guild_id: str, attack_id: int):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    perms = await database.get_member_permissions(guild_id, uid)
    if not _attacks_can_manage(session, guild, perms):
        return JSONResponse({"error": "no_permission"}, status_code=403)
    ok = await database.delete_attack(attack_id, guild_id)
    return JSONResponse({"ok": ok})


@app.get("/guild/{guild_id}/attacks/api/archived")
async def attacks_api_archived(request: Request, guild_id: str):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    perms = await database.get_member_permissions(guild_id, uid)
    if not _attacks_can_manage(session, guild, perms):
        return JSONResponse({"error": "no_permission"}, status_code=403)
    attacks = await database.get_archived_attacks(guild_id)
    return JSONResponse({"attacks": attacks})


@app.post("/guild/{guild_id}/attacks/label/{attack_id}")
async def attacks_label(request: Request, guild_id: str, attack_id: int):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    is_admin  = session.get("type") == "admin"
    is_owner  = guild and guild.get("owner_discord_id") == uid
    perms     = await database.get_member_permissions(guild_id, uid)
    has_right = "ally_manage" in perms or "defend_manage" in perms or "attack_manage" in perms
    if not (is_admin or is_owner or has_right):
        return JSONResponse({"error": "no_permission"}, status_code=403)
    body = await request.json()
    label = body.get("label", "")
    username = session.get("username", uid)
    ok = await database.label_attack(attack_id, guild_id, label, username)
    return JSONResponse({"ok": ok, "label": label})


@app.post("/guild/{guild_id}/attacks/note/{attack_id}")
async def attacks_save_note(request: Request, guild_id: str, attack_id: int):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    notes = body.get("notes", "")
    ok = await database.save_attack_note(attack_id, guild_id, notes)
    return JSONResponse({"ok": ok})


@app.get("/guild/{guild_id}/api/player-info")
async def guild_api_player_info(request: Request, guild_id: str, player: str = ""):
    session, err = _require_session(request)
    if err:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not player:
        return JSONResponse({"error": "player param required"}, status_code=400)
    data = await database.get_player_from_snapshot(guild_id, player)
    if not data:
        return JSONResponse(None)
    return JSONResponse(data)


@app.get("/guild/{guild_id}/attacks/api/enemy-artifacts/{player_name}")
async def attacks_api_get_enemy_artifacts(request: Request, guild_id: str, player_name: str):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    artifacts = await database.get_enemy_artifacts(guild_id, player_name)
    return JSONResponse(artifacts)


@app.post("/guild/{guild_id}/attacks/api/enemy-artifacts/{player_name}/toggle")
async def attacks_api_toggle_enemy_artifact(request: Request, guild_id: str, player_name: str):
    session, err = await _attack_access(request, guild_id)
    if err: return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    vx = body.get("village_x")
    vy = body.get("village_y")
    art_type = body.get("artifact_type", "")
    art_size = body.get("artifact_size", "")
    if not art_type or not art_size:
        return JSONResponse({"error": "artifact_type and artifact_size required"}, status_code=400)
    now_active = await database.toggle_enemy_artifact(
        guild_id, player_name, vx, vy, art_type, art_size
    )
    return JSONResponse({"active": now_active})


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
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    shared = await database.get_all_shared_sitters(guild_id)
    # Enrich with discord_username + travian_name from ally_members
    ally_group = await database.get_ally_group_for_guild(guild_id)
    member_map: dict[str, dict] = {}
    if ally_group:
        members = await database.get_ally_members(ally_group["id"])
        for m in members:
            member_map[m["discord_id"]] = m
    # Also enrich from cached usernames
    import aiosqlite as _aio
    async with _aio.connect(database.DB_PATH) as _db:
        _db.row_factory = _aio.Row
        async with _db.execute(
            "SELECT discord_user_id, discord_username FROM user_subscriptions WHERE discord_username IS NOT NULL"
        ) as _cur:
            for row in await _cur.fetchall():
                uid = row["discord_user_id"]
                if uid not in member_map:
                    member_map[uid] = {}
                if not member_map[uid].get("discord_username"):
                    member_map[uid]["discord_username"] = row["discord_username"]
    for s in shared:
        uid = s["discord_user_id"]
        m = member_map.get(uid, {})
        s["display_name"] = m.get("discord_username") or m.get("travian_name") or uid
        s["travian_name"] = m.get("travian_name") or "—"
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

@app.post("/guild/{guild_id}/my-account/dual/create")
async def dual_create(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    token = await database.create_dual_invite(
        guild_id=guild_id,
        owner_id=session.get("uid", ""),
        owner_username=session.get("username", ""),
    )
    return RedirectResponse(f"/guild/{guild_id}/my-account?dual_created={token}", status_code=303)


@app.post("/guild/{guild_id}/my-account/dual/revoke")
async def dual_revoke(request: Request, guild_id: str, token: str = Form(...)):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.revoke_dual_link(token, session.get("uid", ""))
    return RedirectResponse(f"/guild/{guild_id}/my-account", status_code=303)


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
# Routes — My Ally
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/my-ally", response_class=HTMLResponse)
async def my_ally_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    uid = session.get("uid", "")

    # If accessing via personal workspace but user has an ally membership elsewhere → redirect
    if guild_id.startswith("ws_"):
        real_guild_id = await database.get_ally_membership_guild_id(uid)
        if real_guild_id:
            return RedirectResponse(f"/guild/{real_guild_id}/my-ally", status_code=302)

    # ── Wave 1: fully independent queries ────────────────────────────────
    guild, ally_group, meta_alliances = await asyncio.gather(
        database.get_guild(guild_id),
        database.get_ally_group_for_owner(guild_id, uid),
        database.get_meta_alliances(guild_id),
    )
    if not guild:
        return RedirectResponse("/dashboard")

    # ── Wave 2: depends on ally_group ────────────────────────────────────
    members: list = []
    roles: list = []
    bonuses: list = []
    leaderboard: list = []
    battle_groups: list = []
    membership = None
    guild_group = None
    member_view_members: list = []
    member_leaderboard: list = []

    if ally_group:
        members, roles, bonuses, leaderboard, battle_groups = await asyncio.gather(
            database.get_ally_members(ally_group["id"]),
            database.get_ally_roles(ally_group["id"]),
            database.get_ally_bonuses(ally_group["id"]),
            database.get_member_leaderboard(guild_id),
            database.get_battle_groups(ally_group["id"]),
        )
    else:
        membership, guild_group = await asyncio.gather(
            database.get_ally_membership(guild_id, uid),
            database.get_ally_group_for_guild(guild_id),
        )
        if membership and guild_group:
            member_view_members, member_leaderboard = await asyncio.gather(
                database.get_ally_members(guild_group["id"]),
                database.get_member_leaderboard(guild_id),
            )

    flash = request.query_params.get("flash", "")

    # ── Inline editor perm check (avoids redundant get_guild inside has_perm) ──
    is_lead = bool(ally_group)
    if is_lead or session.get("type") == "admin" or guild.get("owner_discord_id") == uid:
        is_editor = True
    else:
        _perms = await database.get_member_permissions(guild_id, uid)
        is_editor = "ally_manage" in _perms

    # ── Wave 3: depends on members list ──────────────────────────────────
    all_members_list = members or member_view_members or []
    all_member_discord_ids = [m["discord_id"] for m in all_members_list if m.get("discord_id")]
    travian_names = [m["travian_name"] for m in all_members_list if m.get("travian_name")]

    ep_members: set = set()
    growth_data: dict = {}
    lb_by_discord: dict = {}
    member_pop_history: dict = {}
    ally_name_for_pop = (ally_group or {}).get("ally_name", "") or ""

    if all_members_list:
        # Gather all member-dependent queries in parallel
        async def _get_ally_pop():
            if ally_name_for_pop:
                return await database.get_alliance_pop_history(guild_id, ally_name_for_pop)
            return []

        (
            ep_members,
            growth_data,
            lb_by_discord,
            member_pop_history,
            ally_pop_history,
        ) = await asyncio.gather(
            database.get_active_ep_members(guild_id),
            database.get_member_growth(guild_id, all_member_discord_ids),
            database.get_member_troops_for_discord_ids(all_member_discord_ids),
            database.get_all_members_pop_from_snapshot(guild_id, travian_names),
            _get_ally_pop(),
        )
    else:
        ally_pop_history: list = []
        if ally_name_for_pop:
            ally_pop_history = await database.get_alliance_pop_history(guild_id, ally_name_for_pop)

    lb_by_travian: dict = {r["travian_name"]: r for r in lb_by_discord.values() if r.get("travian_name")}

    # Members without upload: pull from latest map snapshot
    all_travian_names_set = {m["travian_name"] for m in all_members_list if m.get("travian_name")}
    uploaded_names_set = {r["travian_name"] for r in lb_by_discord.values() if r.get("travian_name")}
    missing_names = list(all_travian_names_set - uploaded_names_set)
    map_only_members: list = []
    if missing_names and is_editor:
        map_only_members = await database.get_players_from_snapshot(guild_id, missing_names)

    return templates.TemplateResponse("my_ally.html", {
        "request": request, "guild": guild,
        "ally_group": ally_group, "members": members, "roles": roles,
        "membership": membership, "guild_group": guild_group,
        "member_view_members": member_view_members,
        "member_leaderboard": member_leaderboard,
        "session": session,
        "flash": flash, "base_url": str(request.base_url).rstrip("/"),
        "leaderboard": leaderboard,
        "meta_alliances": meta_alliances,
        "ep_members": list(ep_members),
        "growth_data": growth_data,
        "bonuses": bonuses,
        "is_editor": is_editor,
        "is_lead": is_lead,
        "lb_by_discord": lb_by_discord,
        "lb_by_travian": lb_by_travian,
        "lock_travian_name": bool((ally_group or guild_group or {}).get("lock_travian_name")),
        "battle_groups": battle_groups,
        "member_pop_history": member_pop_history,
        "ally_pop_history": ally_pop_history,
        "map_only_members": map_only_members,
    })


@app.post("/guild/{guild_id}/my-ally/create")
async def my_ally_create(request: Request, guild_id: str, ally_name: str = Form(...)):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    # 1 per server: check if ANY group exists for this guild
    existing = await database.get_ally_group_for_guild(guild_id)
    if existing:
        return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=guild_has_ally", status_code=303)
    await database.create_ally_group(guild_id, uid, session.get("username",""), ally_name.strip()[:80])
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=created", status_code=303)


@app.post("/guild/{guild_id}/my-ally/delete")
async def my_ally_delete(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if ally_group:
        await database.delete_ally_group(ally_group["id"], uid)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=deleted", status_code=303)


@app.post("/guild/{guild_id}/my-ally/settings")
async def my_ally_settings(request: Request, guild_id: str,
                             ally_name: str = Form(""),
                             wing1_name: str = Form(""),
                             wing2_name: str = Form(""),
                             tq_min: int = Form(0),
                             lock_travian_name: str = Form("0")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        return RedirectResponse(f"/guild/{guild_id}/my-ally", status_code=303)
    await database.update_ally_group(
        ally_group["id"], uid,
        ally_name=ally_name.strip()[:80],
        wing1_name=wing1_name.strip()[:40],
        wing2_name=wing2_name.strip()[:40],
        tq_min=max(0, tq_min),
        lock_travian_name=1 if lock_travian_name in ("1", "on", "true") else 0,
    )
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=saved", status_code=303)


@app.post("/guild/{guild_id}/my-ally/meta-alliance/add")
async def meta_alliance_add(request: Request, guild_id: str,
                             alliance_name: str = Form(""),
                             color: str = Form("#94a3b8")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    name = alliance_name.strip()[:80]
    if name:
        await database.add_meta_alliance(guild_id, name, color)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=saved#meta-alliances", status_code=303)


@app.post("/guild/{guild_id}/my-ally/meta-alliance/remove")
async def meta_alliance_remove(request: Request, guild_id: str,
                                alliance_name: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.remove_meta_alliance(guild_id, alliance_name)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=saved#meta-alliances", status_code=303)


@app.post("/guild/{guild_id}/my-ally/regen-token")
async def my_ally_regen_token(request: Request, guild_id: str, which: str = Form("main")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        return RedirectResponse(f"/guild/{guild_id}/my-ally", status_code=303)
    import secrets as _sec
    new_token = _sec.token_urlsafe(24)
    if which == "wing1":
        await database.update_ally_group(ally_group["id"], uid, wing1_token=new_token)
    elif which == "wing2":
        await database.update_ally_group(ally_group["id"], uid, wing2_token=new_token)
    else:
        await database.regenerate_ally_token(ally_group["id"], uid)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=token_renewed", status_code=303)


@app.post("/guild/{guild_id}/my-ally/roles/reorder")
async def my_ally_roles_reorder(request: Request, guild_id: str):
    """AJAX endpoint: reorder roles via drag & drop."""
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        return JSONResponse({"error": "not owner"}, status_code=403)
    import json as _json
    try:
        body = await request.json()
        ordered_ids = [int(x) for x in body.get("ids", [])]
    except Exception:
        return JSONResponse({"error": "invalid"}, status_code=400)
    await database.reorder_ally_roles(ally_group["id"], ordered_ids)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/my-ally/roles/create")
async def my_ally_role_create(request: Request, guild_id: str,
                               role_name: str = Form(...), color: str = Form("#94a3b8")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        return JSONResponse({"error": "not owner"}, status_code=403)
    await database.create_ally_role(ally_group["id"], role_name.strip(), color)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=role_created#rollen", status_code=303)


@app.post("/guild/{guild_id}/my-ally/roles/{role_id}/update")
async def my_ally_role_update(request: Request, guild_id: str, role_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        ally_group = await database.get_ally_group_for_guild(guild_id)
        if not ally_group:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not await has_perm(request, guild_id, "ally_manage"):
            return JSONResponse({"error": "no permission"}, status_code=403)
    form = await request.form()
    color = form.get("color") or None
    ALL_FLAGS = [
        "ally_manage",
        "defend_view", "defend_manage",
        "ep_manage", "ep_view", "ep_notify",
        "attack_manage", "attack_view",
        "scout_manage", "scout_view",
        "map_manage", "map_view",
        "res_push_view", "res_push_manage",
        "sector_view", "hospital_view",
    ]
    selected = [f for f in ALL_FLAGS if form.get(f) == "1"]
    # Apply preset shortcut
    preset = form.get("preset", "")
    if preset == "leiter":
        selected = ALL_FLAGS[:]
    elif preset == "officer":
        selected = [
            "defend_view", "defend_manage",
            "ep_manage", "ep_view", "ep_notify",
            "attack_manage", "attack_view",
            "scout_manage", "scout_view",
            "map_view", "map_manage",
            "res_push_view", "res_push_manage",
            "sector_view", "hospital_view",
        ]
    elif preset == "mitglied":
        selected = ["defend_view", "ep_view", "ep_notify", "attack_view", "scout_view", "map_view", "res_push_view", "hospital_view"]
    perms_str = ",".join(selected)
    await database.update_ally_role(role_id, ally_group["id"], color=color, permissions=perms_str)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=role_updated#rollen", status_code=303)


@app.post("/guild/{guild_id}/my-ally/roles/{role_id}/delete")
async def my_ally_role_delete(request: Request, guild_id: str, role_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        return JSONResponse({"error": "not owner"}, status_code=403)
    await database.delete_ally_role(ally_group["id"], role_id)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=role_deleted#rollen", status_code=303)


# ── Battlegroup routes ───────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/my-ally/battlegroups/create")
async def bg_create(request: Request, guild_id: str,
                    name: str = Form(...), color: str = Form("#6366f1"), description: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid","")
    ag = await database.get_ally_group_for_owner(guild_id, uid)
    if not ag: return JSONResponse({"error": "not owner"}, status_code=403)
    await database.create_battle_group(ag["id"], name.strip(), color, description.strip())
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=bg_created#battlegroups", status_code=303)


@app.post("/guild/{guild_id}/my-ally/battlegroups/{bg_id}/update")
async def bg_update(request: Request, guild_id: str, bg_id: int,
                    name: str = Form(...), color: str = Form("#6366f1"), description: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid","")
    ag = await database.get_ally_group_for_owner(guild_id, uid)
    if not ag: return JSONResponse({"error": "not owner"}, status_code=403)
    await database.update_battle_group(bg_id, ag["id"], name.strip(), color, description.strip())
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=bg_updated#battlegroups", status_code=303)


@app.post("/guild/{guild_id}/my-ally/battlegroups/{bg_id}/members")
async def bg_set_members(request: Request, guild_id: str, bg_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid","")
    ag = await database.get_ally_group_for_owner(guild_id, uid)
    if not ag: return JSONResponse({"error": "not owner"}, status_code=403)
    form = await request.form()
    discord_ids = form.getlist("discord_id")
    await database.set_battle_group_members(bg_id, discord_ids)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=bg_members_saved#battlegroups", status_code=303)


@app.post("/guild/{guild_id}/my-ally/battlegroups/{bg_id}/delete")
async def bg_delete(request: Request, guild_id: str, bg_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid","")
    ag = await database.get_ally_group_for_owner(guild_id, uid)
    if not ag: return JSONResponse({"error": "not owner"}, status_code=403)
    await database.delete_battle_group(bg_id, ag["id"])
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=bg_deleted#battlegroups", status_code=303)


@app.post("/guild/{guild_id}/my-ally/member/{discord_id}/update")
async def my_ally_member_update(request: Request, guild_id: str, discord_id: str,
                                 travian_name: str = Form(""), note: str = Form(""),
                                 role_id: str = Form(""), wing: str = Form("0")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        return JSONResponse({"error": "not owner"}, status_code=403)
    rid = int(role_id) if role_id and role_id.isdigit() else None
    await database.update_ally_member(ally_group["id"], discord_id, travian_name, note, rid, int(wing or 0))
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=saved", status_code=303)


@app.post("/guild/{guild_id}/my-ally/member/{discord_id}/remove")
async def my_ally_member_remove(request: Request, guild_id: str, discord_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        return JSONResponse({"error": "not owner"}, status_code=403)
    await database.remove_ally_member(ally_group["id"], discord_id)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=removed", status_code=303)


@app.post("/guild/{guild_id}/my-ally/member/{discord_id}/approve")
async def my_ally_member_approve(request: Request, guild_id: str, discord_id: str):
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        ally_group = await database.get_ally_group_for_guild(guild_id)
        if not ally_group or not await has_perm(request, guild_id, "ally_manage"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.set_ally_member_status(ally_group["id"], discord_id, "approved")
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=approved", status_code=303)


@app.post("/guild/{guild_id}/my-ally/member/{discord_id}/reject")
async def my_ally_member_reject(request: Request, guild_id: str, discord_id: str):
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        ally_group = await database.get_ally_group_for_guild(guild_id)
        if not ally_group or not await has_perm(request, guild_id, "ally_manage"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.remove_ally_member(ally_group["id"], discord_id)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=rejected", status_code=303)


@app.post("/guild/{guild_id}/my-ally/set-entry-role")
async def my_ally_set_entry_role(request: Request, guild_id: str):
    """Set which role new Discord members automatically receive on join."""
    session, err = _require_session(request)
    if err: return err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        if not await has_perm(request, guild_id, "ally_manage"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        ally_group = await database.get_ally_group_for_guild(guild_id)
    if not ally_group:
        return JSONResponse({"error": "no group"}, status_code=404)
    form = await request.form()
    role_id_str = form.get("entry_role_id", "")
    role_id = int(role_id_str) if role_id_str.isdigit() else None
    await database.set_ally_group_entry_role(ally_group["id"], role_id)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=entry_role_saved", status_code=303)


@app.get("/guild/{guild_id}/my-ally/troop-roles", response_class=HTMLResponse)
async def my_ally_troop_roles_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard", status_code=303)
    if not await has_perm(request, guild_id, "ally_manage"):
        return HTMLResponse("Forbidden", status_code=403)
    roles      = await database.get_troop_roles(guild_id)
    crop_map   = await database.get_troop_crop_map(guild_id)
    all_troops = sorted(database.TROOP_ROLE_DEFAULTS.keys())
    flash = request.query_params.get("flash")
    return templates.TemplateResponse("troop_roles.html", {
        "request": request, "guild_id": guild_id, "guild": guild,
        "roles": roles, "all_troops": all_troops, "flash": flash,
        "crop_map": crop_map, "default_crop_map": database.CROP_MAP,
    })


@app.post("/guild/{guild_id}/my-ally/troop-roles")
async def my_ally_troop_roles_save(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    if not await has_perm(request, guild_id, "ally_manage"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    valid_roles = {"off", "def", "both", "scout", "siege", "ignore"}
    new_roles = {}
    new_crops: dict[str, float] = {}
    for k, v in form.items():
        if k.startswith("crop__"):
            troop = k[6:]
            try:
                val = float(v)
                if val >= 0:
                    new_crops[troop] = val
            except (ValueError, TypeError):
                pass
        elif v in valid_roles:
            new_roles[k] = v
    await database.save_troop_roles(guild_id, new_roles, new_crops)
    return RedirectResponse(f"/guild/{guild_id}/my-ally/troop-roles?flash=saved", status_code=303)


# ---------------------------------------------------------------------------
# Ally Bonus Routes
# ---------------------------------------------------------------------------

async def _require_ally_owner(request: Request, guild_id: str):
    """Return (ally_group, None) or (None, redirect) — owner or ally_manage only."""
    session, err = _require_session(request)
    if err: return None, err
    err = _require_guild(session, guild_id)
    if err: return None, err
    uid = session.get("uid", "")
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    if not ally_group:
        ally_group = await database.get_ally_group_for_guild(guild_id)
        if not ally_group:
            return None, RedirectResponse(f"/guild/{guild_id}/my-ally", status_code=303)
        if not await has_perm(request, guild_id, "ally_manage"):
            return None, RedirectResponse(f"/guild/{guild_id}/my-ally?flash=no_permission", status_code=303)
    return ally_group, None


@app.post("/guild/{guild_id}/my-ally/bonuses/add")
async def my_ally_bonus_add(request: Request, guild_id: str):
    ally_group, err = await _require_ally_owner(request, guild_id)
    if err: return err
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=bonus_empty#bonus", status_code=303)
    max_level = int(form.get("max_level") or 20)
    description = (form.get("description") or "").strip()
    await database.add_ally_bonus(ally_group["id"], name, max_level, description)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=bonus_added#bonus", status_code=303)


@app.post("/guild/{guild_id}/my-ally/bonuses/{bonus_id}/update")
async def my_ally_bonus_update(request: Request, guild_id: str, bonus_id: int):
    ally_group, err = await _require_ally_owner(request, guild_id)
    if err: return err
    form = await request.form()
    name = (form.get("name") or "").strip()
    max_level = int(form.get("max_level") or 20)
    current_level = int(form.get("current_level") or 0)
    description = (form.get("description") or "").strip()
    await database.update_ally_bonus(bonus_id, ally_group["id"], name, max_level, current_level, description)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=bonus_saved#bonus", status_code=303)


@app.post("/guild/{guild_id}/my-ally/bonuses/{bonus_id}/delete")
async def my_ally_bonus_delete(request: Request, guild_id: str, bonus_id: int):
    ally_group, err = await _require_ally_owner(request, guild_id)
    if err: return err
    await database.delete_ally_bonus(bonus_id, ally_group["id"])
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=bonus_deleted#bonus", status_code=303)


@app.post("/guild/{guild_id}/my-ally/bonuses/reorder")
async def my_ally_bonus_reorder(request: Request, guild_id: str):
    ally_group, err = await _require_ally_owner(request, guild_id)
    if err: return err
    body = await request.json()
    ordered_ids = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    await database.reorder_ally_bonuses(ally_group["id"], ordered_ids)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Rights Management — inline role assignment per member
# ---------------------------------------------------------------------------

@app.post("/guild/{guild_id}/my-ally/member/{discord_id}/set-role")
async def my_ally_set_member_role(request: Request, guild_id: str, discord_id: str):
    ally_group, err = await _require_ally_owner(request, guild_id)
    if err: return err
    form = await request.form()
    role_id_raw = form.get("role_id", "")
    rid = int(role_id_raw) if role_id_raw and role_id_raw.isdigit() else None
    await database.update_ally_member(ally_group["id"], discord_id, None, None, rid, None)
    return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=role_set#rechte", status_code=303)


# ---------------------------------------------------------------------------
# My Ally — Member Detail Page
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/my-ally/member/{discord_id}/detail", response_class=HTMLResponse)
async def my_ally_member_detail(request: Request, guild_id: str, discord_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    uid = session.get("uid", "")

    # Require membership or ownership
    ally_group = await database.get_ally_group_for_owner(guild_id, uid)
    membership = await database.get_ally_membership(guild_id, uid) if not ally_group else None
    guild_group = await database.get_ally_group_for_guild(guild_id)
    if not ally_group and not membership:
        return RedirectResponse(f"/guild/{guild_id}/my-ally")

    group = ally_group or guild_group
    if not group:
        return RedirectResponse(f"/guild/{guild_id}/my-ally")

    # Get the specific member's ally_members record
    members = await database.get_ally_members(group["id"])
    member = next((m for m in members if m["discord_id"] == discord_id), None)

    # Troop data — cross-guild fallback so data from other guild contexts is found
    troops = await database.get_member_troops_single(guild_id, discord_id)

    # Enrich villages with population from map_snapshots by coordinate
    if troops:
        villages = troops.get("villages") or []
        coords = [(v.get("x"), v.get("y")) for v in villages if v.get("x") is not None]
        pop_map: dict = {}
        if coords:
            pop_map = await database.get_village_populations_by_coords(guild_id, coords)
        for v in villages:
            key = (v.get("x"), v.get("y"))
            v["population"] = pop_map.get(key, 0)
        troops["villages"] = villages

    # Editor check
    is_editor = bool(ally_group) or await has_perm(request, guild_id, "ally_manage")

    # Growth history
    growth = await database.get_member_growth(guild_id, [discord_id])
    growth_data = growth.get(discord_id, [])

    return templates.TemplateResponse("my_ally_member_detail.html", {
        "request": request, "guild": guild,
        "member": member, "troops": troops,
        "is_editor": is_editor,
        "growth_data": growth_data,
    })


# ---------------------------------------------------------------------------
# Enemy Troop Entries
# ---------------------------------------------------------------------------

@app.post("/guild/{guild_id}/enemies/{player_name}/troops/add")
async def enemy_troops_add(request: Request, guild_id: str, player_name: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    form = await request.form()
    off_troops    = int(form.get("off_troops")   or 0)
    def_troops    = int(form.get("def_troops")   or 0)
    total_troops  = int(form.get("total_troops") or 0)
    notes         = (form.get("notes")         or "").strip()
    entry_time    = (form.get("entry_time")    or "").strip()
    troop_details = (form.get("troop_details") or "").strip()
    village_name  = (form.get("village_name")  or "").strip()
    reported_by   = session.get("username", "")
    await database.add_enemy_troop_entry(
        guild_id, player_name, off_troops, def_troops, total_troops,
        notes, reported_by, entry_time, troop_details, village_name,
    )
    return RedirectResponse(
        f"/guild/{guild_id}/enemies/{player_name}?flash=troops_added", status_code=303
    )


@app.post("/guild/{guild_id}/enemies/{player_name}/troops/{entry_id}/delete")
async def enemy_troops_delete(request: Request, guild_id: str, player_name: str, entry_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_enemy_troop_entry(entry_id, guild_id)
    return RedirectResponse(
        f"/guild/{guild_id}/enemies/{player_name}?flash=troops_deleted", status_code=303
    )


# ---------------------------------------------------------------------------
# Enemy Village Tracking Routes
# ---------------------------------------------------------------------------

@app.post("/guild/{guild_id}/enemies/{player_name}/villages/import")
async def enemy_villages_import(request: Request, guild_id: str, player_name: str):
    from stats_parser import parse_player_profile
    from urllib.parse import unquote
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    form = await request.form()
    raw_text    = (form.get("profile_text") or "").strip()
    snapshot_at = (form.get("snapshot_at") or "").strip()
    player_name = unquote(player_name)

    if not raw_text:
        return RedirectResponse(
            f"/guild/{guild_id}/enemies/{player_name}?flash=village_empty", status_code=303)
    if not snapshot_at:
        from datetime import datetime as _dt
        snapshot_at = _dt.utcnow().strftime("%Y-%m-%dT%H:%M")

    parsed = parse_player_profile(raw_text)
    villages = parsed.get("villages", [])
    if not villages:
        return RedirectResponse(
            f"/guild/{guild_id}/enemies/{player_name}?flash=village_parse_fail", status_code=303)

    # Ensure enemy exists in DB
    detected_name = parsed.get("player_name") or player_name
    await database.upsert_enemy(guild_id, player_name)

    snap_id, events = await database.save_enemy_village_snapshot(
        guild_id, player_name, snapshot_at,
        imported_by=session.get("username",""),
        raw_text=raw_text,
        villages=villages,
    )
    evt_count = len(events)
    return RedirectResponse(
        f"/guild/{guild_id}/enemies/{player_name}?flash=villages_imported&vcount={len(villages)}&ecount={evt_count}",
        status_code=303
    )


@app.post("/guild/{guild_id}/enemies/add")
async def enemy_add(request: Request, guild_id: str):
    """Manually add an enemy player to the kartei."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    form = await request.form()
    player_name   = (form.get("player_name") or "").strip()
    coordinates   = (form.get("coordinates") or "").strip()
    village       = (form.get("village") or "").strip()
    alliance_name = (form.get("alliance_name") or "").strip()
    if not player_name:
        return RedirectResponse(f"/guild/{guild_id}/enemies", status_code=303)
    await database.upsert_enemy(guild_id, player_name, coordinates=coordinates, village=village)
    if alliance_name:
        await database.update_enemy_meta(guild_id, player_name, alliance_name=alliance_name)
    return RedirectResponse(
        f"/guild/{guild_id}/enemies/{player_name}", status_code=303
    )


@app.get("/ally/join/{token}", response_class=HTMLResponse)
async def ally_join_page(request: Request, token: str):
    session = get_session(request)
    group = await database.get_ally_group_by_token(token)
    if not group:
        # check wing tokens
        group = await database.get_ally_group_by_wing_token(token)
    wing = 0
    if group:
        if group.get("wing1_token") == token:
            wing = 1
        elif group.get("wing2_token") == token:
            wing = 2
    accepted = request.query_params.get("accepted")
    return templates.TemplateResponse("ally_join.html", {
        "request": request, "group": group, "token": token,
        "wing": wing, "session": session, "accepted": accepted,
    })


@app.post("/ally/join/{token}")
async def ally_join_accept(request: Request, token: str, travian_name: str = Form("")):
    session, err = _require_session(request)
    if err: return RedirectResponse(f"/ally/join/{token}?need_login=1", status_code=303)
    group = await database.get_ally_group_by_token(token)
    wing = 0
    if not group:
        group = await database.get_ally_group_by_wing_token(token)
        if group:
            wing = 1 if group.get("wing1_token") == token else 2
    if not group:
        return RedirectResponse(f"/ally/join/{token}?error=invalid", status_code=303)
    uid = session.get("uid", "")
    if uid == group["owner_discord_id"]:
        return RedirectResponse(f"/ally/join/{token}?error=own_group", status_code=303)
    tname = travian_name.strip()
    guild_id = group["guild_id"]
    # Verify: auto-approve if travian_name matches a known alliance_member
    import aiosqlite as _aio_join
    status = "pending"
    if tname:
        async with _aio_join.connect(database.DB_PATH) as _db:
            _db.row_factory = _aio_join.Row
            async with _db.execute(
                "SELECT 1 FROM alliance_members WHERE guild_id=? AND LOWER(player_name)=LOWER(?) LIMIT 1",
                (guild_id, tname)
            ) as cur:
                if await cur.fetchone():
                    status = "approved"
    await database.join_ally_group(group["id"], uid, session.get("username",""), tname, wing=wing, status=status)
    guild_id = group["guild_id"]
    if status == "approved":
        return RedirectResponse(f"/guild/{guild_id}/my-ally?flash=joined", status_code=303)
    return RedirectResponse(f"/ally/join/{token}?pending=1", status_code=303)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Battle Report Parser
# ---------------------------------------------------------------------------

# Unit name tables — maps various spellings → canonical English key
_UNIT_MAP: dict[str, str] = {
    # Romans
    "legionär": "legionnaire", "legionäre": "legionnaire", "legionnaire": "legionnaire", "legionnaires": "legionnaire",
    "prätorianer": "praetorian", "praetorian": "praetorian", "praetorians": "praetorian",
    "imperianer": "imperian", "imperian": "imperian", "imperians": "imperian",
    "equites legati": "equites_legati", "späher": "equites_legati",
    "equites imperatoris": "equites_imperatoris",
    "equites caesaris": "equites_caesaris",
    "feuerkatapult": "fire_catapult", "fire catapult": "fire_catapult",
    "rammbock": "battering_ram", "battering ram": "battering_ram",
    "senator": "senator",
    # Teutons
    "keulenschwinger": "clubswinger", "clubswinger": "clubswinger", "clubswingers": "clubswinger",
    "speerkämpfer": "spearman", "spearman": "spearman", "spearmen": "spearman",
    "axtkämpfer": "axeman", "axeman": "axeman", "axemen": "axeman",
    "aufklärerin": "scout", "scout": "scout", "scouts": "scout",
    "paladin": "paladin", "paladine": "paladin",
    "teutonischer ritter": "teutonic_knight", "teutonic knight": "teutonic_knight",
    "katapult": "catapult", "catapult": "catapult",
    "häuptling": "chief", "chief": "chief",
    # Gauls
    "phalanx": "phalanx",
    "schwertkämpfer": "swordsman", "swordsman": "swordsman", "swordsmen": "swordsman",
    "pfadfinder": "pathfinder", "pathfinder": "pathfinder",
    "thureophor": "thureophor", "theurophor": "thureophor",
    "druide": "druidrider", "druidenreiter": "druidrider", "druid rider": "druidrider",
    "haeduer": "haeduan", "haeduan": "haeduan",
    "trebuchet": "trebuchet", "trebusche": "trebuchet",
    # Huns
    "mercenary": "mercenary", "söldner": "mercenary",
    "bowman": "bowman", "bogenschütze": "bowman",
    "spotter": "spotter",
    "steppe rider": "steppe_rider", "steppenreiter": "steppe_rider",
    "marksman": "marksman", "heckenschütze": "marksman",
    "marauder": "marauder",
    "ram": "battering_ram",
    # Egyptians
    "slave militia": "slave_militia", "sklavenmiliz": "slave_militia",
    "ash warden": "ash_warden",
    "khopesh warrior": "khopesh_warrior",
    "sopdu explorer": "sopdu_explorer",
    "anhur guard": "anhur_guard",
    "resheph chariot": "resheph_chariot",
    # Spartans
    "hoplite": "hoplite", "hoplit": "hoplite",
    "sentinel": "sentinel",
    "shieldsman": "shieldsman",
    "twinsteel thureophoros": "thureophoros",
    "elpida rider": "elpida_rider",
    "corinthian crusher": "corinthian_crusher",
    # Common
    "siedler": "settler", "settler": "settler", "settlers": "settler",
    "held": "hero", "hero": "hero",
}

# Training time in seconds per unit at level 20 barracks/stable/workshop (Travian T4, 1× server).
# Level 20 buildings train ~2.78× faster than level 1 — these are the endgame reference values.
_TROOP_BUILD_SECONDS: dict[str, int] = {
    # Romans (Barracks / Stable / Workshop lv20)
    "legionnaire":         576,
    "praetorian":          648,
    "imperian":            720,
    "equites_legati":      792,
    "equites_imperatoris": 1152,
    "equites_caesaris":    1440,
    "battering_ram":       1728,
    "fire_catapult":       3240,
    "senator":             32652,
    # Teutons
    "clubswinger":         360,
    "spearman":            576,
    "axeman":              696,
    "scout":               792,
    "paladin":             1152,
    "teutonic_knight":     1440,
    "catapult":            2592,
    "chief":               25344,
    # Gauls
    "phalanx":             576,
    "swordsman":           720,
    "pathfinder":          864,
    "thureophor":          720,
    "druidrider":          1008,
    "haeduan":             1440,
    "trebuchet":           3240,
    "chieftain":           25344,
    # Huns
    "mercenary":           324,
    "bowman":              504,
    "spotter":             720,
    "steppe_rider":        1008,
    "marksman":            1296,
    "marauder":            1584,
    # Egyptians
    "slave_militia":       324,
    "ash_warden":          576,
    "khopesh_warrior":     720,
    "sopdu_explorer":      792,
    "anhur_guard":         1152,
    "resheph_chariot":     1440,
    # Spartans
    "hoplite":             576,
    "sentinel":            648,
    "shieldsman":          720,
    "thureophoros":        792,
    "elpida_rider":        1152,
    "corinthian_crusher":  1440,
    # Generic
    "settler":             9600,
    "hero":                0,
}


def _build_seconds(troops: dict) -> int:
    """Total training time in seconds to rebuild the given troop dict."""
    return sum(_TROOP_BUILD_SECONDS.get(unit, 1800) * cnt
               for unit, cnt in (troops or {}).items() if cnt and cnt > 0)


def _fmt_duration(seconds: int) -> str:
    """Format seconds as human-readable duration (e.g. '3d 14h 22m')."""
    if seconds <= 0:
        return "—"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m and not d: parts.append(f"{m}m")
    return " ".join(parts) or "<1m"


_TROOP_TABLE_MARKERS = {
    "de": ["gesendet", "verluste", "im dorf", "angreifer", "verteidiger"],
    "en": ["sent", "losses", "in village", "attacker", "defender"],
}

_SIEGE_UNITS = {"catapult", "trebuchet", "battering_ram", "fire_catapult"}
_CHEAP_UNITS = {
    "clubswinger", "legionnaire", "phalanx", "spearman", "axeman",
    "mercenary", "slave_militia", "hoplite", "sentinel", "shieldsman",
}
_STRONG_OFF  = {
    "teutonic_knight", "equites_imperatoris", "equites_caesaris",
    "haeduan", "steppe_rider", "marksman", "marauder",
    "resheph_chariot", "corinthian_crusher", "elpida_rider",
}

_SIEGE_REAL_THRESHOLD = 20  # ≥ this many siege → never auto-fake

def _detect_fake(troops_sent: dict) -> dict:
    """Analyse troop composition and return fake likelihood."""
    if not troops_sent:
        return {"fake_confidence": "none", "fake_reason": ""}

    total      = sum(v for v in troops_sent.values() if v)
    siege      = sum(v for k, v in troops_sent.items() if k in _SIEGE_UNITS and v)
    strong_off = sum(v for k, v in troops_sent.items() if k in _STRONG_OFF and v)

    if total == 0:
        return {"fake_confidence": "none", "fake_reason": ""}

    # ≥ 20 siege → definitely real attack, never flag as fake
    if siege >= _SIEGE_REAL_THRESHOLD:
        return {"fake_confidence": "none", "fake_reason": ""}

    siege_ratio  = siege / total
    strong_ratio = strong_off / total

    if siege >= 1 and total <= 60:
        return {"fake_confidence": "high",
                "fake_reason": f"{total} troops · {siege} siege — classic fake"}
    if siege >= 1 and total <= 200 and strong_ratio < 0.05:
        return {"fake_confidence": "medium",
                "fake_reason": f"{total} troops · {siege} siege · no strong off — likely fake"}
    if total <= 30 and siege == 0:
        return {"fake_confidence": "medium",
                "fake_reason": f"Only {total} troops sent — possible fake/distraction"}
    if siege >= 1 and siege_ratio > 0.05 and strong_ratio < 0.1:
        return {"fake_confidence": "low",
                "fake_reason": f"{siege} siege in {total} troops ({siege_ratio:.0%}) — fake-like ratio"}

    return {"fake_confidence": "none", "fake_reason": ""}


def _parse_battle_report(text: str) -> dict:
    """Parse a pasted Travian battle/spy/farm report text.
    Returns a dict with all extracted fields. Missing fields are None/empty.
    """
    import json as _json
    from datetime import datetime as _dt

    result: dict = {
        "report_type": "unknown",
        "report_date": None,
        "attacker_name": None, "attacker_village": None,
        "attacker_x": None, "attacker_y": None,
        "defender_name": None, "defender_village": None,
        "defender_x": None, "defender_y": None,
        "troops_sent": {}, "troops_lost": {}, "def_troops": {},
        "spy_resources": {}, "plunder": {}, "plunder_total": 0,
        "luck": None, "hero_hp": None,
        "raw_text": text,
        "parse_warnings": [],
    }

    lines = [l.strip() for l in text.splitlines()]
    text_lower = text.lower()

    # ── Report type ────────────────────────────────────────────────────────
    for kw, rt in [
        (["spionage", "espionage", "spy report", "spionbericht"], "spy"),
        (["angriff", "attack", "raid report"], "attack"),
        (["verteidigung", "defense", "defence", "defending"], "defense"),
        (["marktbericht", "market report", "trade report"], "market"),
    ]:
        if any(k in text_lower for k in kw):
            result["report_type"] = rt
            break
    # Fallback: if resources plundered and no type yet, it's likely a farm
    if result["report_type"] == "unknown" and any(k in text_lower for k in ["beute", "plunder", "loot"]):
        result["report_type"] = "attack"

    # ── Clean text: strip Unicode direction marks, normalise whitespace ───────
    text_clean = re.sub(r'[‎‏‪-‮⁦-⁩­]', '', text)

    # ── Date ───────────────────────────────────────────────────────────────
    # Handles: 04.06.2026 23:04:02  and  04.06.26, 23:04:02
    date_re = re.compile(
        r'(\d{1,2})[./](\d{1,2})[./](\d{2,4})[,\s]+(\d{1,2}:\d{2}:\d{2})'
    )
    dm = date_re.search(text_clean)
    if dm:
        try:
            yr = dm.group(3)
            if len(yr) == 2:
                yr = "20" + yr
            result["report_date"] = f"{yr}-{int(dm.group(2)):02d}-{int(dm.group(1)):02d} {dm.group(4)}"
        except Exception:
            pass

    # ── Coordinates ────────────────────────────────────────────────────────
    coord_re = re.compile(r'\(\s*(-?\d+)\s*\|\s*(-?\d+)\s*\)')

    # ── Helper: extract player name and optional village from a raw string ──
    _VILLAGE_SUFFIX_RE = re.compile(
        r'\s+(?:from village|aus Dorf|von Dorf)\s+(.+?)(?:\s*\(|$)',
        re.IGNORECASE
    )

    def _clean_name(raw: str):
        """Return (player_name, village_name, (x, y)|None) from a raw line."""
        raw = re.sub(r'\[/?(?:player|village|ally)\]', '', raw, flags=re.IGNORECASE).strip()
        # Strip alliance tag: "[TAG] PlayerName" → "PlayerName"
        raw = re.sub(r'^\s*\[[^\]]{1,10}\]\s*', '', raw).strip()
        # Extract coordinates
        vm = coord_re.search(raw)
        coord = (int(vm.group(1)), int(vm.group(2))) if vm else None
        base = raw[:vm.start()].strip() if vm else raw

        # Extract village suffix: "... from village VillageName"
        vsm = _VILLAGE_SUFFIX_RE.search(base)
        if vsm:
            village = vsm.group(1).strip().rstrip(',').strip()
            player = base[:vsm.start()].strip()
        else:
            village = None
            player = base

        player = player.rstrip(',').strip() or None
        return player, village, coord

    # ── Player / village extraction ────────────────────────────────────────
    # Two formats:
    #   A) "Attacker: [TAG] Name from village X (x|y)"
    #   B) "Attacker\n[TAG] Name from village X"   (next line)
    lines = text_clean.splitlines()
    stripped = [l.strip() for l in lines]

    def _find_block(keyword_re: str) -> str | None:
        """Return the content on the keyword line (after ':') or the next non-empty line."""
        for idx, line in enumerate(stripped):
            m = re.match(keyword_re + r'\s*[:\-–]?\s*(.*)', line, re.IGNORECASE)
            if not m:
                continue
            inline = m.group(1).strip()
            if inline:
                return inline
            # Try next non-empty line
            for nxt in stripped[idx + 1: idx + 3]:
                if nxt:
                    return nxt
        return None

    att_raw = _find_block(r'(?:Angreifer|Attacker)')
    def_raw = _find_block(r'(?:Verteidiger|Defender)')
    att_origin = _find_block(r'(?:Herkunft(?:sdorf)?|Origin)')
    def_village = _find_block(r'(?:Verteidigt mit|Defending village)')

    if att_raw:
        pname, vname, coord = _clean_name(att_raw)
        result["attacker_name"] = pname
        if vname: result["attacker_village"] = vname
        if coord: result["attacker_x"], result["attacker_y"] = coord
    if att_origin:
        _, vname, coord = _clean_name(att_origin)
        if vname and not result.get("attacker_village"): result["attacker_village"] = vname
        if coord: result["attacker_x"], result["attacker_y"] = coord
    if def_raw:
        pname, vname, coord = _clean_name(def_raw)
        result["defender_name"] = pname
        if vname: result["defender_village"] = vname
        if coord: result["defender_x"], result["defender_y"] = coord
    if def_village:
        _, vname, coord = _clean_name(def_village)
        if vname and not result.get("defender_village"): result["defender_village"] = vname
        if coord: result["defender_x"], result["defender_y"] = coord

    # ── Resources / Plunder ────────────────────────────────────────────────
    # Format A: "Wood: 1500  Clay: 800  Iron: 600  Crop: 200"
    res_inline = re.compile(
        r'(?:Holz|Wood|Lumber)[:\s]+(\d[\d\.,]*)'
        r'.*?(?:Lehm|Ton|Clay)[:\s]+(\d[\d\.,]*)'
        r'.*?(?:Eisen|Iron)[:\s]+(\d[\d\.,]*)'
        r'.*?(?:Getreide|Crop|Grain|Korn)[:\s]+(\d[\d\.,]*)',
        re.IGNORECASE | re.DOTALL
    )
    def _ri(s): return int(re.sub(r'[.,\s]', '', s))

    for m in res_inline.finditer(text_clean):
        w, c, i, g = _ri(m.group(1)), _ri(m.group(2)), _ri(m.group(3)), _ri(m.group(4))
        ctx = text_clean[max(0, m.start()-80):m.start()].lower()
        is_spy = any(k in ctx for k in ["spion", "spy", "rohstoff", "resource", "im dorf", "in village"])
        if is_spy:
            result["spy_resources"] = {"wood": w, "clay": c, "iron": i, "crop": g, "total": w+c+i+g}
            if result["report_type"] == "unknown": result["report_type"] = "spy"
        else:
            result["plunder"] = {"wood": w, "clay": c, "iron": i, "crop": g}
            result["plunder_total"] = w + c + i + g

    # Format B: "Bounty\nW\nC\nI\nG\n(capacity)" — 4 consecutive numbers after Bounty/Beute
    if not result["plunder"]:
        bounty_m = re.search(
            r'(?:Bounty|Beute)\s*\n\s*(\d+)\s*\n\s*(\d+)\s*\n\s*(\d+)\s*\n\s*(\d+)',
            text_clean, re.IGNORECASE
        )
        if bounty_m:
            w, c, i, g = (int(bounty_m.group(k)) for k in (1,2,3,4))
            result["plunder"] = {"wood": w, "clay": c, "iron": i, "crop": g}
            result["plunder_total"] = w + c + i + g

    # ── Luck ───────────────────────────────────────────────────────────────
    luck_m = re.search(
        r'(?:Glück|Luck)[:\s]*(?:↑|↓|[+-])?\s*([+-]?\d+(?:[.,]\d+)?)\s*%',
        text_clean, re.IGNORECASE
    )
    if luck_m:
        try: result["luck"] = float(luck_m.group(1).replace(',', '.'))
        except Exception: pass

    # ── Hero HP ────────────────────────────────────────────────────────────
    hero_m = re.search(r'Hero\s*\d*\s*\n?\s*(\d+)\s*%', text_clean, re.IGNORECASE)
    if hero_m:
        try: result["hero_hp"] = int(hero_m.group(1))
        except Exception: pass

    # ── Troop table parsing ────────────────────────────────────────────────
    def _parse_troop_table(txt: str) -> dict[str, dict[str, int]]:
        """
        Handles two formats:
        A) Labeled rows:  "Sent:\t19\t0..."  /  "Losses:\t7\t0..."
        B) Unlabeled rows: header line + 1-3 numeric lines
           Convention (Travian browser copy):
             row 0 = sent, row 1 = survivors (if 3 rows), last row = losses
        Supports tab-separated AND space-separated (multi-space) columns.
        """
        out: dict[str, dict] = {"sent": {}, "lost": {}, "hospital": {}, "def": {}, "def_lost": {}, "def_hospital": {}}

        def _parse_nums(line: str) -> list[int | None]:
            """Parse a row of numbers, skip '?' marks."""
            parts = re.split(r'\s{2,}|\t', line.strip())
            result_nums = []
            for p in parts:
                p = p.strip()
                if p == '?' or p == '':
                    result_nums.append(None)
                else:
                    try:
                        result_nums.append(int(p.replace('.','').replace(',','').replace('−','-')))
                    except Exception:
                        result_nums.append(None)
            return result_nums

        clean_lines = [l.rstrip() for l in txt.splitlines()]
        i = 0
        tables_found = 0
        while i < len(clean_lines):
            line = clean_lines[i].strip()
            parts = re.split(r'\s{2,}|\t', line)
            parts = [p.strip() for p in parts if p.strip()]

            # Detect unit header: ≥2 parts matching known unit names
            mapped = [_UNIT_MAP.get(p.lower()) for p in parts]
            hits = sum(1 for m in mapped if m)
            if hits < 2:
                i += 1
                continue

            unit_header = [_UNIT_MAP.get(p.lower(), p.lower()) for p in parts]
            dest_key = "def" if tables_found > 0 else "sent"

            # Collect value rows
            value_rows: list[tuple[str | None, list]] = []
            j = i + 1
            while j < min(i + 10, len(clean_lines)):
                vline = clean_lines[j].strip()
                if not vline:
                    j += 1
                    break

                # Check for labeled row
                label_m = re.match(
                    r'(gesendet|sent|verluste|losses?|im dorf|in village|überlebende|survivors?'
                    r'|im hospital|in hospital|hospital)'
                    r'\s*[:\-–]?\s*(.*)',
                    vline, re.IGNORECASE
                )
                if label_m:
                    label = label_m.group(1).lower()
                    nums_str = label_m.group(2).strip() or clean_lines[j + 1].strip() if j + 1 < len(clean_lines) else ""
                    nums = _parse_nums(nums_str)
                    lbl_dest = ("sent"     if any(k in label for k in ["gesendet","sent"]) else
                                "lost"     if any(k in label for k in ["verluste","loss"]) else
                                "hospital" if any(k in label for k in ["hospital"]) else
                                "def"      if any(k in label for k in ["im dorf","in village"]) else None)
                    if lbl_dest:
                        value_rows.append((lbl_dest, nums))
                    j += 1
                    continue

                # Unlabeled: strict numeric-only check
                # CRITICAL: text lines (Information, Bounty, etc.) MUST end the table.
                # Only real "?" markers (spy-blocked troops) are kept as None placeholders.
                vparts = re.split(r'\s{2,}|\t', vline)
                vparts = [p.strip() for p in vparts if p.strip()]

                if not vparts:
                    break  # blank line

                # Pure "?" row — spy-blocked defender troops
                if all(p == '?' for p in vparts):
                    value_rows.append((None, [None] * len(vparts)))
                    j += 1
                    continue

                # Try parsing every part as integer — if ANY part fails, stop the table
                nums = []
                row_ok = True
                for p in vparts:
                    try:
                        nums.append(int(p.replace('.', '').replace(',', '').replace('−', '-')))
                    except ValueError:
                        row_ok = False
                        break

                if not row_ok or not nums:
                    break  # text line — end of troop table

                value_rows.append((None, nums))
                j += 1

            # Assign unlabeled rows by position:
            # Travian always shows: Sent / [Survivors] / Losses
            # 1 row  → sent only (can't compute losses)
            # 2 rows → sent, losses
            # 3 rows → sent, survivors (skip), losses
            unlabeled = [(dest, nums) for dest, nums in value_rows if dest is None]
            labeled   = [(dest, nums) for dest, nums in value_rows if dest is not None]

            is_defender_table = tables_found > 0

            if unlabeled and not labeled:
                if len(unlabeled) == 1:
                    labeled = [("troops", unlabeled[0][1])]
                elif len(unlabeled) == 2:
                    # Row 0=sent, Row 1=dead losses (no hospital)
                    labeled = [("troops", unlabeled[0][1]), ("losses", unlabeled[1][1])]
                elif len(unlabeled) >= 3:
                    # Row 0=sent, Row 1=dead (permanent), Row 2=hospital (healable wounded)
                    # Survivors = sent - dead - hospital (return home implicitly)
                    labeled = [("troops", unlabeled[0][1]), ("losses", unlabeled[1][1]), ("hospital", unlabeled[2][1])]

            for dest, nums in labeled:
                if is_defender_table:
                    actual_dest = ("def"          if dest == "troops"   else
                                   "def_hospital" if dest == "hospital" else
                                   "def_lost")
                else:
                    actual_dest = ("sent"     if dest == "troops"   else
                                   "hospital" if dest == "hospital" else
                                   "lost")

                if actual_dest not in out:
                    out[actual_dest] = {}
                for ui, unit in enumerate(unit_header):
                    if ui < len(nums) and nums[ui] is not None and nums[ui] > 0:
                        out[actual_dest][unit] = out[actual_dest].get(unit, 0) + nums[ui]

            tables_found += 1
            i = j

        return out

    troop_data = _parse_troop_table(text_clean)
    result["troops_sent"]     = troop_data.get("sent", {})
    result["troops_lost"]     = troop_data.get("lost", {})
    result["troops_hospital"] = troop_data.get("hospital", {})
    result["def_troops"]      = troop_data.get("def", {})
    result["def_troops_lost"] = troop_data.get("def_lost", {})
    result["def_troops_hospital"] = troop_data.get("def_hospital", {})

    # ── Building damage ────────────────────────────────────────────────────────
    # Matches lines like:
    #   "Cropland level 10 destroyed"          (EN)
    #   "City Wall level 5 destroyed"
    #   "Getreidefeld Stufe 10 zerstört"       (DE)
    #   "Stadtmauer Stufe 5 zerstört"
    #   "Earth Wall level 3 damaged to level 1"
    #   "Erdwall Stufe 3 auf Stufe 1 beschädigt"
    _BLDG_RE = re.compile(
        r'([A-ZÄÖÜa-zäöü][A-Za-zäöüÄÖÜ\s]+?)'   # building name
        r'\s+(?:level|stufe|Stufe|Level)\s+(\d+)'  # "level N"
        r'(?:'
            r'\s+(?:destroyed|zerstört)'           # destroyed
            r'|'
            r'\s+(?:damaged(?:\s+to\s+level\s+(\d+))?'  # damaged [to level M]
            r'|(?:auf\s+Stufe\s+(\d+)\s+)?beschädigt)'  # DE: beschädigt
        r')',
        re.IGNORECASE
    )
    buildings_hit: list[dict] = []
    for line in text_clean.splitlines():
        m = _BLDG_RE.search(line.strip())
        if not m:
            continue
        bname = m.group(1).strip()
        lvl_before = int(m.group(2))
        # destroyed or damaged?
        line_l = line.lower()
        destroyed = any(k in line_l for k in ("destroyed", "zerstört"))
        lvl_after_str = m.group(3) or m.group(4)
        lvl_after = int(lvl_after_str) if lvl_after_str else (0 if destroyed else lvl_before - 1)
        buildings_hit.append({
            "building": bname,
            "level_before": lvl_before,
            "level_after": lvl_after,
            "destroyed": destroyed,
        })
    result["buildings_hit"] = buildings_hit

    # ── Fake detection ─────────────────────────────────────────────────────────
    fake_info = _detect_fake(result.get("troops_sent", {}))
    result["fake_confidence"] = fake_info["fake_confidence"]
    result["fake_reason"]     = fake_info["fake_reason"]

    return result


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

@app.get("/guild/{guild_id}/farming/inactive-search", response_class=HTMLResponse)
async def farming_inactive_search(
    request: Request,
    guild_id: str,
    ref_x: Optional[int] = None,
    ref_y: Optional[int] = None,
    max_pop_increase: Optional[int] = None,
    min_pop: Optional[int] = None,
    max_pop: Optional[int] = None,
    min_player_pop: Optional[int] = None,
    max_player_pop: Optional[int] = None,
    min_dist: Optional[float] = None,
    max_dist: Optional[float] = None,
    player_filter: str = "",
    alliance_filter: str = "",
    exclude_players: str = "",
    exclude_alliances: str = "",
    include_natars: bool = False,
    tribes: Optional[List[int]] = Query(default=None),
    in_farmlist: str = "",
    searched: bool = False,
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    tw_world = (guild.get("tw_world") or "").strip()
    snap_count = await database.get_snapshot_count(guild_id)
    result = {"villages": [], "snap_dates": [], "total": 0}

    # Apply defaults for optional numeric params
    ref_x = ref_x or 0
    ref_y = ref_y or 0
    max_pop_increase = max_pop_increase if max_pop_increase is not None else 0
    min_pop = min_pop or 0
    max_pop = max_pop if max_pop is not None else 9999
    min_player_pop = min_player_pop or 0
    max_player_pop = max_player_pop if max_player_pop is not None else 999999
    min_dist = min_dist or 0.0
    max_dist = max_dist if max_dist is not None else 9999.0

    uid = session.get("uid", "")
    farmlist_xy_lookup = await database.get_farmlist_xy_lookup(guild_id, uid)
    has_farmlist = bool(farmlist_xy_lookup)

    if searched and snap_count >= 1:
        result = await database.search_inactive_advanced(
            guild_id=guild_id,
            ref_x=ref_x, ref_y=ref_y,
            max_pop_increase=max_pop_increase,
            min_pop=min_pop, max_pop=max_pop,
            min_player_pop=min_player_pop, max_player_pop=max_player_pop,
            min_distance=min_dist, max_distance=max_dist,
            player_filter=player_filter,
            alliance_filter=alliance_filter,
            exclude_players=exclude_players,
            exclude_alliances=exclude_alliances,
            include_natars=include_natars,
            tribes=tribes or None,
            limit=300,
        )
        # Annotate villages with farmlist info
        for v in result.get("villages", []):
            key = (v["x"], v["y"])
            v["farmlist_groups"] = farmlist_xy_lookup.get(key, [])
        # Apply in_farmlist filter
        if in_farmlist == "no":
            result["villages"] = [v for v in result["villages"] if not v["farmlist_groups"]]
        elif in_farmlist == "yes":
            result["villages"] = [v for v in result["villages"] if v["farmlist_groups"]]
        result["total"] = len(result["villages"])

    # Alliance names from snapshot for autocomplete
    alliance_names = await database.get_alliance_names_from_snapshot(guild_id)

    return templates.TemplateResponse("farming_inactive_search.html", {
        "request": request,
        "guild": guild,
        "tw_world": tw_world,
        "snap_count": snap_count,
        "result": result,
        "searched": searched,
        # Re-pass filter values
        "ref_x": ref_x, "ref_y": ref_y,
        "max_pop_increase": max_pop_increase,
        "min_pop": min_pop, "max_pop": max_pop,
        "min_player_pop": min_player_pop, "max_player_pop": max_player_pop,
        "min_dist": min_dist, "max_dist": max_dist,
        "player_filter": player_filter,
        "alliance_filter": alliance_filter,
        "exclude_players": exclude_players,
        "exclude_alliances": exclude_alliances,
        "include_natars": include_natars,
        "tribes": tribes or [],
        "in_farmlist": in_farmlist,
        "has_farmlist": has_farmlist,
        "alliance_names": [a["alliance_name"] for a in alliance_names],
    })


@app.get("/guild/{guild_id}/farming", response_class=HTMLResponse)
async def farming_page(
    request: Request,
    guild_id: str,
    saved: str = "",
    tab: str = "inactive",
    q: str = "",
    # Basic inactive filters (str to handle empty string from form)
    min_days: str = "1",
    min_pop: str = "0",
    max_pop: str = "9999",
    # Advanced filters — all str to avoid FastAPI parse errors on empty inputs
    ref_x: str = "",
    ref_y: str = "",
    min_dist: str = "",
    max_dist: str = "",
    min_player_pop: str = "",
    max_player_pop: str = "",
    max_pop_increase: str = "",
    player_filter: str = "",
    alliance_filter: str = "",
    exclude_players: str = "",
    exclude_alliances: str = "",
    include_natars: bool = False,
    include_ww: bool = False,
    tribes: Optional[List[int]] = Query(default=None),
    in_farmlist: str = "",
    advanced: bool = False,
    farmlist_id: Optional[int] = None,
    _offset: int = 0,
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    # Convert str params (empty string → default/None)
    def _int(v: str, default: int) -> int:
        try: return int(v) if v.strip() else default
        except (ValueError, AttributeError): return default
    def _float_or_none(v: str) -> "float | None":
        try: return float(v) if v.strip() else None
        except (ValueError, AttributeError): return None
    def _int_or_none(v: str) -> "int | None":
        try: return int(v) if v.strip() else None
        except (ValueError, AttributeError): return None

    min_days_i       = _int(min_days, 1)
    min_pop_i        = _int(min_pop, 0)
    max_pop_i        = _int(max_pop, 9999)
    ref_x_i          = _int_or_none(ref_x)
    ref_y_i          = _int_or_none(ref_y)
    min_dist_f       = _float_or_none(min_dist)
    max_dist_f       = _float_or_none(max_dist)
    min_player_pop_i = _int_or_none(min_player_pop)
    max_player_pop_i = _int_or_none(max_player_pop)
    max_pop_inc_i    = _int_or_none(max_pop_increase)

    uid = session.get("uid", "") or session.get("discord_id", "")
    is_admin = session.get("guilds") is None
    tw_world = (guild.get("tw_world") or "").strip()

    # Auto-fetch first snapshot if none exists
    auto_fetched = False
    auto_fetch_error = ""
    if tw_world and saved != "snapshots_cleared":
        snap_count_pre = await database.get_snapshot_count(guild_id)
        if snap_count_pre == 0:
            try:
                await _fetch_and_save_snapshot(guild_id, tw_world)
                auto_fetched = True
            except Exception as e:
                auto_fetch_error = str(e)

    import asyncio as _asyncio

    # ── Parallel batch 1: all independent metadata queries ──────────────────
    (
        farm_stats,
        snap_pop_range,
        farm_list,
        scout_village,
        own_village_ids,
        cross_reference,
        farmlist_analyses,
        alliance_names,
        growth_data,
    ) = await _asyncio.gather(
        database.get_farm_stats(guild_id),
        database.get_snapshot_pop_range(guild_id),
        database.get_farm_list(guild_id),
        database.get_scout_village(guild_id, uid),
        database.get_own_village_ids(guild_id, uid),
        database.get_farming_cross_reference(guild_id, min_days=min_days_i),
        database.get_farmlist_analyses(guild_id, uid, limit=20),
        database.get_alliance_names_from_snapshot(guild_id),
        database.get_player_growth(guild_id, limit=100),
    )

    # Enrich scout village with Travian village_id for newdid= links
    if scout_village and scout_village.get("x") is not None:
        sv_id = await database.get_village_id_by_xy(guild_id, scout_village["x"], scout_village["y"])
        scout_village = {**scout_village, "travian_village_id": sv_id}

    cross_ref_coords = {(r["x"], r["y"]) for r in cross_reference}
    farm_list_coords = {(f["x"], f["y"]) for f in farm_list}

    # Farmlist cross-reference — use selected or most recent analysis
    _fl_id = farmlist_id or (farmlist_analyses[0]["id"] if farmlist_analyses else None)
    farmlist_xy = await database.get_farmlist_xy_lookup(guild_id, uid, analysis_id=_fl_id)
    has_farmlist = bool(farmlist_xy)

    # Detect if any advanced filter is active
    _advanced_active = any([
        ref_x_i is not None, ref_y_i is not None,
        min_dist_f is not None, max_dist_f is not None,
        min_player_pop_i is not None, max_player_pop_i is not None,
        max_pop_inc_i is not None,
        player_filter.strip(), alliance_filter.strip(),
        exclude_players.strip(), exclude_alliances.strip(),
        include_natars, include_ww, tribes, in_farmlist,
    ])

    # Choose query: advanced (search_inactive_advanced) or basic (get_inactive_farms)
    if _advanced_active and farm_stats.get("snapshot_count", 0) >= 2:
        adv_result = await database.search_inactive_advanced(
            guild_id=guild_id,
            ref_x=ref_x_i or 0,
            ref_y=ref_y_i or 0,
            max_pop_increase=max_pop_inc_i if max_pop_inc_i is not None else 999,
            min_pop=min_pop_i,
            max_pop=max_pop_i if max_pop_i < 9999 else (snap_pop_range.get("max_pop", 9999) if snap_pop_range else 9999),
            min_player_pop=min_player_pop_i or 0,
            max_player_pop=max_player_pop_i if max_player_pop_i is not None else 999999,
            min_distance=min_dist_f or 0.0,
            max_distance=max_dist_f if max_dist_f is not None else 9999.0,
            player_filter=player_filter,
            alliance_filter=alliance_filter,
            exclude_players=exclude_players,
            exclude_alliances=exclude_alliances,
            include_natars=include_natars,
            include_ww=include_ww,
            tribes=tribes or [],
            limit=500,
        )
        inactive_farms_raw = adv_result.get("villages", [])
        for v in inactive_farms_raw:
            v["farmlist_groups"] = farmlist_xy.get((v["x"], v["y"]), [])
            v.setdefault("days_tracked", 0)
        if in_farmlist == "no":
            inactive_farms = [v for v in inactive_farms_raw if not v["farmlist_groups"]]
        elif in_farmlist == "yes":
            inactive_farms = [v for v in inactive_farms_raw if v["farmlist_groups"]]
        else:
            inactive_farms = inactive_farms_raw
    else:
        inactive_farms_raw = await database.get_inactive_farms(
            guild_id, min_days=min_days_i, min_pop=min_pop_i, max_pop=max_pop_i, include_ww=include_ww)
        for v in inactive_farms_raw:
            v["farmlist_groups"] = farmlist_xy.get((v["x"], v["y"]), [])
        if in_farmlist == "no":
            inactive_farms = [v for v in inactive_farms_raw if not v["farmlist_groups"]]
        elif in_farmlist == "yes":
            inactive_farms = [v for v in inactive_farms_raw if v["farmlist_groups"]]
        else:
            inactive_farms = inactive_farms_raw

    # ── Pagination: 100 per page (initial + AJAX) ────────────────────────────
    _PAGE_SIZE = 100
    inactive_farms_real_total = len(inactive_farms)
    inactive_farms = inactive_farms[_offset:_offset + _PAGE_SIZE]

    # ── AJAX mode: return JSON only (for load-more button) ───────────────────
    if request.query_params.get("_ajax") == "1":
        import json as _json
        ajax_farms = []
        for f in inactive_farms:
            coords = (f["x"], f["y"])
            ajax_farms.append({
                "x": f["x"], "y": f["y"],
                "village_name": f.get("village_name") or "",
                "player_name": f.get("player_name") or "",
                "alliance_name": f.get("alliance_name") or "",
                "tribe": f.get("tribe") or 0,
                "population": f.get("population") or 0,
                "days_tracked": f.get("days_tracked") or 0,
                "is_capital": 1 if f.get("is_capital") else 0,
                "village_type": int(f.get("village_type") or 0),
                "is_cross": coords in cross_ref_coords,
                "is_on_list": coords in farm_list_coords,
                "farmlist_groups": f.get("farmlist_groups") or [],
                "acc_delta": None,
                "d1": None, "d2": None, "d3": None, "d5": None,
                "d7": None, "d14": None,
            })
        return JSONResponse({
            "farms": ajax_farms,
            "total": inactive_farms_real_total,
            "offset": _offset,
            "has_more": (_offset + _PAGE_SIZE) < inactive_farms_real_total,
        })

    # ── Parallel batch 2: per-village stats (depend on inactive_farms result) ─
    _INITIAL_RENDER = 100  # rows rendered as Jinja HTML (= _PAGE_SIZE, no split needed)
    _farm_slice = inactive_farms[:100]  # fetch deltas for the 100 rendered farms
    result_coords = [(f["x"], f["y"]) for f in _farm_slice]
    result_players = list({f["player_name"] for f in _farm_slice if f.get("player_name")})
    pop_deltas, player_growth = await _asyncio.gather(
        database.get_bulk_village_pop_deltas(guild_id, result_coords),
        database.get_bulk_player_pop_growth(guild_id, result_players),
    )

    # Map search
    search_results = []
    search_error = ""
    snap_count_for_search = await database.get_snapshot_count(guild_id)
    if q.strip():
        if snap_count_for_search == 0 and tw_world:
            try:
                await _fetch_and_save_snapshot(guild_id, tw_world)
                snap_count_for_search = 1
            except Exception as e:
                search_error = f"Snapshot konnte nicht geladen werden: {e}"
        if not search_error:
            search_results = await database.search_map_snapshot(guild_id, q.strip())

    # All 100 farms rendered as Jinja HTML; no rest-JSON needed (AJAX handles paging)
    import json as _json
    inactive_farms_visible = inactive_farms  # all 100
    inactive_farms_rest_json = "[]"          # always empty — AJAX fetches next pages

    return templates.TemplateResponse("farming.html", {
        "request": request,
        "guild": guild,
        "is_admin": is_admin,
        "saved": saved,
        "farm_stats": farm_stats,
        "inactive_farms": inactive_farms_visible,
        "inactive_farms_total": inactive_farms_real_total,
        "inactive_farms_page_total": len(inactive_farms_visible),
        "inactive_farms_offset": _offset,
        "inactive_farms_has_more": (_offset + _PAGE_SIZE) < inactive_farms_real_total,
        "inactive_farms_rest_json": inactive_farms_rest_json,
        "pop_deltas": pop_deltas,
        "player_growth": player_growth,
        "farm_list": farm_list,
        "cross_reference": cross_reference,
        "cross_ref_coords": cross_ref_coords,
        "farm_list_coords": farm_list_coords,
        "min_days": min_days_i,
        "min_pop": min_pop_i,
        "max_pop": max_pop_i,
        "tab": tab,
        "q": q,
        "growth_data": growth_data,
        "search_results": search_results,
        "search_error": search_error,
        "auto_fetched": auto_fetched,
        "auto_fetch_error": auto_fetch_error,
        "snap_count_for_search": snap_count_for_search,
        "snap_pop_range": snap_pop_range,
        "scout_village": scout_village,
        "own_village_ids": own_village_ids,
        "farmlist_analyses": farmlist_analyses,
        "farmlist_id": _fl_id,
        # Advanced filter values
        "ref_x": ref_x_i or 0, "ref_y": ref_y_i or 0,
        "min_dist": min_dist_f or 0, "max_dist": max_dist_f or "",
        "min_player_pop": min_player_pop_i or 0,
        "max_player_pop": max_player_pop_i or "",
        "max_pop_increase": max_pop_inc_i if max_pop_inc_i is not None else "",
        "player_filter": player_filter,
        "alliance_filter": alliance_filter,
        "exclude_players": exclude_players,
        "exclude_alliances": exclude_alliances,
        "include_natars": include_natars,
        "include_ww": include_ww,
        "tribes": tribes or [],
        "in_farmlist": in_farmlist,
        "advanced": advanced or _advanced_active,
        "has_farmlist": has_farmlist,
        "alliance_names": [a["alliance_name"] for a in alliance_names],
    })


@app.post("/guild/{guild_id}/farming/import-farmlist")
async def farming_import_farmlist(
    request: Request, guild_id: str,
    farmlist_text: str = Form(""),
):
    """Import a farmlist paste directly from the farming intelligence page."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    farms = parse_farmlist(farmlist_text)
    if not farms:
        return RedirectResponse(f"/guild/{guild_id}/farming?tab=inactive&saved=farmlist_empty", status_code=303)
    # Compute stats and save as farmlist analysis
    from collections import defaultdict as _dd
    _grp: dict = _dd(list)
    for f in farms:
        _grp[f["group"]].append(f)
    group_stats = []
    for gn, gf in _grp.items():
        non_nat = [f for f in gf if not f["is_natar"] and not f["is_oasis"]]
        group_stats.append({
            "name": gn, "total": len(gf),
            "gut": sum(1 for f in gf if f["rating"] == "gut"),
            "ok":  sum(1 for f in gf if f["rating"] == "ok"),
            "leer": sum(1 for f in gf if f["rating"] == "leer"),
            "natars": sum(1 for f in gf if f["is_natar"]),
            "res_last": sum(f["res_last"] for f in gf),
            "res_total": sum(f["res_total"] for f in gf),
            "avg_dist": round(sum(f["distance"] for f in non_nat) / len(non_nat), 1) if non_nat else 0,
            "lists": sorted({f["list_name"] for f in gf}),
        })
    stats = {
        "total": len(farms),
        "gut": sum(1 for f in farms if f["rating"] == "gut"),
        "ok":  sum(1 for f in farms if f["rating"] == "ok"),
        "leer": sum(1 for f in farms if f["rating"] == "leer"),
        "avg_res": round(sum(f["res_last"] for f in farms) / len(farms), 1) if farms else 0,
        "avg_dist": round(sum(f["distance"] for f in farms) / len(farms), 1) if farms else 0,
        "total_res_last": sum(f["res_last"] for f in farms),
        "total_res_total": sum(f["res_total"] for f in farms),
    }
    await database.save_farmlist_analysis(
        guild_id, session.get("discord_id", ""),
        session.get("username", ""), stats, group_stats, farms,
    )
    return RedirectResponse(
        f"/guild/{guild_id}/farming?tab=inactive&saved=farmlist_imported&advanced=1",
        status_code=303
    )


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
    tab: str = Form("myfarms"),
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
    return RedirectResponse(f"/guild/{guild_id}/farming?saved=added&tab={tab}", status_code=303)


@app.post("/guild/{guild_id}/farming/farmlist/delete/{entry_id}")
async def farming_farmlist_delete(request: Request, guild_id: str, entry_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_farm_list_entry(guild_id, entry_id)
    return RedirectResponse(f"/guild/{guild_id}/farming?tab=myfarms&saved=deleted", status_code=303)


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
    discord_id = session.get("uid", "")
    march_settings = await database.get_march_settings(guild_id, discord_id)
    return templates.TemplateResponse("einsatz.html", {
        "request": request,
        "guild": guild,
        "plans": plans,
        "session": session,
        "saved": request.query_params.get("saved"),
        "march_settings": march_settings,
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
# Routes — Einsatzplanung (Alliance Operation Planner)
# ---------------------------------------------------------------------------

import json as _op_json

async def _op_api_guard(request: Request, guild_id: str, check_alliance: bool = False):
    """Auth guard for operations JSON API endpoints.
    Returns (session, errorJSONResponse). error is None if access granted."""
    session, err = _require_session(request)
    if err:
        return None, JSONResponse({"error": "not_logged_in"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err:
        return None, JSONResponse({"error": "no_access"}, status_code=403)
    if check_alliance:
        guild = await database.get_guild(guild_id)
        if guild:
            guild = await _enrich_guild_subscription(guild)
        if not guild or not _has_alliance_pro(guild):
            return None, JSONResponse({"error": "alliance_plan_required"}, status_code=403)
    return session, None

@app.get("/guild/{guild_id}/operations", response_class=HTMLResponse)
async def operations_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    err = await _require_alliance(guild, guild_id)
    if err: return err
    uid  = session.get("uid","")
    plans = await database.get_op_plans(guild_id)
    favorites = await database.get_op_favorites(guild_id, uid)
    members   = await database.get_member_troops(guild_id)
    troop_roles = await database.get_troop_roles(guild_id)
    return templates.TemplateResponse("operations.html", {
        "request":   request,
        "guild":     guild,
        "session":   session,
        "plans":     plans,
        "favorites": favorites,
        "members":   members,
        "troops_def": database.TRAVIAN_TROOPS,
        "troop_roles": troop_roles,
        "default_ts": guild.get("default_tournament_square") or 0,
    })


# ── Plan CRUD (JSON API) ──────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/plans")
async def op_create_plan(
    request: Request, guild_id: str,
    name: str = Form("Neuer Einsatz"),
    landing_time: str = Form(""),
    server_speed: float = Form(1.0),
    target_ally: str = Form(""),
    notes: str = Form(""),
):
    session, err = await _op_api_guard(request, guild_id, check_alliance=True)
    if err: return err
    plan_id = await database.create_op_plan(
        guild_id, name.strip() or "Neuer Einsatz",
        landing_time, max(0.5, min(server_speed, 10.0)),
        target_ally.strip(), notes.strip(),
        session.get("uid",""),
    )
    return JSONResponse({"ok": True, "id": plan_id})


@app.get("/guild/{guild_id}/operations/api/plan-list")
async def op_plan_list(request: Request, guild_id: str):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    plans = await database.get_op_plans(guild_id)
    return JSONResponse(plans)


@app.get("/guild/{guild_id}/operations/api/plans/{plan_id}")
async def op_get_plan(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    plan = await database.get_op_plan_full(plan_id, guild_id)
    if not plan:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(plan)


@app.post("/guild/{guild_id}/operations/api/plans/{plan_id}/update")
async def op_update_plan(
    request: Request, guild_id: str, plan_id: int,
    name: str = Form(None), landing_time: str = Form(None),
    server_speed: float = Form(None), target_ally: str = Form(None),
    notes: str = Form(None), status: str = Form(None),
):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    kwargs = {}
    if name         is not None: kwargs["name"]          = name.strip()[:120]
    if landing_time is not None: kwargs["landing_time"]  = landing_time
    if server_speed is not None: kwargs["server_speed"]  = max(0.5, min(float(server_speed), 10.0))
    if target_ally  is not None: kwargs["target_ally"]   = target_ally.strip()[:100]
    if notes        is not None: kwargs["notes"]         = notes.strip()[:500]
    if status       is not None and status in ("draft","active","completed","cancelled"):
        kwargs["status"] = status
    # Detect status transitions that require Discord notifications
    new_status = kwargs.get("status")
    old_plan = await database.get_op_plan(plan_id, guild_id) if new_status in ("active", "cancelled") else None
    await database.update_op_plan(plan_id, guild_id, **kwargs)
    if old_plan and new_status:
        old_status = old_plan.get("status")
        if new_status == "active" and old_status != "active":
            # Plan activated (fresh or re-activated) → send full announcement
            await _announce_plan_via_bot(guild_id, plan_id)
        elif new_status == "cancelled" and old_status == "active":
            # Plan cancelled → notify members
            await _announce_plan_cancelled_via_bot(guild_id, plan_id)
    return JSONResponse({"ok": True})


async def _announce_plan_via_bot(guild_id: str, plan_id: int):
    """Send Discord DMs + internal notifications when a plan goes active."""
    dm_results: list = []
    member_ids: list = []
    try:
        plan = await database.get_op_plan(plan_id, guild_id)
        if not plan:
            print(f"[announce-ep] plan not found: guild={guild_id} plan={plan_id}")
            await database.save_op_notify_log(guild_id, plan_id, "", "auto",
                [{"discord_id": "", "name": "System", "status": "error", "error": "Plan not found"}])
            return
        landing = (plan.get("landing_time") or "").replace("T", " ")[:16]
        plan_name = plan.get("name", "Einsatzplan")
        server_host = os.environ.get("SERVER_HOST", "https://travops.online")
        plan_url = f"{server_host}/guild/{guild_id}/operations"

        # Only notify members who have waves assigned in this plan
        waves = await database.get_all_op_waves(plan_id)
        # Build travian_name → discord_id lookup for fallback
        name_to_discord: dict[str, str] = {}
        async with __import__('aiosqlite').connect(database.DB_PATH) as _db:
            async with _db.execute(
                "SELECT travian_name, discord_id FROM member_troops WHERE guild_id=? AND travian_name != ''",
                (guild_id,)
            ) as _cur:
                for _row in await _cur.fetchall():
                    if _row[0] and _row[1]:
                        name_to_discord[_row[0]] = _row[1]
        member_wave_times: dict[str, str] = {}
        for w in (waves or []):
            disc_id = str(w.get("attacker_discord_id") or "").strip()
            # Fallback: resolve by travian name if discord_id missing
            if not disc_id:
                aname = str(w.get("attacker_name") or "").strip()
                disc_id = name_to_discord.get(aname, "")
            if not disc_id:
                continue
            st = str(w.get("send_time") or "").strip()
            if disc_id not in member_wave_times:
                member_wave_times[disc_id] = st
            elif st and (not member_wave_times[disc_id] or st < member_wave_times[disc_id]):
                member_wave_times[disc_id] = st
        member_ids = list(member_wave_times.keys())

        ally_group = await database.get_ally_group_for_guild(guild_id)

        # 1. Discord DMs via bot (no channel needed)
        payload = {
            "guild_id": guild_id,
            "plan_name": plan_name,
            "landing_time": landing,
            "plan_url": plan_url,
            "poll_channel_id": "",
            "member_discord_ids": member_ids,
            "member_wave_times": member_wave_times,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post("http://bot:7777/api/announce-ep", json=payload)
            bot_data = resp.json() if resp.status_code == 200 else {}
            dms = bot_data.get("dms", 0)
            dm_results = bot_data.get("results", [])
            print(f"[announce-ep] DMs sent: {dms}/{len(member_ids)} for plan {plan_id}")
        await database.save_op_notify_log(guild_id, plan_id, "", "auto", dm_results)

        # 2. Internal TravOps notifications for all approved members
        await database.create_notifications(
            guild_id=guild_id,
            ally_group_id=ally_group["id"] if ally_group else None,
            recipient_ids=member_ids,
            notif_type="ep_active",
            title=f"⚔️ Neuer Einsatzplan: {plan_name}",
            message=f"Ein Einsatz wurde aktiviert. Einschlag: {landing}. Bitte prüfe deine Wellen unter »Mein Account«.",
            plan_id=plan_id,
        )
        print(f"[announce-ep] internal notifications created for {len(member_ids)} members")
    except Exception as e:
        print(f"[announce-ep] error: {e}")
        dm_results = [{"discord_id": "", "name": "Bot", "status": "error", "error": str(e)[:120]}]
    finally:
        await database.save_op_notify_log(guild_id, plan_id, "", "auto", dm_results)


async def _announce_plan_cancelled_via_bot(guild_id: str, plan_id: int):
    """Notify Discord members when an active EP is cancelled."""
    try:
        plan = await database.get_op_plan(plan_id, guild_id)
        if not plan:
            return
        plan_name = plan.get("name", "Einsatzplan")
        server_host = os.environ.get("SERVER_HOST", "https://travops.online")
        plan_url = f"{server_host}/guild/{guild_id}/operations"

        ally_group = await database.get_ally_group_for_guild(guild_id)
        member_ids = []
        if ally_group:
            members = await database.get_ally_members(ally_group["id"])
            member_ids = [str(m["discord_id"]) for m in members
                          if m.get("discord_id") and m.get("status", "approved") == "approved"]

        payload = {
            "guild_id": guild_id,
            "plan_name": plan_name,
            "plan_url": plan_url,
            "member_discord_ids": member_ids,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post("http://bot:7777/api/announce-ep-cancelled", json=payload)
            dms = resp.json().get("dms", 0) if resp.status_code == 200 else 0
            print(f"[announce-ep-cancelled] DMs sent: {dms}/{len(member_ids)} for plan {plan_id}")

        await database.create_notifications(
            guild_id=guild_id,
            ally_group_id=ally_group["id"] if ally_group else None,
            recipient_ids=member_ids,
            notif_type="ep_cancelled",
            title=f"❌ Einsatz abgebrochen: {plan_name}",
            message=f'Der Einsatz "{plan_name}" wurde abgebrochen.',
            plan_id=plan_id,
        )
    except Exception as e:
        print(f"[announce-ep-cancelled] error: {e}")


@app.post("/guild/{guild_id}/operations/api/plans/{plan_id}/delete")
async def op_delete_plan(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    await database.delete_op_plan(plan_id, guild_id)
    return JSONResponse({"ok": True})


# ── Targets ───────────────────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/plans/{plan_id}/targets")
async def op_add_target(
    request: Request, guild_id: str, plan_id: int,
    player_name: str = Form(""), village_name: str = Form(""),
    x: int = Form(...), y: int = Form(...),
    population: int = Form(0), notes: str = Form(""),
):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    plan = await database.get_op_plan(plan_id, guild_id)
    if not plan:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    tid = await database.add_op_target(
        plan_id, guild_id,
        player_name.strip(), village_name.strip(),
        x, y, population, notes.strip()
    )
    return JSONResponse({"ok": True, "id": tid})


@app.post("/guild/{guild_id}/operations/api/targets/{target_id}/delete")
async def op_delete_target(request: Request, guild_id: str, target_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    await database.delete_op_target(target_id, guild_id)
    return JSONResponse({"ok": True})


# ── Waves ─────────────────────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/targets/{target_id}/waves")
async def op_add_wave(
    request: Request, guild_id: str, target_id: int,
    plan_id: int = Form(...),
    attacker_discord_id: str = Form(""),
    attacker_name: str = Form(""),
    origin_village: str = Form(""),
    origin_x: int = Form(None),
    origin_y: int = Form(None),
    wave_type: str = Form("real"),
    tribe: str = Form("romans"),
    troop_json: str = Form("{}"),
    landing_time: str = Form(""),
    server_speed: float = Form(1.0),
    notes: str = Form(""),
    tournament_square: int = Form(0),
):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    if wave_type not in ("real","fake","def","scout","cleaner1","cleaner2"):
        wave_type = "real"
    if tribe not in database.TRAVIAN_TROOPS:
        tribe = "romans"
    try:
        troops = _op_json.loads(troop_json)
        if not isinstance(troops, dict): troops = {}
    except Exception:
        troops = {}
    result = await database.add_op_wave(
        target_id, plan_id, guild_id,
        attacker_discord_id, attacker_name.strip(),
        origin_village.strip(), origin_x, origin_y,
        wave_type, tribe, troops, landing_time, server_speed, notes.strip(),
        tournament_square=max(0, min(tournament_square, 20))
    )
    # Auto-notify the assigned player via Discord DM
    if attacker_discord_id:
        async def _auto_notify_wave():
            try:
                plan = await database.get_op_plan_full(plan_id, guild_id)
                if plan:
                    async with httpx.AsyncClient(timeout=8) as _hc:
                        await _hc.post("http://bot:7777/api/op-wave-assigned", json={
                            "guild_id": guild_id, "plan": plan,
                            "discord_id": attacker_discord_id,
                            "attacker_name": attacker_name.strip(),
                            "wave_type": wave_type,
                            "target_x": result.get("target_x"),
                            "target_y": result.get("target_y"),
                            "send_time": result.get("send_time"),
                        })
            except Exception:
                pass
        asyncio.create_task(_auto_notify_wave())
    return JSONResponse({"ok": True, **result})


@app.post("/guild/{guild_id}/operations/api/waves/{wave_id}/update")
async def op_update_wave(request: Request, guild_id: str, wave_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    # Recompute times if origin/target changed
    plan = None
    if "origin_x" in data or "tribe" in data or "troop_json" in data:
        import aiosqlite as _aiosqlite_op
        import datetime as _dt_op
        row = None
        async with _aiosqlite_op.connect(database.DB_PATH) as _db2:
            _db2.row_factory = _aiosqlite_op.Row
            async with _db2.execute(
                "SELECT w.*, t.x as tx, t.y as ty, p.landing_time, p.server_speed "
                "FROM op_waves w JOIN op_targets t ON t.id=w.target_id "
                "JOIN op_plans p ON p.id=w.plan_id WHERE w.id=? AND w.guild_id=?",
                (wave_id, guild_id)
            ) as _cur2:
                row = await _cur2.fetchone()
        if row:
            row = dict(row)
            troops = data.get("troop_json", _op_json.loads(row["troop_json"] or "{}"))
            if isinstance(troops, str):
                troops = _op_json.loads(troops)
            tribe   = data.get("tribe", row["tribe"])
            ox      = data.get("origin_x", row["origin_x"])
            oy      = data.get("origin_y", row["origin_y"])
            lt      = data.get("landing_time", row["landing_time"])
            spd     = float(data.get("server_speed", row["server_speed"]))
            slowest = min(troops.keys(), key=lambda t: database._TROOP_SPEED.get(t, 99), default="")
            sl_speed = database._TROOP_SPEED.get(slowest, 6.0)
            travel_sec = 0
            send_t = lt or ""
            if ox is not None and oy is not None and sl_speed > 0:
                travel_sec = database._calc_travel_seconds(
                    int(ox), int(oy), row["tx"], row["ty"], sl_speed, spd)
                if lt:
                    try:
                        ltd = _dt_op.datetime.fromisoformat(lt.replace("Z", ""))
                        send_t = (ltd - _dt_op.timedelta(seconds=travel_sec)).strftime("%Y-%m-%dT%H:%M:%S")
                    except Exception:
                        pass
            data["travel_seconds"] = travel_sec
            data["send_time"]      = send_t
            data["slowest_unit"]   = slowest
            data["slowest_speed"]  = sl_speed
            if isinstance(data.get("troop_json"), dict):
                data["troop_json"] = _op_json.dumps(data["troop_json"])
    await database.update_op_wave(wave_id, guild_id, **data)
    return JSONResponse({"ok": True, "send_time": data.get("send_time",""), "travel_seconds": data.get("travel_seconds",0)})


@app.post("/guild/{guild_id}/operations/api/waves/{wave_id}/delete")
async def op_delete_wave(request: Request, guild_id: str, wave_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    await database.delete_op_wave(wave_id, guild_id)
    return JSONResponse({"ok": True})


# ── Wave confirmation ─────────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/waves/{wave_id}/confirm")
async def op_confirm_wave(
    request: Request, guild_id: str, wave_id: int,
    confirm_status: str = Form(""),
    confirm_delta_seconds: int = Form(0),
):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    uid = session.get("uid","")
    valid = {"on_time","late","not_sent","cant_send",""}
    if confirm_status not in valid:
        return JSONResponse({"error": "invalid status"}, status_code=400)
    # Only the assigned attacker may confirm (by discord_id)
    import aiosqlite as _aiosqlite_c
    async with _aiosqlite_c.connect(database.DB_PATH) as _db_c:
        _db_c.row_factory = _aiosqlite_c.Row
        async with _db_c.execute(
            "SELECT attacker_discord_id FROM op_waves WHERE id=? AND guild_id=?",
            (wave_id, guild_id)
        ) as _cur_c:
            wrow = await _cur_c.fetchone()
    if not wrow:
        return JSONResponse({"error": "not found"}, status_code=404)
    if wrow["attacker_discord_id"] and wrow["attacker_discord_id"] != uid:
        return JSONResponse({"error": "not your wave"}, status_code=403)
    await database.update_op_wave(wave_id, guild_id,
        confirm_status=confirm_status,
        confirm_delta_seconds=confirm_delta_seconds)
    return JSONResponse({"ok": True})


@app.get("/guild/{guild_id}/operations/api/plans/{plan_id}/live")
async def op_live_status(request: Request, guild_id: str, plan_id: int):
    """Lightweight live-status endpoint — returns wave statuses + send times only."""
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    plan = await database.get_op_plan(plan_id, guild_id)
    if not plan:
        return JSONResponse({"error": "not found"}, status_code=404)
    now_utc = __import__('datetime').datetime.utcnow().isoformat()
    result = []
    for t in (plan.get("targets") or []):
        result.append({
            "id":   t.get("id"),
            "name": t.get("player_name") or t.get("village_name") or t.get("target_name") or "",
            "x":    t.get("x"),
            "y":    t.get("y"),
            "waves": [{
                "id":             w.get("id"),
                "attacker_name":  w.get("attacker_name",""),
                "send_time":      w.get("send_time",""),
                "arrival_time":   w.get("arrival_time",""),
                "wave_type":      w.get("wave_type","real"),
                "confirm_status": w.get("confirm_status",""),
            } for w in (t.get("waves") or [])]
        })
    return JSONResponse({
        "plan_id":    plan_id,
        "plan_name":  plan.get("name",""),
        "status":     plan.get("status",""),
        "server_now": now_utc,
        "targets":    result,
    })


@app.get("/guild/{guild_id}/operations/api/my-waves")
async def op_my_waves(request: Request, guild_id: str):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    uid = session.get("uid","")
    waves = await database.get_my_op_waves(guild_id, uid)
    hero_actions = await database.get_hero_actions_for_player(guild_id, uid)
    return JSONResponse({"waves": waves, "hero_actions": hero_actions})


@app.get("/guild/{guild_id}/my-operations", response_class=HTMLResponse)
async def my_operations_page(request: Request, guild_id: str):
    """Player-facing op plan view — only shows waves assigned to the logged-in user."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    uid = session.get("uid", "")
    waves = await database.get_my_op_waves(guild_id, uid)
    tw_world = (guild or {}).get("tw_world", "")
    return templates.TemplateResponse("my_operations.html", {
        "request": request, "guild": guild, "guild_id": guild_id,
        "waves": waves, "tw_world": tw_world,
    })


# ── Recalculate wave times ────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/save-default-ts")
async def op_save_default_ts(request: Request, guild_id: str):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    data = await request.json()
    ts = max(0, min(20, int(data.get("ts", 0))))
    async with __import__('aiosqlite').connect(database.DB_PATH) as db:
        await db.execute(
            "UPDATE guild_configs SET default_tournament_square=? WHERE guild_id=?", (ts, guild_id)
        )
        await db.commit()
    return JSONResponse({"ok": True, "ts": ts})


@app.get("/guild/{guild_id}/operations/{plan_id}/live", response_class=HTMLResponse)
async def op_live_popup(request: Request, guild_id: str, plan_id: int):
    """Standalone live board popup — detachable window, auto-refreshes every 5s."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    plan = await database.get_op_plan(plan_id, guild_id)
    if not plan:
        return HTMLResponse("<h2>Plan not found</h2>", status_code=404)
    return templates.TemplateResponse("op_live_popup.html", {
        "request":  request,
        "guild":    await database.get_guild(guild_id),
        "plan":     plan,
        "plan_id":  plan_id,
        "guild_id": guild_id,
    })


@app.get("/guild/{guild_id}/operations/{plan_id}/map-popup", response_class=HTMLResponse)
async def op_map_popup(request: Request, guild_id: str, plan_id: int):
    """Standalone map window — no nav, just the canvas + auto-refresh."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    plan = await database.get_op_plan_full(plan_id, guild_id)
    if not plan:
        return HTMLResponse("Plan not found", status_code=404)
    return templates.TemplateResponse("op_map_popup.html", {
        "request": request, "guild": guild,
        "guild_id": guild_id, "plan_id": plan_id, "plan": plan,
    })


@app.post("/guild/{guild_id}/operations/api/plans/{plan_id}/recalc-times")
async def op_recalc_times(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    updated = await database.recalc_op_wave_times(plan_id, guild_id)
    return JSONResponse({"ok": True, "updated": updated})


# ── EP Poll ───────────────────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/plans/{plan_id}/launch-poll")
async def op_launch_poll(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild or not guild.get("poll_channel_id"):
        return JSONResponse({"error": "Kein Poll-Kanal konfiguriert. Bitte zuerst unter Umfragen einrichten."}, status_code=400)
    plan = await database.get_op_plan(plan_id, guild_id)
    if not plan:
        return JSONResponse({"error": "Plan nicht gefunden."}, status_code=404)
    title = f"⚔️ EP: {plan['name']}"
    landing = (plan.get("landing_time") or "").replace("T", " ")[:16]
    description = f"Bist du für diesen Einsatz verfügbar?\n🕐 Einschlag: **{landing}**" if landing else "Bist du für diesen Einsatz verfügbar?"
    poll_id = await database.create_ep_poll(guild_id, plan_id, title, description, plan.get("landing_time") or "")
    # Post to Discord
    token = os.environ.get("DISCORD_TOKEN", "")
    channel_id = guild["poll_channel_id"]
    embed = {
        "title": title,
        "description": description,
        "color": 15548997,
        "fields": [{"name": "🕐 Einschlagszeit", "value": landing or "—", "inline": True},
                   {"name": "📋 Plan", "value": plan["name"], "inline": True}],
        "footer": {"text": f"EP-Umfrage #{poll_id} · Antwort ist anonym"},
    }
    components = [{"type": 1, "components": [
        {"type": 2, "style": 3, "label": "Going",       "emoji": {"name": "✅"}, "custom_id": f"poll_available_{poll_id}"},
        {"type": 2, "style": 1, "label": "Maybe",  "emoji": {"name": "⏰"}, "custom_id": f"poll_maybe_{poll_id}"},
        {"type": 2, "style": 4, "label": "Not going", "emoji": {"name": "❌"}, "custom_id": f"poll_unavailable_{poll_id}"},
    ]}]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"embeds": [embed], "components": components},
        )
    if resp.status_code in (200, 201):
        await database.set_poll_message_id(poll_id, resp.json()["id"])
        return JSONResponse({"ok": True, "poll_id": poll_id})
    return JSONResponse({"error": f"Discord-Fehler {resp.status_code}"}, status_code=502)


@app.get("/guild/{guild_id}/operations/api/plans/{plan_id}/poll-availability")
async def op_poll_availability(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    availability = await database.get_ep_poll_availability(guild_id, plan_id)
    poll = await database.get_ep_poll(guild_id, plan_id)
    return JSONResponse({"availability": availability, "poll": poll})


# ── Plausibility ──────────────────────────────────────────────────────────────

@app.get("/guild/{guild_id}/operations/api/plans/{plan_id}/check")
async def op_plausibility(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    result = await database.check_op_plausibility(plan_id, guild_id)
    return JSONResponse(result)


# ── Favourites ────────────────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/favorites")
async def op_add_favorite(
    request: Request, guild_id: str,
    player_name: str = Form(""), village_name: str = Form(""),
    x: int = Form(...), y: int = Form(...), label: str = Form(""),
):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    fid = await database.add_op_favorite(
        guild_id, session.get("uid",""),
        player_name.strip(), village_name.strip(), x, y, label.strip()
    )
    return JSONResponse({"ok": True, "id": fid})


@app.post("/guild/{guild_id}/operations/api/favorites/{fav_id}/delete")
async def op_delete_favorite(request: Request, guild_id: str, fav_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    await database.delete_op_favorite(fav_id, session.get("uid",""), guild_id)
    return JSONResponse({"ok": True})


# ── Village / player search ───────────────────────────────────────────────────

@app.get("/guild/{guild_id}/operations/api/alliances")
async def op_list_alliances(request: Request, guild_id: str):
    """Return alliances sorted by total population (strength), plus meta groups."""
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    import aiosqlite as _aiosqlite_op
    async with _aiosqlite_op.connect(database.DB_PATH) as db:
        db.row_factory = _aiosqlite_op.Row
        # Alliances sorted by total population desc (= world rank proxy)
        async with db.execute("""
            SELECT m.alliance_name,
                   COUNT(DISTINCT m.player_name) AS player_count,
                   SUM(m.population) AS total_pop
            FROM map_snapshots m
            INNER JOIN (
                SELECT guild_id, MAX(fetched_at) as max_ts FROM map_snapshots
                WHERE guild_id=? GROUP BY guild_id
            ) lts ON m.guild_id=lts.guild_id AND m.fetched_at=lts.max_ts
            WHERE m.guild_id=? AND m.alliance_name IS NOT NULL AND m.alliance_name != ''
            GROUP BY m.alliance_name
            ORDER BY total_pop DESC
        """, (guild_id, guild_id)) as cur:
            alliances = [{"name": r["alliance_name"], "players": r["player_count"], "pop": r["total_pop"]} for r in await cur.fetchall()]
    # Meta groups
    meta_groups = await database.get_meta_groups(guild_id)
    return JSONResponse({"alliances": alliances, "meta_groups": meta_groups})


@app.get("/guild/{guild_id}/operations/api/villages")
async def op_search_villages(request: Request, guild_id: str, q: str = "", alliances: str = ""):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    q = q.strip()
    # alliances = comma-separated list of alliance names to filter by (empty = all)
    alliance_filter = [a.strip() for a in alliances.split(",") if a.strip()] if alliances else []
    import aiosqlite as _aiosqlite_op
    async with _aiosqlite_op.connect(database.DB_PATH) as db:
        db.row_factory = _aiosqlite_op.Row
        if q:
            alliance_clause = ""
            params: list = [guild_id, guild_id, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
            if alliance_filter:
                placeholders = ",".join("?" * len(alliance_filter))
                alliance_clause = f" AND m.alliance_name IN ({placeholders})"
                params.extend(alliance_filter)
            async with db.execute(f"""
                SELECT DISTINCT m.village_name, m.x, m.y, m.player_name, m.population, m.alliance_name
                FROM map_snapshots m
                INNER JOIN (
                    SELECT guild_id, MAX(fetched_at) as max_ts FROM map_snapshots
                    WHERE guild_id=? GROUP BY guild_id
                ) lts ON m.guild_id=lts.guild_id AND m.fetched_at=lts.max_ts
                WHERE m.guild_id=?
                  AND (m.player_name LIKE ? OR m.village_name LIKE ?
                       OR (m.x||'|'||m.y) LIKE ? OR (m.x||'/'||m.y) LIKE ?)
                  {alliance_clause}
                ORDER BY m.player_name LIMIT 40
            """, params) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        else:
            rows = []
    return JSONResponse({"results": rows})


def _normalize_tribe(raw) -> str:
    """Convert any tribe representation to the JS TROOPS_DEF key (gauls/romans/teutons/huns/egyptians)."""
    _ID_MAP = {1: "romans", 2: "teutons", 3: "gauls", 5: "egyptians", 6: "huns"}
    _NAME_MAP = {
        "römer": "romans", "romans": "romans", "roman": "romans",
        "teutonen": "teutons", "teutons": "teutons", "teuton": "teutons",
        "gallier": "gauls", "gauls": "gauls", "gaul": "gauls",
        "ägypter": "egyptians", "egyptians": "egyptians", "egyptian": "egyptians",
        "hunnen": "huns", "huns": "huns", "hun": "huns",
    }
    if raw is None: return ""
    try:
        return _ID_MAP.get(int(raw), "")
    except (ValueError, TypeError):
        return _NAME_MAP.get(str(raw).lower().strip(), "")


@app.get("/guild/{guild_id}/operations/api/player-tribe")
async def op_player_tribe(request: Request, guild_id: str, name: str = ""):
    """Quick lookup: return normalized tribe key for a player from all available sources."""
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    if not name:
        return JSONResponse({"tribe": ""})
    import aiosqlite as _aiosqlite_pt
    tribe = ""
    async with _aiosqlite_pt.connect(database.DB_PATH, timeout=15) as db:
        db.row_factory = _aiosqlite_pt.Row
        # 1. member_troops
        async with db.execute(
            "SELECT tribe FROM member_troops WHERE (travian_name=? OR discord_name=?) AND tribe!='' LIMIT 1",
            (name, name)
        ) as cur:
            r = await cur.fetchone()
            if r: tribe = _normalize_tribe(r["tribe"])
        # 2. map_snapshots (numeric tribe from game data)
        if not tribe:
            async with db.execute(
                "SELECT tribe FROM map_snapshots WHERE guild_id=? AND player_name=? AND tribe IS NOT NULL AND tribe!=0 LIMIT 1",
                (guild_id, name)
            ) as cur:
                r = await cur.fetchone()
                if r: tribe = _normalize_tribe(r["tribe"])
        # 3. alliance_members
        if not tribe:
            async with db.execute(
                "SELECT tribe FROM alliance_members WHERE guild_id=? AND player_name=? AND tribe!='' LIMIT 1",
                (guild_id, name)
            ) as cur:
                r = await cur.fetchone()
                if r: tribe = _normalize_tribe(r["tribe"])
    return JSONResponse({"tribe": tribe})


@app.get("/guild/{guild_id}/operations/api/attacker-list")
async def op_attacker_list(request: Request, guild_id: str):
    """All own alliance members merged with their village coords from map_snapshots + troop data."""
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    import aiosqlite as _aiosqlite_op, json as _jop
    async with _aiosqlite_op.connect(database.DB_PATH) as db:
        db.row_factory = _aiosqlite_op.Row
        # Alliance members (Travian names, tribe, rank)
        async with db.execute(
            "SELECT player_name, tribe, rank, population FROM alliance_members WHERE guild_id=? ORDER BY rank",
            (guild_id,)
        ) as cur:
            ally_members = {r["player_name"]: dict(r) for r in await cur.fetchall()}

        # Member troops — we need their travian names to filter the snapshot
        async with db.execute(
            "SELECT discord_id, discord_name, travian_name, tribe, villages_json FROM member_troops WHERE guild_id=?",
            (guild_id,)
        ) as cur:
            troop_rows = {r["travian_name"] or r["discord_name"]: dict(r) for r in await cur.fetchall()}

        # Own villages imported via "Mein Account" — always include regardless of snapshot.
        # Join with member_troops to resolve discord_id → travian_name (guild_own_villages
        # has no travian_name column — it only stores discord_id).
        async with db.execute(
            """SELECT ov.discord_id,
                      COALESCE(mt.travian_name, '') AS travian_name,
                      COALESCE(mt.discord_name, '') AS discord_name,
                      ov.village_name, ov.x, ov.y, ov.population, ov.troops_json
               FROM guild_own_villages ov
               LEFT JOIN member_troops mt
                      ON mt.guild_id = ov.guild_id AND mt.discord_id = ov.discord_id
               WHERE ov.guild_id = ?
               ORDER BY ov.discord_id, ov.population DESC""",
            (guild_id,)
        ) as cur:
            own_rows = await cur.fetchall()
        # Build dict: travian_name (or discord_id) → list of village dicts
        import json as _jov
        own_villages_by_player: dict = {}
        for r in own_rows:
            key = r["travian_name"] or r["discord_id"]
            if key and key not in own_villages_by_player:
                own_villages_by_player[key] = []
            if key:
                try:
                    troops = _jov.loads(r["troops_json"] or "{}")
                except Exception:
                    troops = {}
                own_villages_by_player[key].append({
                    "name": r["village_name"] or "", "x": r["x"], "y": r["y"],
                    "pop": r["population"] or 0, "troops": troops
                })

        # Also include approved ally_members with a travian_name as additional name source
        async with db.execute("""
            SELECT am.discord_id, am.discord_username, am.travian_name
            FROM ally_members am
            JOIN ally_groups ag ON ag.id = am.ally_group_id
            WHERE ag.guild_id=? AND am.status='approved' AND am.travian_name != ''
        """, (guild_id,)) as cur:
            for r in await cur.fetchall():
                tname = r["travian_name"]
                if tname and tname not in troop_rows and tname not in ally_members:
                    # Add as a minimal entry so they appear in the dropdown
                    troop_rows[tname] = {
                        "discord_id": r["discord_id"],
                        "discord_name": r["discord_username"] or "",
                        "travian_name": tname,
                        "tribe": "", "villages_json": None
                    }

        # Their villages from latest map snapshot (own alliance villages)
        tw_name = await database.get_tw_alliance_name(guild_id)
        snap_params = [guild_id, guild_id]
        ally_clause = ""
        known_names = set(ally_members.keys()) | set(troop_rows.keys())
        if tw_name:
            ally_clause = " AND m.alliance_name = ?"
            snap_params.append(tw_name)
        elif ally_members:
            placeholders = ",".join("?" * len(ally_members))
            ally_clause = f" AND m.player_name IN ({placeholders})"
            snap_params.extend(ally_members.keys())
        elif troop_rows:
            # Fallback: filter snapshot by known travian names from troop uploads
            placeholders = ",".join("?" * len(troop_rows))
            ally_clause = f" AND m.player_name IN ({placeholders})"
            snap_params.extend(troop_rows.keys())

        snap_rows = []
        if ally_clause or tw_name:  # only query if we have a filter (avoid full scan)
            async with db.execute(f"""
                SELECT m.player_name, m.village_name, m.x, m.y, m.population, m.tribe
                FROM map_snapshots m
                INNER JOIN (
                    SELECT guild_id, MAX(fetched_at) as max_ts FROM map_snapshots
                    WHERE guild_id=? GROUP BY guild_id
                ) lts ON m.guild_id=lts.guild_id AND m.fetched_at=lts.max_ts
                WHERE m.guild_id=? {ally_clause}
                ORDER BY m.player_name, m.population DESC
            """, snap_params) as cur:
                snap_rows = await cur.fetchall()

    # Group snap villages by player
    from collections import defaultdict
    snap_villages: dict = defaultdict(list)
    snap_tribe: dict = {}
    for r in snap_rows:
        snap_villages[r["player_name"]].append({
            "name": r["village_name"], "x": r["x"], "y": r["y"], "pop": r["population"]
        })
        if not snap_tribe.get(r["player_name"]):
            snap_tribe[r["player_name"]] = r["tribe"]

    # Build merged attacker list — only include players we actually know about
    all_names = (set(ally_members.keys()) | set(snap_villages.keys())
                 | set(troop_rows.keys()) | set(own_villages_by_player.keys()))
    result = []
    for name in all_names:
        am = ally_members.get(name, {})
        tr = troop_rows.get(name, {})
        villages = snap_villages.get(name, [])
        # Fallback 1: villages_json from member_troops upload (include troops)
        # Include villages even without coords (x=None) so player appears in list
        if not villages and tr.get("villages_json"):
            try:
                tv = _jop.loads(tr["villages_json"])
                villages = [{
                    "name": v.get("village_name",""), "x": v.get("x"), "y": v.get("y"),
                    "pop": v.get("population", 0), "troops": v.get("troops", {}),
                } for v in tv]
            except Exception:
                pass
        # Enrich snap villages with troops from member_troops (match by village name)
        elif villages and tr.get("villages_json"):
            try:
                tv = _jop.loads(tr["villages_json"])
                troop_by_name = {v.get("village_name", ""): v.get("troops", {}) for v in tv if v.get("troops")}
                for v in villages:
                    if not v.get("troops"):
                        v["troops"] = troop_by_name.get(v.get("name", ""), {})
            except Exception:
                pass
        # Fallback 2: own villages imported via "Mein Account" (guild_own_villages)
        if not villages and name in own_villages_by_player:
            villages = own_villages_by_player[name]
        result.append({
            "player_name": name,
            "discord_id": tr.get("discord_id", ""),
            "discord_name": tr.get("discord_name", ""),
            "tribe": _normalize_tribe(tr.get("tribe") or snap_tribe.get(name) or am.get("tribe") or ""),
            "rank": am.get("rank", 9999),
            "villages": villages,
        })
    result.sort(key=lambda x: x["rank"])
    return JSONResponse({"attackers": result})


@app.get("/guild/{guild_id}/operations/api/players-by-alliance")
async def op_players_by_alliance(request: Request, guild_id: str, alliances: str = ""):
    """Return all players+villages from latest snapshot, filtered by alliances, grouped by player."""
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    alliance_filter = [a.strip() for a in alliances.split(",") if a.strip()] if alliances else []
    import aiosqlite as _aiosqlite_op
    async with _aiosqlite_op.connect(database.DB_PATH) as db:
        db.row_factory = _aiosqlite_op.Row
        params: list = [guild_id, guild_id]
        alliance_clause = ""
        if alliance_filter:
            placeholders = ",".join("?" * len(alliance_filter))
            alliance_clause = f" AND m.alliance_name IN ({placeholders})"
            params.extend(alliance_filter)
        async with db.execute(f"""
            SELECT m.player_name, m.alliance_name,
                   m.village_name, m.x, m.y, m.population
            FROM map_snapshots m
            INNER JOIN (
                SELECT guild_id, MAX(fetched_at) as max_ts FROM map_snapshots
                WHERE guild_id=? GROUP BY guild_id
            ) lts ON m.guild_id=lts.guild_id AND m.fetched_at=lts.max_ts
            WHERE m.guild_id=? {alliance_clause}
            ORDER BY m.alliance_name, m.player_name, m.population DESC
        """, params) as cur:
            rows = await cur.fetchall()
    # Group by player
    from collections import OrderedDict
    players: dict = OrderedDict()
    for r in rows:
        key = (r["alliance_name"] or "", r["player_name"] or "")
        if key not in players:
            players[key] = {"player_name": r["player_name"], "alliance_name": r["alliance_name"],
                            "total_pop": 0, "villages": []}
        players[key]["villages"].append({"name": r["village_name"], "x": r["x"], "y": r["y"], "pop": r["population"]})
        players[key]["total_pop"] += (r["population"] or 0)
    result = sorted(players.values(), key=lambda p: -p["total_pop"])
    return JSONResponse({"players": result})


# ── Member troops ─────────────────────────────────────────────────────────────

@app.get("/guild/{guild_id}/operations/api/members")
async def op_get_members(request: Request, guild_id: str):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    members = await database.get_member_troops(guild_id)
    return JSONResponse({"members": members})


# ── Discord notification ───────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/plans/{plan_id}/notify")
async def op_send_notification(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    plan = await database.get_op_plan_full(plan_id, guild_id)
    if not plan:
        return JSONResponse({"error": "not found"}, status_code=404)
    results = []
    ok = False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "http://bot:7777/api/op-notify",
                json={"guild_id": guild_id, "plan": plan}
            )
            if resp.status_code == 200:
                ok = True
                data = resp.json()
                results = data.get("results", [])
    except Exception as e:
        results = [{"discord_id": "", "name": "Bot", "status": "error", "error": str(e)[:120]}]
    triggered_by = session.get("uid", "") if session else ""
    await database.save_op_notify_log(guild_id, plan_id, triggered_by, "manual", results)
    return JSONResponse({"ok": ok, "results": results})


@app.get("/guild/{guild_id}/operations/api/map-villages")
async def op_map_villages(
    request: Request, guild_id: str,
    x1: int = 0, y1: int = 0, x2: int = 0, y2: int = 0,
):
    """Return map_snapshots villages in bounding box for op map background."""
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    import aiosqlite as _aio
    rows = []
    async with _aio.connect(database.DB_PATH) as db:
        db.row_factory = _aio.Row
        # Use the single latest import batch (max fetched_at for this guild)
        async with db.execute(
            "SELECT MAX(fetched_at) FROM map_snapshots WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            latest = row[0] if row else None
        if not latest:
            return JSONResponse({"villages": []})
        async with db.execute("""
            SELECT x, y, village_name, player_name, alliance_name, tribe,
                   population, is_capital
            FROM map_snapshots
            WHERE guild_id=? AND fetched_at=? AND x>=? AND x<=? AND y>=? AND y<=?
            LIMIT 4000
        """, (guild_id, latest, x1, x2, y1, y2)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return JSONResponse({"villages": rows})


@app.get("/guild/{guild_id}/operations/api/plans/{plan_id}/notify-log")
async def op_get_notify_log(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    logs = await database.get_op_notify_logs(guild_id, plan_id)
    return JSONResponse({"logs": logs})


# ── OP Evaluation ────────────────────────────────────────────────────────────

@app.get("/guild/{guild_id}/operations/api/plans/{plan_id}/evaluation")
async def op_evaluation(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    data = await database.get_op_evaluation(plan_id, guild_id)
    return JSONResponse(data)


# ── Hero Actions ──────────────────────────────────────────────────────────────

@app.post("/guild/{guild_id}/operations/api/plans/{plan_id}/hero-actions")
async def op_add_hero_action(request: Request, guild_id: str, plan_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    action_id = await database.add_op_hero_action(
        plan_id, guild_id,
        action_type=data.get("action_type", "gear_switch"),
        player_discord_id=data.get("player_discord_id", ""),
        player_name=data.get("player_name", ""),
        item_slot=data.get("item_slot", ""),
        item_name=data.get("item_name", ""),
        notes=data.get("notes", ""),
        send_time=data.get("send_time", ""),
        target_id=data.get("target_id"),
    )
    # Notify the player via Discord DM
    discord_id = data.get("player_discord_id", "")
    if discord_id:
        async def _notify_hero():
            try:
                plan = await database.get_op_plan(plan_id, guild_id)
                async with httpx.AsyncClient(timeout=8) as _hc:
                    await _hc.post("http://bot:7777/api/op-hero-action", json={
                        "guild_id": guild_id,
                        "discord_id": discord_id,
                        "player_name": data.get("player_name", ""),
                        "action_type": data.get("action_type", "gear_switch"),
                        "item_slot": data.get("item_slot", ""),
                        "item_name": data.get("item_name", ""),
                        "notes": data.get("notes", ""),
                        "plan_name": (plan or {}).get("name", "Operation"),
                        "landing_time": (plan or {}).get("landing_time", ""),
                    })
            except Exception:
                pass
        asyncio.create_task(_notify_hero())
    return JSONResponse({"ok": True, "id": action_id})


@app.post("/guild/{guild_id}/operations/api/hero-actions/{action_id}/delete")
async def op_delete_hero_action(request: Request, guild_id: str, action_id: int):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    await database.delete_op_hero_action(action_id, guild_id)
    return JSONResponse({"ok": True})


# ── Personal missions (used by my-account tab) ──────────────────────────────

@app.get("/guild/{guild_id}/operations/api/my-missions")
async def op_my_missions(request: Request, guild_id: str):
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    missions = await database.get_personal_missions(guild_id, session.get("uid",""))
    return JSONResponse({"missions": missions})


# ── Notifications ─────────────────────────────────────────────────────────────

@app.get("/guild/{guild_id}/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    notifs = await database.get_notifications(guild_id, uid, limit=80)
    unread = sum(1 for n in notifs if not n["read"])
    # Mark as read only when user explicitly clears — not on page load
    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "guild": guild,
        "notifications": notifs,
        "unread_count": unread,
    })


@app.post("/guild/{guild_id}/notifications/clear")
async def notifications_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    await database.mark_notifications_read(guild_id, uid)
    return RedirectResponse(f"/guild/{guild_id}/notifications", status_code=303)


@app.get("/guild/{guild_id}/notifications/api/unread-count")
async def notifications_unread_count(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"count": 0})
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"count": 0})
    count = await database.count_unread_notifications(guild_id, session.get("uid", ""))
    return JSONResponse({"count": count})


# Wave confirm — trigger notifications for ep_notify roles
@app.post("/guild/{guild_id}/operations/api/waves/{wave_id}/notify-leads")
async def op_wave_notify_leads(request: Request, guild_id: str, wave_id: int):
    """Called after confirm to notify leads if attacker can't send."""
    session, err = await _op_api_guard(request, guild_id)
    if err: return err
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False})
    status = data.get("confirm_status", "")
    attacker = data.get("attacker_name", "")
    plan_name = data.get("plan_name", "")
    plan_id = data.get("plan_id")
    target = data.get("target", "")
    if status not in ("cant_send", "not_sent"):
        return JSONResponse({"ok": True})
    lead_ids = await database.get_ep_notify_members(guild_id)
    sender_id = session.get("uid", "")
    recipients = [lid for lid in lead_ids if lid != sender_id]
    if not recipients:
        return JSONResponse({"ok": True})
    title_map = {"cant_send": "⚠️ Angreifer kann nicht abschicken", "not_sent": "❌ Welle nicht abgeschickt"}
    msg_map = {
        "cant_send": f"{attacker} hat gemeldet, dass er die Welle zu '{target}' nicht abschicken kann.\nPlan: {plan_name}",
        "not_sent": f"{attacker} hat bestätigt, dass die Welle zu '{target}' nicht abgeschickt wurde.\nPlan: {plan_name}",
    }
    import datetime as _dt_n
    ally_group = await database.get_ally_group_for_guild(guild_id)
    await database.create_notifications(
        guild_id, ally_group["id"] if ally_group else None,
        recipients, status,
        title_map[status], msg_map[status], plan_id=plan_id
    )
    return JSONResponse({"ok": True, "notified": len(recipients)})


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


@app.get("/admin/server", response_class=HTMLResponse)
async def admin_server(request: Request):
    session, err = _require_admin(request)
    if err: return err
    return templates.TemplateResponse("admin_server.html", {"request": request, "session": session})


@app.get("/admin/worlds", response_class=HTMLResponse)
async def admin_worlds(request: Request):
    session, err = _require_admin(request)
    if err: return err
    guilds = await database.get_all_guilds()
    from datetime import datetime, timezone

    world_map: dict[str, dict] = {}
    for g in guilds:
        world = (g.get("tw_world") or "").strip().rstrip("/")
        if not world:
            continue
        if world not in world_map:
            world_map[world] = {"guilds": [], "latest": None, "age_min": None}
        world_map[world]["guilds"].append(g)
        ts = await database.get_latest_snapshot_time(g["guild_id"])
        if ts:
            if world_map[world]["latest"] is None or ts > world_map[world]["latest"]:
                world_map[world]["latest"] = ts

    now = datetime.now(timezone.utc)
    for w, info in world_map.items():
        if info["latest"]:
            try:
                dt = datetime.fromisoformat(info["latest"].replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                info["age_min"] = round((now - dt).total_seconds() / 60)
            except Exception:
                pass

    return templates.TemplateResponse("admin_worlds.html", {
        "request": request,
        "session": session,
        "world_map": world_map,
        "no_world": [g for g in guilds if not (g.get("tw_world") or "").strip()],
    })


@app.get("/api/admin/server-stats")
async def api_server_stats(request: Request):
    session, err = _require_admin(request)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    import os, time
    stats: dict = {}
    # ── CPU ──────────────────────────────────────────────────────────────────
    try:
        with open("/proc/stat") as f:
            cpu_line = f.readline()
        vals = list(map(int, cpu_line.split()[1:]))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        stats["cpu_idle"] = idle
        stats["cpu_total"] = total
    except Exception:
        stats["cpu_idle"] = stats["cpu_total"] = 0
    # ── Memory ───────────────────────────────────────────────────────────────
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k.strip()] = int(v.strip().split()[0])
        stats["mem_total_kb"] = mem.get("MemTotal", 0)
        stats["mem_free_kb"]  = mem.get("MemAvailable", mem.get("MemFree", 0))
        stats["mem_used_kb"]  = stats["mem_total_kb"] - stats["mem_free_kb"]
    except Exception:
        stats["mem_total_kb"] = stats["mem_free_kb"] = stats["mem_used_kb"] = 0
    # ── Load average ─────────────────────────────────────────────────────────
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        stats["load_1"]  = float(parts[0])
        stats["load_5"]  = float(parts[1])
        stats["load_15"] = float(parts[2])
        procs = parts[3].split("/")
        stats["procs_running"] = int(procs[0])
        stats["procs_total"]   = int(procs[1]) if len(procs) > 1 else 0
    except Exception:
        stats["load_1"] = stats["load_5"] = stats["load_15"] = 0.0
        stats["procs_running"] = stats["procs_total"] = 0
    # ── Uptime ───────────────────────────────────────────────────────────────
    try:
        with open("/proc/uptime") as f:
            uptime_sec = float(f.read().split()[0])
        stats["uptime_sec"] = int(uptime_sec)
    except Exception:
        stats["uptime_sec"] = 0
    # ── Disk ─────────────────────────────────────────────────────────────────
    try:
        st = os.statvfs("/")
        stats["disk_total_kb"] = st.f_frsize * st.f_blocks // 1024
        stats["disk_free_kb"]  = st.f_frsize * st.f_bavail // 1024
        stats["disk_used_kb"]  = stats["disk_total_kb"] - stats["disk_free_kb"]
    except Exception:
        stats["disk_total_kb"] = stats["disk_free_kb"] = stats["disk_used_kb"] = 0
    # ── DB size ──────────────────────────────────────────────────────────────
    try:
        db_path = database.DB_PATH
        stats["db_size_kb"] = os.path.getsize(db_path) // 1024
    except Exception:
        stats["db_size_kb"] = 0
    stats["ts"] = int(time.time())
    return JSONResponse(stats)


@app.get("/admin/api/guild-search")
async def admin_guild_search(request: Request, q: str = ""):
    session, err = _require_admin(request)
    if err: return JSONResponse({"guilds": []}, status_code=403)
    all_guilds = await database.get_all_guilds()
    q_lower = q.strip().lower()
    results = []
    for g in all_guilds:
        name = (g.get("guild_name") or "").lower()
        gid = (g.get("guild_id") or "")
        if q_lower in name or q_lower in gid:
            results.append({
                "guild_id": gid,
                "guild_name": g.get("guild_name") or gid,
                "subscription_status": g.get("subscription_status") or "free",
                "subscription_plan": g.get("subscription_plan") or "",
            })
        if len(results) >= 20:
            break
    return JSONResponse({"guilds": results})


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


@app.get("/admin/trial-links", response_class=HTMLResponse)
async def admin_trial_links(request: Request):
    session, err = _require_admin(request)
    if err: return err
    import secrets as _sec
    links = await database.get_all_trial_links()
    created = request.query_params.get("created")
    return templates.TemplateResponse("admin_trial_links.html", {
        "request": request, "links": links, "created": created,
    })


@app.post("/admin/trial-links/create")
async def admin_trial_links_create(request: Request):
    session, err = _require_admin(request)
    if err: return err
    import secrets as _sec
    code = _sec.token_urlsafe(10)
    admin_name = session.get("username", "admin")
    await database.create_trial_link(code=code, created_by=admin_name)
    return RedirectResponse(f"/admin/trial-links?created={code}", status_code=303)


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

@app.get("/admin/ideas", response_class=HTMLResponse)
async def admin_ideas_page(request: Request, saved: str = "", deleted: str = ""):
    session, err = _require_admin(request)
    if err: return err
    ideas = await database.get_ideas()
    return templates.TemplateResponse("admin_ideas.html", {
        "request": request, "ideas": ideas, "saved": saved, "deleted": deleted
    })

@app.post("/admin/ideas")
async def admin_ideas_create(request: Request,
                              title: str = Form(...), description: str = Form(""),
                              category: str = Form("general")):
    session, err = _require_admin(request)
    if err: return err
    await database.create_idea(title.strip(), description.strip(), category)
    # Pre-populate default ideas on first save if empty
    return RedirectResponse("/admin/ideas?saved=1", status_code=303)

@app.post("/admin/ideas/{idea_id}/delete")
async def admin_ideas_delete(request: Request, idea_id: int):
    session, err = _require_admin(request)
    if err: return err
    await database.delete_idea(idea_id)
    return RedirectResponse("/admin/ideas?deleted=1", status_code=303)


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


async def _discover_travian_servers() -> list[dict]:
    """Probe known Travian URL patterns and return active servers."""
    import asyncio as _aio
    regions = ["europe", "international", "america", "asia", "arabia",
               "de", "fr", "pl", "ru", "nl", "cz", "hu", "ro", "tr", "it", "es", "pt"]
    speeds  = ["x1", "x2", "x3", "x5", "x10"]
    nums    = range(1, 16)

    candidates = [
        f"https://ts{n}.{sp}.{r}.travian.com"
        for n in nums for sp in speeds for r in regions
    ]

    found = []
    async def probe(url: str):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(url + "/api/v1/info",
                                headers={"User-Agent": "TravOps/1.0"})
                if r.status_code == 200:
                    data = r.json()
                    players = data.get("playersCount", 0)
                    speed   = data.get("serverConfiguration", {}).get("speed", 1)
                    region  = url.split(".")[2] if url.count(".") >= 3 else ""
                    found.append({"url": url, "players": players, "speed": speed, "region": region})
                    await database.upsert_travian_server(url, players, speed, region)
        except Exception:
            pass

    await _aio.gather(*[probe(u) for u in candidates])
    return sorted(found, key=lambda x: -x["players"])


@app.get("/admin/servers", response_class=HTMLResponse)
async def admin_servers(request: Request, discovered: str = ""):
    session, err = _require_admin(request)
    if err: return err
    guild_overview = await database.get_servers_overview()
    travian_servers = await database.get_travian_servers()
    return templates.TemplateResponse("admin_servers.html", {
        "request": request,
        "servers": guild_overview,
        "travian_servers": travian_servers,
        "discovered": discovered,
        "session": session,
    })


@app.post("/admin/servers/discover")
async def admin_servers_discover(request: Request):
    session, err = _require_admin(request)
    if err: return err
    found = await _discover_travian_servers()
    return RedirectResponse(f"/admin/servers?discovered={len(found)}", status_code=303)


@app.get("/api/travian-servers")
async def api_travian_servers():
    servers = await database.get_travian_servers()
    # Also include worlds currently in use by any guild
    guilds = await database.get_all_guilds()
    known_urls = {s["url"] for s in servers}
    for g in guilds:
        w = (g.get("tw_world") or "").strip().rstrip("/")
        if w and w not in known_urls:
            servers.append({"url": w, "speed": None, "region": None, "players_count": None})
            known_urls.add(w)
    return JSONResponse([{
        "url": s["url"],
        "label": _server_label(s),
    } for s in sorted(servers, key=lambda x: -(x.get("players_count") or 0))])


def _server_label(s: dict) -> str:
    url = s.get("url", "")
    parts = url.replace("https://", "").split(".")
    # ts2.x1.europe.travian.com → "Europe x1 #2"
    try:
        num    = parts[0].lstrip("ts")
        speed  = parts[1].upper() if len(parts) > 1 else ""
        region = parts[2].capitalize() if len(parts) > 2 else ""
        pc     = s.get("players_count")
        suffix = f" · {pc} players" if pc else ""
        return f"{region} {speed} #{num}{suffix}" if region else url
    except Exception:
        return url


@app.post("/admin/servers/travian/{url:path}/fetch-snapshot")
async def admin_fetch_travian_snapshot(request: Request, url: str):
    session, err = _require_admin(request)
    if err: return err
    # Reconstruct URL (FastAPI strips the https://)
    full_url = "https://" + url if not url.startswith("http") else url
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(full_url.rstrip("/") + "/map.sql")
            r.raise_for_status()
        loop = asyncio.get_event_loop()
        villages = await loop.run_in_executor(None, _parse_map_sql, r.text)
        await database.mark_travian_server_snapshot(full_url, len(villages))
        return RedirectResponse(f"/admin/servers?discovered=0&snap_ok={len(villages)}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/servers?snap_err={str(e)[:80]}", status_code=303)


@app.post("/admin/servers/travian/{url:path}/delete")
async def admin_delete_travian_server(request: Request, url: str):
    session, err = _require_admin(request)
    if err: return err
    full_url = "https://" + url if not url.startswith("http") else url
    await database.delete_travian_server(full_url)
    return RedirectResponse("/admin/servers", status_code=303)


@app.post("/admin/servers/{guild_id}/clear-snapshots")
async def admin_clear_snapshots(request: Request, guild_id: str):
    session, err = _require_admin(request)
    if err: return err
    await database.clear_all_snapshots(guild_id)
    return RedirectResponse("/admin/servers", status_code=303)


@app.post("/admin/servers/{guild_id}/archive")
async def admin_archive_guild(request: Request, guild_id: str):
    session, err = _require_admin(request)
    if err: return err
    await database.archive_guild(guild_id)
    return RedirectResponse("/admin/servers", status_code=303)


@app.post("/admin/servers/{guild_id}/unarchive")
async def admin_unarchive_guild(request: Request, guild_id: str):
    session, err = _require_admin(request)
    if err: return err
    await database.unarchive_guild(guild_id)
    return RedirectResponse("/admin/servers?tab=archived", status_code=303)


@app.post("/admin/servers/{guild_id}/set-inactive")
async def admin_set_guild_inactive(request: Request, guild_id: str, active: int = Form(0)):
    session, err = _require_admin(request)
    if err: return err
    await database.set_guild_active_flag(guild_id, bool(active))
    return RedirectResponse("/admin/servers", status_code=303)


@app.get("/admin/servers/archived", response_class=HTMLResponse)
async def admin_servers_archived(request: Request):
    session, err = _require_admin(request)
    if err: return err
    archived = await database.get_archived_guilds()
    return templates.TemplateResponse("admin_servers_archived.html", {
        "request": request,
        "servers": archived,
        "session": session,
    })


@app.post("/admin/customers/{discord_user_id}/delete")
async def admin_delete_customer(request: Request, discord_user_id: str):
    session, err = _require_admin(request)
    if err: return err
    await database.delete_customer(discord_user_id)
    return RedirectResponse("/admin/customers", status_code=303)


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
# Admin — Impressum editor
# ---------------------------------------------------------------------------

@app.get("/admin/impressum", response_class=HTMLResponse)
async def admin_impressum(request: Request):
    session, err = _require_session(request)
    if err: return err
    if session.get("type") != "admin":
        return RedirectResponse("/dashboard", status_code=303)
    impressum = await _get_impressum()
    saved = request.query_params.get("saved")
    return templates.TemplateResponse("admin_impressum.html", {
        "request": request, "impressum": impressum, "saved": saved,
    })

@app.post("/admin/impressum/save")
async def admin_impressum_save(
    request: Request,
    name:        str = Form(""),
    street:      str = Form(""),
    city:        str = Form(""),
    country:     str = Form(""),
    email:       str = Form(""),
    phone:       str = Form(""),
    website:     str = Form(""),
    ust_id:      str = Form(""),
    responsible: str = Form(""),
    updated:     str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    if session.get("type") != "admin":
        return RedirectResponse("/dashboard", status_code=303)
    config = {
        "name": name, "street": street, "city": city, "country": country,
        "email": email, "phone": phone, "website": website,
        "ust_id": ust_id, "responsible": responsible, "updated": updated,
    }
    await database.set_setting("impressum_config", _json_mod.dumps(config))
    return RedirectResponse("/admin/impressum?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Admin — Sidebar Navigation Editor
# ---------------------------------------------------------------------------

_DEFAULT_SIDEBAR_NAV = [
    # ── Home ──────────────────────────────────────────────────────────────
    {"type": "item",  "icon": "home",      "label": "Overview",           "url_suffix": ""},
    # ── Aufklärung ────────────────────────────────────────────────────────
    {"type": "group", "label": "Aufklärung"},
    {"type": "item",  "icon": "map",       "label": "Map",                "url_suffix": "/map"},
    {"type": "item",  "icon": "radar",     "label": "Sector Monitor",     "url_suffix": "/map/sector-monitor"},
    {"type": "item",  "icon": "search",    "label": "Player Intelligence","url_suffix": "/intel"},
    {"type": "item",  "icon": "eye",       "label": "Scout Tracking",     "url_suffix": "/scout"},
    {"type": "item",  "icon": "shield",    "label": "Hero Scout",         "url_suffix": "/defense/hero-scout"},
    {"type": "item",  "icon": "alert",     "label": "Scout Incidents",    "url_suffix": "/scout-incidents"},
    {"type": "item",  "icon": "radar",     "label": "Alliance Tracking",  "url_suffix": "/alliance-tracking"},
    # ── Kämpfe ────────────────────────────────────────────────────────────
    {"type": "group", "label": "Kämpfe"},
    {"type": "item",  "icon": "sword",     "label": "Attacks",            "url_suffix": "/attacks"},
    {"type": "item",  "icon": "scroll",    "label": "Battle Reports",     "url_suffix": "/reports"},
    {"type": "item",  "icon": "alert",     "label": "Alliance Attacks",   "url_suffix": "/attacks/alliance-overview"},
    # ── Farming ───────────────────────────────────────────────────────────
    {"type": "group", "label": "Farming"},
    {"type": "item",  "icon": "wheat",     "label": "Farming Intel",      "url_suffix": "/farming"},
    {"type": "item",  "icon": "list",      "label": "Farmlist Analyst",   "url_suffix": "/farmlist-analyst"},
    {"type": "item",  "icon": "crop",      "label": "Effizienz",          "url_suffix": "/tools/crop-calculator"},
    {"type": "item",  "icon": "truck",     "label": "Traderouten",        "url_suffix": "/tools/crop-calculator?tab=traderoutes"},
    {"type": "item",  "icon": "wheat",     "label": "Grain Supply",       "url_suffix": "/tools/crop-calculator?tab=grain-supply"},
    {"type": "item",  "icon": "wheat",     "label": "Grain Simulations",  "url_suffix": "/tools/grain-simulations"},
    # ── Allianz ───────────────────────────────────────────────────────────
    {"type": "group", "label": "Allianz"},
    {"type": "item",  "icon": "castle",    "label": "My Alliance",        "url_suffix": "/my-ally"},
    {"type": "item",  "icon": "users",     "label": "Members",            "url_suffix": "/allianz/mitglieder"},
    {"type": "item",  "icon": "shield",    "label": "Defense",            "url_suffix": "/verteidigung"},
    {"type": "item",  "icon": "skull",     "label": "Enemies",            "url_suffix": "/enemies"},
    {"type": "item",  "icon": "cross",     "label": "Hospital",           "url_suffix": "/allianz/hospital"},
    {"type": "item",  "icon": "gear",      "label": "Operations",         "url_suffix": "/operations"},
    {"type": "item",  "icon": "flag",      "label": "My Op Plan",         "url_suffix": "/my-operations"},
    {"type": "item",  "icon": "poll",      "label": "Polls",              "url_suffix": "/polls"},
    {"type": "item",  "icon": "blueprint", "label": "Blueprints",         "url_suffix": "/blueprints"},
    # ── Tools ─────────────────────────────────────────────────────────────
    {"type": "group", "label": "Tools"},
    {"type": "item",  "icon": "box",       "label": "Res Push",           "url_suffix": "/res-push"},
    {"type": "item",  "icon": "chart",     "label": "Statistics",         "url_suffix": "/stats"},
    {"type": "item",  "icon": "clock",     "label": "Timer",              "url_suffix": "/timer"},
    {"type": "item",  "icon": "flag",      "label": "Settle List",        "url_suffix": "/settle-list"},
    {"type": "item",  "icon": "fist",      "label": "Combat Power",       "url_suffix": "/my-account/kampfkraft"},
    # ── Account ───────────────────────────────────────────────────────────
    {"type": "group", "label": "Account"},
    {"type": "item",  "icon": "person",    "label": "My Account",         "url_suffix": "/my-account"},
    {"type": "item",  "icon": "bell",      "label": "Notifications",      "url_suffix": "/notifications"},
    {"type": "item",  "icon": "gear",      "label": "Settings",           "url_suffix": "/settings"},
    {"type": "item",  "icon": "card",      "label": "Billing",            "url_suffix": "/billing"},
]


async def _get_sidebar_nav() -> list:
    raw = await database.get_setting("sidebar_nav_config")
    if raw:
        try:
            return _json_mod.loads(raw)
        except Exception:
            pass
    return _DEFAULT_SIDEBAR_NAV


@app.get("/api/sidebar-config")
async def api_sidebar_config(request: Request):
    nav = await _get_sidebar_nav()
    return JSONResponse(nav)


@app.get("/api/my-workspaces")
async def api_my_workspaces(request: Request):
    """Return all personal workspaces for the current user (for the sidebar switcher)."""
    session = _get_session(request)
    if not session:
        return JSONResponse([])
    uid = session.get("uid", "")
    if not uid:
        return JSONResponse([])
    workspaces = await database.get_personal_workspaces(uid)
    result = []
    for w in workspaces:
        tw = (w.get("tw_world") or "").rstrip("/")
        short = tw.replace("https://", "").replace("http://", "") if tw else ""
        result.append({
            "guild_id":      w["guild_id"],
            "guild_name":    w.get("guild_name") or w["guild_id"],
            "tw_world":      tw,
            "tw_world_short": short,
        })
    return JSONResponse(result)


@app.post("/dashboard/create-workspace")
async def dashboard_create_workspace(request: Request):
    """Create a new personal workspace (JSON API for the sidebar switcher)."""
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    uid = session.get("uid", "")
    if not uid:
        return JSONResponse({"error": "No user id"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    name = (body.get("name") or "").strip()[:80]
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=422)
    # Limit: max 10 personal workspaces
    existing = await database.get_personal_workspaces(uid)
    if len(existing) >= 10:
        return JSONResponse({"error": "Maximum 10 Workspaces erreicht"}, status_code=422)
    guild_id = await database.create_personal_workspace(uid, name)
    return JSONResponse({"guild_id": guild_id})


@app.get("/api/sidebar-style")
async def api_sidebar_style(request: Request):
    raw = await database.get_setting("sidebar_style_config")
    if raw:
        try:
            return JSONResponse(_json_mod.loads(raw))
        except Exception:
            pass
    return JSONResponse({"font_family": "", "font_size": ""})


@app.post("/admin/sidebar/style")
async def admin_sidebar_style_save(request: Request):
    session, err = _require_session(request)
    if err: return err
    if session.get("type") != "admin":
        return JSONResponse({"error": "admin only"}, status_code=403)
    body = await request.json()
    font_family = str(body.get("font_family", "")).strip()
    font_size   = str(body.get("font_size", "")).strip()
    await database.set_setting("sidebar_style_config", _json_mod.dumps({
        "font_family": font_family,
        "font_size": font_size,
    }))
    return JSONResponse({"ok": True})


@app.get("/guild/{guild_id}/api/sidebar-badges")
async def sidebar_badges(request: Request, guild_id: str):
    """Return notification counts for sidebar badges + player pro status."""
    session = get_session(request) or {}
    if not session.get("uid"):
        return JSONResponse({"sector_monitor": 0, "my_operations": 0, "has_player_pro": False})
    uid = session["uid"]
    guild, sector_count, waves = await asyncio.gather(
        database.get_guild(guild_id),
        _count_sector_alerts(guild_id),
        database.get_my_op_waves(guild_id, uid),
    )
    g = guild or {}
    return JSONResponse({
        "sector_monitor": sector_count,
        "my_operations": len({w["plan_id"] for w in waves}),
        "has_player_pro": _has_player_pro(g),
        "has_alliance_pro": _has_alliance_pro(g),
    })


async def _count_sector_alerts(guild_id: str) -> int:
    """Count undismissed sector alerts for a guild."""
    try:
        alerts = await database.get_sector_alerts(guild_id, include_dismissed=False, limit=500)
        return len(alerts)
    except Exception:
        return 0


@app.get("/admin/features", response_class=HTMLResponse)
async def admin_features(request: Request):
    session, err = _require_session(request)
    if err: return err
    if session.get("type") != "admin":
        return RedirectResponse("/dashboard", status_code=303)
    plan_rows = [
        {"name": "Free",      "css": "free",         "monthly": "€0",    "annual": "€0",    "servers": "1",  "notes": "Discord server required; no player features"},
        {"name": "Player Pro","css": "player-pro",   "monthly": "€2.99", "annual": "€23.99","servers": "1",  "notes": "Solo / personal workspace; all player features; no alliance Discord features"},
        {"name": "Starter",   "css": "alliance-pro", "monthly": "€9.99", "annual": "€79.99","servers": "1",  "notes": "Small alliances; all Player Pro + alliance features"},
        {"name": "Clan",      "css": "alliance-pro", "monthly": "€14.99","annual": "€119.99","servers": "3", "notes": "Multiple servers"},
        {"name": "Alliance",  "css": "alliance-pro", "monthly": "€24.99","annual": "€199.99","servers": "5", "notes": "Larger alliances"},
        {"name": "Imperium",  "css": "alliance-pro", "monthly": "€49.99","annual": "€399.99","servers": "∞", "notes": "Unlimited servers"},
    ]
    return templates.TemplateResponse("admin_features.html", {
        "request": request, "plan_rows": plan_rows,
    })


@app.get("/admin/sidebar", response_class=HTMLResponse)
async def admin_sidebar(request: Request):
    session, err = _require_session(request)
    if err: return err
    if session.get("type") != "admin":
        return RedirectResponse("/dashboard", status_code=303)
    nav = await _get_sidebar_nav()
    saved = request.query_params.get("saved")
    return templates.TemplateResponse("admin_sidebar.html", {
        "request": request, "nav": nav, "saved": saved,
    })


@app.post("/admin/sidebar/save")
async def admin_sidebar_save(request: Request):
    session, err = _require_session(request)
    if err: return err
    if session.get("type") != "admin":
        return RedirectResponse("/dashboard", status_code=303)
    body = await request.json()
    nav = body.get("nav", [])
    # Sanitise: only allow known fields
    def _clean_item(item):
        t = item.get("type")
        if t == "group":
            return {"type": "group", "label": str(item.get("label", ""))[:60]}
        if t == "item":
            bc = str(item.get("border_color", ""))[:20]
            # Migrate old requires_pro bool → requires_plan string
            rp = item.get("requires_plan", "")
            if not rp and item.get("requires_pro"):
                rp = "player_pro"
            if rp not in ("player_pro", "alliance", ""):
                rp = ""
            return {
                "type":         "item",
                "icon":         str(item.get("icon", "home"))[:30],
                "label":        str(item.get("label", ""))[:60],
                "url_suffix":   str(item.get("url_suffix", ""))[:200],
                "disabled":     bool(item.get("disabled", False)),
                "border_color": bc if bc.startswith("#") or bc == "" else "",
                "requires_plan": rp,
            }
        if t == "submenu":
            children = [_clean_item(c) for c in (item.get("children") or []) if c.get("type") == "item"]
            return {
                "type":     "submenu",
                "icon":     str(item.get("icon", "home"))[:30],
                "label":    str(item.get("label", ""))[:60],
                "children": children,
            }
        return None

    clean = [x for x in (_clean_item(i) for i in nav) if x is not None]
    await database.set_setting("sidebar_nav_config", _json_mod.dumps(clean))
    return JSONResponse({"ok": True})


@app.post("/admin/sidebar/reset")
async def admin_sidebar_reset(request: Request):
    session, err = _require_session(request)
    if err: return err
    if session.get("type") != "admin":
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    await database.set_setting("sidebar_nav_config", _json_mod.dumps(_DEFAULT_SIDEBAR_NAV))
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Admin — preview mode (simulate different subscription tiers)
# ---------------------------------------------------------------------------

@app.post("/admin/preview/set")
async def admin_preview_set(
    request: Request,
    guild_id: str = Form(""),
    plan: str = Form("starter"),
):
    """Store a preview override in the admin's session cookie."""
    session, err = _require_admin(request)
    if err: return err
    plan = plan if plan in ("free", "player_pro", "starter", "clan", "alliance", "imperium") else "starter"
    new_session = dict(session)
    if guild_id:
        new_session["preview"] = {"guild_id": guild_id, "plan": plan}
    else:
        new_session.pop("preview", None)
    token = create_session(new_session)
    redirect_to = f"/guild/{guild_id}" if guild_id else "/admin"
    resp = RedirectResponse(redirect_to, status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.post("/admin/preview/clear")
async def admin_preview_clear(request: Request, redirect: str = Form("/dashboard")):
    """Clear the preview override from the admin's session cookie."""
    session, err = _require_admin(request)
    if err: return err
    new_session = dict(session)
    new_session.pop("preview", None)
    token = create_session(new_session)
    resp = RedirectResponse(redirect, status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return resp


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

_IMPRESSUM_DEFAULTS = {
    "name":        os.environ.get("IMPRESSUM_NAME", "Maximilian Frischholz"),
    "street":      os.environ.get("IMPRESSUM_STREET", "Eberhard-Wildermuth-Straße 58"),
    "city":        os.environ.get("IMPRESSUM_CITY", "34121 Kassel"),
    "country":     os.environ.get("IMPRESSUM_COUNTRY", "Deutschland"),
    "email":       os.environ.get("IMPRESSUM_EMAIL", "kontakt@travops.online"),
    "phone":       os.environ.get("IMPRESSUM_PHONE", ""),
    "website":     os.environ.get("IMPRESSUM_WEBSITE", "https://travops.online"),
    "ust_id":      os.environ.get("IMPRESSUM_UST_ID", ""),
    "responsible": os.environ.get("IMPRESSUM_RESPONSIBLE", "Maximilian Frischholz, Anschrift wie oben"),
    "updated":     os.environ.get("IMPRESSUM_UPDATED", "Mai 2026"),
}

async def _get_impressum() -> dict:
    raw = await database.get_setting("impressum_config")
    if raw:
        try:
            data = _json_mod.loads(raw)
            return {**_IMPRESSUM_DEFAULTS, **data}
        except Exception:
            pass
    return dict(_IMPRESSUM_DEFAULTS)

async def _legal_ctx(request: Request) -> dict:
    impressum = await _get_impressum()
    return {
        "request": request,
        "impressum": impressum,
        "current_year": _dt.datetime.utcnow().year,
    }


@app.get("/impressum", response_class=HTMLResponse)
async def page_impressum(request: Request):
    return templates.TemplateResponse("impressum.html", await _legal_ctx(request))


@app.get("/datenschutz", response_class=HTMLResponse)
async def page_datenschutz(request: Request):
    return templates.TemplateResponse("datenschutz.html", await _legal_ctx(request))


@app.get("/agb", response_class=HTMLResponse)
async def page_agb(request: Request):
    return templates.TemplateResponse("agb.html", await _legal_ctx(request))


@app.get("/cookies", response_class=HTMLResponse)
async def page_cookies(request: Request):
    return templates.TemplateResponse("cookies.html", await _legal_ctx(request))


# ---------------------------------------------------------------------------
# API — popup config (public, for JS fetch)
# ---------------------------------------------------------------------------

# ──────────────────────────────────────────────────────────────────────────────
# DEF CROP TRACKER API
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/def-crop/save")
async def api_def_crop_save(request: Request):
    """Bot posts here when a user logs sent def troops."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    guild_id          = body.get("guild_id", "")
    sender_discord_id = body.get("sender_discord_id", "")
    if not guild_id or not sender_discord_id:
        return JSONResponse({"error": "missing fields"}, status_code=400)
    send_id = await database.save_def_crop_send(
        guild_id          = guild_id,
        sender_discord_id = sender_discord_id,
        sender_name       = body.get("sender_name", ""),
        recipient_name    = body.get("recipient_name", ""),
        recipient_village = body.get("recipient_village", ""),
        tribe             = body.get("tribe", ""),
        troops            = body.get("troops", {}),
        crop_per_hour     = float(body.get("crop_per_hour", 0)),
        notes             = body.get("notes", ""),
    )
    return JSONResponse({"ok": True, "id": send_id})


@app.get("/guild/{guild_id}/api/def-crop/my")
async def api_def_crop_my(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    discord_id = session.get("uid", "")
    sends = await database.get_def_crop_sends(guild_id, discord_id)
    return JSONResponse(sends)


@app.post("/guild/{guild_id}/api/def-crop/{send_id}/deactivate")
async def api_def_crop_deactivate(request: Request, guild_id: str, send_id: int):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.deactivate_def_crop_send(send_id, guild_id)
    return JSONResponse({"ok": True})


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


@app.get("/api/my-blueprints")
async def api_my_blueprints(request: Request):
    """Return player blueprints for the logged-in user across all guilds — for the Chrome Extension."""
    session = get_session(request)
    if not session:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    uid = session.get("uid", "")
    username = session.get("username", "")

    # Collect blueprints from all guilds the user has access to
    all_guilds = await database.get_all_guilds()
    if session.get("guilds") is not None:
        allowed = set(session["guilds"])
        user_guilds = [g for g in all_guilds if g["guild_id"] in allowed or g.get("workspace_owner_id") == uid]
    else:
        user_guilds = all_guilds

    results = []
    for g in user_guilds:
        bps = await database.get_player_blueprints(g["guild_id"])
        for bp in bps:
            if bp.get("player_name", "").lower() == username.lower() or not bp.get("player_name"):
                full = await database.get_player_blueprint(bp["id"], g["guild_id"])
                if full:
                    results.append({
                        "id": full["id"],
                        "guild_id": g["guild_id"],
                        "guild_name": g["guild_name"],
                        "player_name": full.get("player_name", ""),
                        "village_name": full.get("village_name", ""),
                        "village_coords": full.get("village_coords", ""),
                        "template_name": full.get("template_name", ""),
                        "tribe": full.get("tribe", ""),
                        "steps": [
                            {
                                "id": s["id"],
                                "order_num": s["order_num"],
                                "step_type": s["step_type"],
                                "title": s["title"],
                                "description": s.get("description", ""),
                                "target": s.get("target", ""),
                                "completed": bool(s.get("completed", 0)),
                                "completed_at": s.get("completed_at", ""),
                            }
                            for s in full.get("steps", [])
                        ],
                        "total_steps": full.get("total_steps", len(full.get("steps", []))),
                        "done_steps": sum(1 for s in full.get("steps", []) if s.get("completed")),
                    })
    return JSONResponse({"blueprints": results})


@app.post("/api/blueprint-step/toggle")
async def api_blueprint_step_toggle(request: Request):
    """Toggle a blueprint step from the Chrome Extension."""
    session = get_session(request)
    if not session:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    body = await request.json()
    blueprint_id = int(body.get("blueprint_id", 0))
    step_id = int(body.get("step_id", 0))
    guild_id = body.get("guild_id", "")
    if not blueprint_id or not step_id or not guild_id:
        return JSONResponse({"error": "missing_params"}, status_code=400)
    new_state = await database.toggle_blueprint_step(blueprint_id, step_id)
    return JSONResponse({"completed": bool(new_state)})


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

    Supports two formats:

    FORMAT A — Table (Hospital Overview, EN + DE):
      "in progress\tWounded troops"  or  "Im Gange\tVerwundete Truppen"
      TroopA\tTroopB\tTroopC\t...        ← header row with troop names
      Village1\t•\t0\t14\t0\t...         ← village rows (• or - as 2nd col)
      Village2\t-\t10\t0\t0\t...

    FORMAT B — Row-by-row with finish time (older / detail pages):
      Village name
      TroopName\tCount\tHH:MM:SS

    Returns list of {village_name, troop_name, count, heal_finish}.
    """
    import re as _re

    # Known troop names (EN + DE) for header detection
    _KNOWN_TROOPS = {
        # Romans EN/DE
        'legionnaire','praetorian','imperian','equites legati','equites imperatoris',
        'equites caesaris','fire catapult','senator','settler',
        'legionär','prätorianer','imperianer','feuerkatapult',
        # Teutons EN/DE
        'clubswinger','spearman','scout','paladin','teutonic knight','catapult','chief',
        'keulenschwinger','speerkämpfer','späher','teutonischer ritter','katapult',
        'häuptling','teutonen-rammbock',
        # Gauls EN/DE
        'phalanx','swordsman','pathfinder','theutates thunder','druidrider','haeduan',
        'schwertkämpfer','kundschafter','theutates-blitz','druider','häduaner',
        # shared
        'ram','rammbock',
        # Egyptians EN/DE
        'slave militia','ash warden','khopesh warrior','sopdu explorer',
        'anhur guard','resheph chariot','stone catapult','nomarch',
        'sklavenmiliz','aschenwächter','khopesh-kämpfer','sopdu-kundschafter',
        'anhur-wächter','resheph-streitwagen','steinkatapult',
        # Huns EN/DE
        'mercenary','bowman','spotter','steppe rider','marksman','marauder',
        'logades','cataphract',
        'söldner','bogenschütze','kundschafter','steppenreiter','scharfschütze',
        # Spartans EN/DE
        'hoplite','senator','spartan',
        'hoplit',
    }

    # Strip Unicode bidirectional / formatting marks
    text = _re.sub(r'[​-‏‪-‮⁦-⁩﻿]', '', text)
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    # --- Detect format ---
    _TABLE_INTRO = _re.compile(
        r'^(in progress|im gange|wounded troops|verwundete truppen)', _re.IGNORECASE
    )
    _TIME_ONLY = _re.compile(r'^\d{1,2}:\d{2}:\d{2}$')
    _DATETIME  = _re.compile(r'^\d{1,2}\.\d{1,2}\.\d{4}\s+\d{1,2}:\d{2}:\d{2}$')
    _INDICATOR = _re.compile(r'^[•\-–·]$')  # hospital present/absent indicator

    def _clean_num(s: str) -> int:
        cleaned = _re.sub(r'[^\d]', '', s)
        return int(cleaned) if cleaned else 0

    def _is_troop_header(parts: list) -> bool:
        hits = sum(1 for p in parts if p.lower() in _KNOWN_TROOPS)
        return hits >= 2

    # Check for table format: find header row with troop names
    header_idx = None
    troop_cols: list[str] = []
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        if _TABLE_INTRO.match(stripped):
            continue  # skip intro row
        if '\t' in stripped:
            parts = [p.strip() for p in stripped.split('\t')]
            parts_clean = [p for p in parts if p]
            if _is_troop_header(parts_clean):
                header_idx = i
                troop_cols = parts_clean
                break

    # --- FORMAT A: Table ---
    if header_idx is not None:
        entries = []
        for raw in lines[header_idx + 1:]:
            stripped = raw.strip()
            if not stripped or '\t' not in stripped:
                continue
            parts = [p.strip() for p in stripped.split('\t')]
            # First non-empty = village name
            village = parts[0] if parts else ''
            if not village:
                continue
            # Determine where counts start: skip •/- indicator col if present
            rest = parts[1:]
            start = 0
            if rest and _INDICATOR.match(rest[0]):
                start = 1
            counts = rest[start:]
            for j, troop in enumerate(troop_cols):
                if j >= len(counts):
                    break
                try:
                    cnt = _clean_num(counts[j])
                except Exception:
                    cnt = 0
                if cnt > 0:
                    entries.append({
                        'village_name': village,
                        'troop_name':   troop,
                        'count':        cnt,
                        'heal_finish':  None,
                    })
        return entries

    # --- FORMAT B: Row-by-row (village header + tab-separated troop rows) ---
    _SKIP = _re.compile(
        r'^Lazarett$|^Hospital$|^Krankenhaus$|^Heilung$|^Truppe$|^Anzahl$|'
        r'^Fertig$|^Troop$|^Count$|^Healing finish$|^Finish$|'
        r'^Dorf$|^Village$|^Overview$|^Übersicht$|'
        r'^Homepage|^\© \d{4}|Discord|Support|Game rules|Terms|Imprint|'
        r'^Server time|^TravOps|^Profile|^Rally|^Management|'
        r'^\s*Troop\s+Count\s+|^\s*Truppe\s+Anzahl',
        _re.IGNORECASE,
    )
    entries = []
    current_village = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if '\t' in stripped:
            parts = [p.strip() for p in stripped.split('\t')]
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                troop_name = parts[0]
                if _SKIP.match(troop_name):
                    continue
                try:
                    count = _clean_num(parts[1])
                except Exception:
                    continue
                if count == 0:
                    continue
                heal_finish = None
                if len(parts) >= 3:
                    t = parts[2].strip()
                    if _TIME_ONLY.match(t) or _DATETIME.match(t):
                        heal_finish = t
                    elif len(parts) >= 4:
                        combined = f"{parts[2]} {parts[3]}".strip()
                        if _DATETIME.match(combined):
                            heal_finish = combined
                if current_village:
                    entries.append({
                        'village_name': current_village,
                        'troop_name':   troop_name,
                        'count':        count,
                        'heal_finish':  heal_finish,
                    })
            continue
        if _SKIP.search(stripped):
            continue
        current_village = stripped
    return entries


# ---------------------------------------------------------------------------
# Routes — Lazarett-Tracker (Hospital)
# ---------------------------------------------------------------------------

@app.post("/guild/{guild_id}/my-account/hospital")
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

    entries = parse_hospital(hospital_text)
    await database.save_hospital_data(
        guild_id=guild_id,
        discord_user_id=session.get("uid", ""),
        discord_username=session.get("username"),
        entries=entries,
    )
    return RedirectResponse(
        f"/guild/{guild_id}/my-account?hospital_uploaded={len(entries)}",
        status_code=303,
    )


@app.post("/guild/{guild_id}/my-account/hospital/clear")
async def hospital_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    await database.delete_hospital_data(guild_id, session.get("uid", ""))
    return RedirectResponse(f"/guild/{guild_id}/my-account?hospital_cleared=1", status_code=303)


@app.get("/guild/{guild_id}/allianz/hospital", response_class=HTMLResponse)
async def allianz_hospital(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_alliance(guild, guild_id)
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


@app.get("/guild/{guild_id}/alliance-tracking", response_class=HTMLResponse)
async def alliance_tracking_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    watched = await database.get_watched_alliances(guild_id)
    top_alliances = await database.get_top_alliances(guild_id, limit=10)
    meta_groups = await database.get_meta_groups(guild_id)
    meta_alliances = await database.get_meta_alliances(guild_id)
    ally_group = await database.get_ally_group_for_guild(guild_id)
    saved = request.query_params.get("saved")
    return templates.TemplateResponse("alliance_tracking.html", {
        "request": request, "guild": guild,
        "watched": watched, "top_alliances": top_alliances,
        "meta_groups": meta_groups, "meta_alliances": meta_alliances,
        "ally_group": ally_group,
        "saved": saved,
    })


@app.get("/guild/{guild_id}/alliance-tracking/meta/own", response_class=HTMLResponse)
async def alliance_tracking_meta_own(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    ally_group = await database.get_ally_group_for_guild(guild_id)
    alliances = []
    if ally_group:
        for f in ("ally_name","wing1_name","wing2_name"):
            v = ally_group.get(f,"") or ""
            if v: alliances.append(v)
    group = {"id": "own", "meta_name": "Eigene Meta", "alliances": alliances}
    data = await database.get_meta_combined_tracking(guild_id, alliances)
    meta_groups = await database.get_meta_groups(guild_id)
    watched = await database.get_watched_alliances(guild_id)
    return templates.TemplateResponse("alliance_tracking_meta.html", {
        "request": request, "guild": guild,
        "group": group, "data": data,
        "meta_groups": meta_groups, "watched": watched,
    })


@app.get("/guild/{guild_id}/alliance-tracking/meta/{group_id}", response_class=HTMLResponse)
async def alliance_tracking_meta(request: Request, guild_id: str, group_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    meta_groups = await database.get_meta_groups(guild_id)
    group = next((g for g in meta_groups if g["id"] == group_id), None)
    if not group:
        return RedirectResponse(f"/guild/{guild_id}/alliance-tracking")
    data = await database.get_meta_combined_tracking(guild_id, group["alliances"])
    watched = await database.get_watched_alliances(guild_id)
    return templates.TemplateResponse("alliance_tracking_meta.html", {
        "request": request, "guild": guild,
        "group": group, "data": data,
        "meta_groups": meta_groups, "watched": watched,
    })


@app.post("/guild/{guild_id}/alliance-tracking/watch")
async def alliance_tracking_watch(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    form = await request.form()
    name = form.get("alliance_name", "").strip()
    if name:
        await database.watch_alliance(guild_id, name)
    return RedirectResponse(f"/guild/{guild_id}/alliance-tracking?saved=1", status_code=303)


@app.post("/guild/{guild_id}/alliance-tracking/{alliance_name}/unwatch")
async def alliance_tracking_unwatch(request: Request, guild_id: str, alliance_name: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    await database.unwatch_alliance(guild_id, alliance_name)
    return RedirectResponse(f"/guild/{guild_id}/alliance-tracking", status_code=303)


@app.get("/guild/{guild_id}/alliance-tracking/{alliance_name}", response_class=HTMLResponse)
async def alliance_tracking_detail(request: Request, guild_id: str, alliance_name: str):
    from urllib.parse import unquote
    alliance_name = unquote(alliance_name)
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    data = await database.get_alliance_tracking_data(guild_id, alliance_name)
    flows = await database.get_alliance_player_flows(guild_id, alliance_name)
    watched = await database.get_watched_alliances(guild_id)
    meta_groups = await database.get_meta_groups(guild_id)
    ally_group = await database.get_ally_group_for_guild(guild_id)
    is_watched = any(w["alliance_name"].lower() == alliance_name.lower() for w in watched)
    return templates.TemplateResponse("alliance_tracking_detail.html", {
        "request": request, "guild": guild,
        "alliance_name": alliance_name,
        "data": data, "flows": flows,
        "is_watched": is_watched, "watched": watched,
        "meta_groups": meta_groups, "ally_group": ally_group,
    })


@app.get("/guild/{guild_id}/enemies", response_class=HTMLResponse)
async def enemies_page(request: Request, guild_id: str, saved: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    enemies = await database.get_enemies(guild_id)
    report_channel = await database.get_report_channel(guild_id)
    top_alliances = await database.get_top_alliances(guild_id, limit=5)
    return templates.TemplateResponse("enemies.html", {
        "request": request,
        "guild": guild,
        "enemies": enemies,
        "saved": saved,
        "report_channel": report_channel,
        "top_alliances": top_alliances,
    })


@app.get("/guild/{guild_id}/enemies/{player_name}", response_class=HTMLResponse)
async def enemy_detail(request: Request, guild_id: str, player_name: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    enemy = await database.get_enemy(guild_id, player_name)
    if not enemy:
        return RedirectResponse(f"/guild/{guild_id}/enemies", status_code=303)
    history = await database.get_enemy_scout_history(guild_id, player_name)
    troop_entries    = await database.get_enemy_troop_entries(guild_id, player_name)
    village_history  = await database.get_enemy_village_history(guild_id, player_name)
    village_details  = await database.get_enemy_village_details(guild_id, player_name)
    return templates.TemplateResponse("enemy_detail.html", {
        "request": request,
        "guild": guild,
        "enemy": enemy,
        "history": history,
        "troop_entries": troop_entries,
        "village_history": village_history,
        "village_details": village_details,
        "flash": request.query_params.get("flash", ""),
        "vcount": request.query_params.get("vcount",""),
        "ecount": request.query_params.get("ecount",""),
    })


@app.post("/guild/{guild_id}/enemies/report-channel/set")
async def set_report_channel(
    request: Request, guild_id: str,
    channel_id: str = Form(""), channel_name: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.set_report_channel(guild_id, channel_id.strip() or None, channel_name.strip() or None)
    return RedirectResponse(f"/guild/{guild_id}/enemies?saved=report_channel", status_code=303)


@app.post("/guild/{guild_id}/enemies/report-channel/clear")
async def clear_report_channel(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.set_report_channel(guild_id, None, None)
    return RedirectResponse(f"/guild/{guild_id}/enemies?saved=report_channel_cleared", status_code=303)


@app.post("/guild/{guild_id}/enemies/report-channel/create")
async def create_report_channel(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "http://bot:7777/api/create-report-channel",
                json={"guild_id": guild_id},
            )
            data = resp.json()
    except Exception as e:
        return RedirectResponse(
            f"/guild/{guild_id}/enemies?saved=channel_create_error&msg={str(e)[:80]}",
            status_code=303,
        )
    if data.get("ok"):
        return RedirectResponse(f"/guild/{guild_id}/enemies?saved=report_channel", status_code=303)
    return RedirectResponse(
        f"/guild/{guild_id}/enemies?saved=channel_create_error&msg={data.get('error','unknown')[:80]}",
        status_code=303,
    )


# ── Request Hub ───────────────────────────────────────────────────────────────
@app.post("/guild/{guild_id}/request-hub/create")
async def create_request_hub(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "http://bot:7777/api/create-request-hub",
                json={"guild_id": guild_id},
            )
            data = resp.json()
            if data.get("ok"):
                return RedirectResponse(f"/guild/{guild_id}?saved=hub_created", status_code=303)
            print(f"[hub_create] bot error: {data}")
            return RedirectResponse(f"/guild/{guild_id}?error=hub_create_failed&detail={data.get('error','')}", status_code=303)
    except Exception as e:
        print(f"[hub_create] exception: {type(e).__name__}: {e}")
        return RedirectResponse(f"/guild/{guild_id}?error=hub_create_failed", status_code=303)


@app.post("/guild/{guild_id}/request-hub/refresh")
async def refresh_request_hub(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "http://bot:7777/api/refresh-request-hub",
                json={"guild_id": guild_id},
            )
            data = resp.json()
            if data.get("ok"):
                return RedirectResponse(f"/guild/{guild_id}?saved=hub_refreshed", status_code=303)
            return RedirectResponse(f"/guild/{guild_id}?error=hub_refresh_failed&detail={data.get('error','')}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/guild/{guild_id}?error=hub_refresh_failed", status_code=303)


@app.post("/guild/{guild_id}/request-hub/clear")
async def clear_request_hub(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.clear_request_hub(guild_id)
    return RedirectResponse(f"/guild/{guild_id}?saved=hub_cleared", status_code=303)


@app.post("/guild/{guild_id}/digest/set")
async def digest_set(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    if not (_is_leader(session, guild_id) or session.get("type") == "admin"):
        return RedirectResponse(f"/guild/{guild_id}", status_code=303)
    form = await request.form()
    channel_id = (form.get("channel_id") or "").strip()
    if channel_id:
        await database.update_guild_config_fields(guild_id, digest_channel_id=channel_id)
    return RedirectResponse(f"/guild/{guild_id}?saved=digest", status_code=303)


@app.post("/guild/{guild_id}/digest/clear")
async def digest_clear(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    if not (_is_leader(session, guild_id) or session.get("type") == "admin"):
        return RedirectResponse(f"/guild/{guild_id}", status_code=303)
    await database.update_guild_config_fields(guild_id, digest_channel_id=None)
    return RedirectResponse(f"/guild/{guild_id}?saved=digest_cleared", status_code=303)


@app.post("/guild/{guild_id}/clear-stale-channels")
async def clear_stale_channels(request: Request, guild_id: str):
    """Remove DB entries for channels that no longer exist in Discord."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    # Ask bot which channels are stale
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post("http://bot:7777/api/check-permissions", json={"guild_id": guild_id})
            issues = resp.json().get("issues", []) if resp.status_code == 200 else []
    except Exception:
        issues = []
    stale_ids = {i["channel_id"] for i in issues if not i.get("missing")}
    if stale_ids:
        await database.clear_stale_channel_refs(guild_id, stale_ids)
    return RedirectResponse(f"/guild/{guild_id}?saved=channels_cleaned", status_code=303)


# ── Verteidigung ──────────────────────────────────────────────────────────────
@app.get("/guild/{guild_id}/verteidigung", response_class=HTMLResponse)
async def verteidigung_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    uid = session.get("uid", "")
    # If workspace guild → try to redirect to the real guild where the user is an ally member
    if guild_id.startswith("ws_"):
        membership_guild = await database.get_ally_membership_guild_id(uid)
        if membership_guild:
            return RedirectResponse(f"/guild/{membership_guild}/verteidigung", status_code=302)
    # Allow access if: alliance plan OR user is member of the guild's ally group
    alliance_err = await _require_alliance(guild, guild_id)
    if alliance_err:
        ally_group = await database.get_ally_group_for_guild(guild_id)
        membership  = await database.get_ally_membership(guild_id, uid) if ally_group else None
        is_ally_member = bool(membership and membership.get("status") == "approved")
        if not is_ally_member:
            return alliance_err
    show = request.query_params.get("show", "open")  # open | all | closed
    all_channels = await database.get_defend_channels(guild_id)
    if show == "open":
        channels = [c for c in all_channels if c.get("status") != "closed"]
    elif show == "closed":
        channels = [c for c in all_channels if c.get("status") == "closed"]
    else:
        channels = all_channels
    can_close = (
        session.get("type") == "admin"
        or guild.get("owner_discord_id") == uid
        or "defend_manage" in await database.get_member_permissions(guild_id, uid)
        or "ally_manage"   in await database.get_member_permissions(guild_id, uid)
    )
    contributions = await database.get_defend_contributions_for_guild(guild_id)
    my_villages = await database.get_my_villages_for_travel(guild_id, uid)
    perms = await database.get_member_permissions(guild_id, uid)
    can_edit_all = (
        session.get("type") == "admin"
        or guild.get("owner_discord_id") == uid
        or "ally_manage" in perms
        or "defend_manage" in perms
        or "defend_edit" in perms
    )
    my_sent = await database.get_my_defend_sent(guild_id, uid)
    return templates.TemplateResponse("verteidigung.html", {
        "request": request,
        "guild": guild,
        "channels": channels,
        "contributions": contributions,
        "my_sent": my_sent,
        "my_villages": my_villages,
        "uid": uid,
        "can_edit_all": can_edit_all,
        "tw_world": guild.get("tw_world") or "",
        "show": show,
        "total_open":   sum(1 for c in all_channels if c.get("status") != "closed"),
        "total_closed": sum(1 for c in all_channels if c.get("status") == "closed"),
        "saved": request.query_params.get("saved", ""),
        "flash": request.query_params.get("flash", ""),
        "can_close": can_close,
    })


@app.post("/guild/{guild_id}/defend/{channel_id}/close")
async def defend_close(request: Request, guild_id: str, channel_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    is_admin   = session.get("type") == "admin"
    is_owner   = guild and guild.get("owner_discord_id") == uid
    perms      = await database.get_member_permissions(guild_id, uid)
    has_rights = "defend_manage" in perms or "ally_manage" in perms
    if not (is_admin or is_owner or has_rights):
        return RedirectResponse(f"/guild/{guild_id}/verteidigung?flash=no_permission", status_code=303)
    await database.close_defend_channel(channel_id)
    # Tell bot to move channel to archive category
    bot_msg = ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post("http://bot:7777/api/archive-defend-channel",
                                  json={"guild_id": guild_id, "channel_id": channel_id})
            data = r.json()
            if data.get("ok"):
                bot_msg = "archived"
            else:
                bot_msg = f"bot_error:{data.get('error','?')[:80]}"
    except Exception as e:
        bot_msg = f"bot_offline:{str(e)[:60]}"
    from urllib.parse import quote
    return RedirectResponse(
        f"/guild/{guild_id}/verteidigung?flash=closed&bot={quote(bot_msg)}", status_code=303
    )


@app.post("/guild/{guild_id}/defend/{channel_id}/reopen")
async def defend_reopen(request: Request, guild_id: str, channel_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    is_admin   = session.get("type") == "admin"
    is_owner   = guild and guild.get("owner_discord_id") == uid
    perms      = await database.get_member_permissions(guild_id, uid)
    has_rights = "defend_manage" in perms or "ally_manage" in perms
    if not (is_admin or is_owner or has_rights):
        return RedirectResponse(f"/guild/{guild_id}/verteidigung?flash=no_permission", status_code=303)
    async with __import__("aiosqlite").connect(database.DB_PATH) as db:
        await db.execute("UPDATE defend_channels SET status='open' WHERE channel_id=?", (channel_id,))
        await db.commit()
    bot_msg = ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post("http://bot:7777/api/unarchive-defend-channel",
                                  json={"guild_id": guild_id, "channel_id": channel_id})
            data = r.json()
            bot_msg = "archived" if data.get("ok") else f"bot_error:{data.get('error','?')[:80]}"
    except Exception as e:
        bot_msg = f"bot_offline:{str(e)[:60]}"
    from urllib.parse import quote
    return RedirectResponse(
        f"/guild/{guild_id}/verteidigung?show=closed&flash=reopened&bot={quote(bot_msg)}", status_code=303
    )


@app.post("/guild/{guild_id}/defend/{channel_id}/update-request")
async def defend_update_request(request: Request, guild_id: str, channel_id: str):
    """Edit a defend channel's metadata (attacker, coords, arrival, goal, notes)."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    guild = await database.get_guild(guild_id)
    is_admin   = session.get("type") == "admin"
    is_owner   = guild and guild.get("owner_discord_id") == uid
    perms      = await database.get_member_permissions(guild_id, uid)
    has_rights = "defend_manage" in perms or "ally_manage" in perms or "defend_edit" in perms
    if not (is_admin or is_owner or has_rights):
        return RedirectResponse(f"/guild/{guild_id}/verteidigung?flash=no_permission", status_code=303)
    form = await request.form()
    attacker     = str(form.get("attacker", "")).strip()
    coords       = str(form.get("coords", "")).strip()
    arrival_time = str(form.get("arrival_time", "")).strip()
    goal         = str(form.get("goal", "")).strip()
    notes        = str(form.get("notes", "")).strip()
    async with __import__("aiosqlite").connect(database.DB_PATH) as db:
        await db.execute("""
            UPDATE defend_channels
            SET attacker=?, coords=?, arrival_time=?, goal=?, notes=?
            WHERE channel_id=? AND guild_id=?
        """, (attacker, coords, arrival_time, goal, notes, channel_id, guild_id))
        await db.commit()
    # Refresh Discord tracking embed
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post("http://bot:7777/api/refresh-defend-tracking",
                              json={"channel_id": channel_id})
    except Exception:
        pass
    show = str(form.get("show", "open"))
    return RedirectResponse(f"/guild/{guild_id}/verteidigung?show={show}&flash=saved", status_code=303)


@app.post("/api/village-ts")
async def api_save_village_ts(request: Request):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False}, status_code=401)
    uid = session.get("uid", "")
    data = await request.json()
    x = int(data.get("x", 0))
    y = int(data.get("y", 0))
    ts = int(data.get("ts", 0))
    await database.save_village_ts_level(uid, x, y, ts)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/attacks/api/dc-grain-sim/save")
async def api_save_dc_grain_sim(request: Request, guild_id: str):
    """Save a grain simulation snapshot for a def-call channel."""
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"ok": False}, status_code=403)
    uid = session.get("uid", "")
    data = await request.json()
    channel_id   = str(data.get("channel_id", ""))
    if not channel_id:
        return JSONResponse({"ok": False, "error": "missing channel_id"})
    import json as _json
    new_id = await database.save_dc_grain_sim(
        guild_id     = guild_id,
        channel_id   = channel_id,
        saved_by     = uid,
        current_grain= int(data.get("current_grain", 0)),
        production_h = int(data.get("production_h", 0)),
        capacity     = int(data.get("capacity", 0)),
        consumption_h= int(data.get("consumption_h", 0)),
        net_per_h    = int(data.get("net_per_h", 0)),
        deliveries_json = _json.dumps(data.get("deliveries", [])),
        run_out_at   = data.get("run_out_at") or None,
        market_paste = data.get("market_paste", ""),
    )
    return JSONResponse({"ok": True, "id": new_id})


@app.get("/guild/{guild_id}/attacks/api/dc-grain-sim/{channel_id}")
async def api_get_dc_grain_sims(request: Request, guild_id: str, channel_id: str):
    """
    Return saved grain sims for a def-call.
    - ≥100 troops sent → full data
    - <100 troops       → suggestion only (run_out_at of latest sim)
    """
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"ok": False}, status_code=403)
    uid = session.get("uid", "")

    troops = await database.get_dc_troop_count_for_user(guild_id, channel_id, uid)
    sims   = await database.get_dc_grain_sims(guild_id, channel_id)

    if troops >= 100:
        # Full access
        import json as _json
        result = []
        for s in sims:
            try:
                deliveries = _json.loads(s.get("deliveries_json") or "[]")
            except Exception:
                deliveries = []
            result.append({
                "id":            s["id"],
                "saved_at":      s["saved_at"],
                "saved_by":      s["saved_by"],
                "current_grain": s["current_grain"],
                "production_h":  s["production_h"],
                "capacity":      s["capacity"],
                "consumption_h": s["consumption_h"],
                "net_per_h":     s["net_per_h"],
                "deliveries":    deliveries,
                "run_out_at":    s["run_out_at"],
                "market_paste":  s["market_paste"],
            })
        return JSONResponse({"ok": True, "access": "full", "troops": troops, "sims": result})
    else:
        # Suggestion only: just the run_out_at from the latest sim
        latest = sims[0] if sims else None
        suggestion = None
        if latest and latest.get("run_out_at"):
            suggestion = latest["run_out_at"]
        return JSONResponse({
            "ok": True, "access": "suggestion",
            "troops": troops,
            "suggestion": suggestion,  # e.g. "19:28" or None
            "has_sims": len(sims) > 0,
        })


@app.post("/api/village-crop")
async def api_save_village_crop(request: Request):
    """Save a manual grain/h override for one of the user's villages."""
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False}, status_code=401)
    uid = session.get("uid", "")
    data = await request.json()
    x    = int(data.get("x", 0))
    y    = int(data.get("y", 0))
    crop = int(data.get("crop", -1))
    await database.save_village_crop_override(uid, x, y, crop)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/defend/update-sent")
async def defend_update_sent(request: Request, guild_id: str):
    """Allow a user to edit their own defend_sent entry (amount + troop type)."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    form = await request.form()
    sent_id = int(form.get("sent_id", 0))
    channel_id = str(form.get("channel_id", ""))
    amount_raw = str(form.get("amount_raw", "")).strip()
    troop_type = str(form.get("troop_type", "")).strip()

    # Parse amount_raw → int
    import re as _re
    def _parse_num(s: str) -> int:
        s = s.replace(".", "").replace(",", "").replace(" ", "").upper()
        m = _re.match(r"(\d+(?:\.\d+)?)(K|M)?", s)
        if not m:
            return 0
        n = float(m.group(1))
        if m.group(2) == "K": n *= 1_000
        elif m.group(2) == "M": n *= 1_000_000
        return int(n)

    amount_parsed = _parse_num(amount_raw)

    # Determine grain per unit from known unit table
    _UNIT_GRAIN = {
        "equites legati": 2, "equites imperatoris": 3, "equites caesaris": 4,
        "paladin": 2, "teutonischer ritter": 3,
        "treverer-späher": 2, "druidenreiter": 2, "haeduer": 3,
        "steppenkämpfer": 2, "marksman": 3, "mameluk": 3, "amazonas": 3,
        "sopdu-speerkämpfer": 2, "anhur-garde": 3, "asclepion": 2,
    }
    grain_per_unit = _UNIT_GRAIN.get(troop_type.lower(), 1)

    guild = await database.get_guild(guild_id)
    perms = await database.get_member_permissions(guild_id, uid)
    can_edit_all = (
        session.get("type") == "admin"
        or (guild and guild.get("owner_discord_id") == uid)
        or "ally_manage" in perms
        or "defend_manage" in perms
        or "defend_edit" in perms
    )

    ok = await database.update_defend_sent_entry(
        sent_id, uid, amount_raw, amount_parsed, troop_type, grain_per_unit,
        can_edit_all=can_edit_all,
    )
    if ok:
        # Refresh Discord tracking embed for the affected channel
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post("http://bot:7777/api/refresh-defend-tracking",
                                  json={"channel_id": channel_id})
        except Exception:
            pass
    flash = "saved" if ok else "error"
    return RedirectResponse(f"/guild/{guild_id}/verteidigung?flash={flash}", status_code=303)


@app.get("/guild/{guild_id}/verteidigung/stats", response_class=HTMLResponse)
async def verteidigung_stats_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    err = await _require_alliance(guild, guild_id)
    if err: return err
    stats = await database.get_defend_stats(guild_id)
    return templates.TemplateResponse("verteidigung_stats.html", {
        "request": request,
        "guild": guild,
        "stats": stats,
    })


@app.post("/guild/{guild_id}/enemies/{player_name}/notes")
async def enemy_update_notes(
    request: Request, guild_id: str, player_name: str,
    notes: str = Form("")
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.update_enemy_notes(guild_id, player_name, notes)
    return RedirectResponse(
        f"/guild/{guild_id}/enemies/{player_name}?saved=1", status_code=303
    )


@app.post("/guild/{guild_id}/enemies/{player_name}/village-detail")
async def enemy_village_detail_save(request: Request, guild_id: str, player_name: str):
    """Save building/field detail for a specific village."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    import json as _json
    try:
        body = await request.json()
        coords_key = str(body.get("coords_key", "")).strip()
        detail     = body.get("detail", {})
        if not coords_key:
            return JSONResponse({"error": "missing coords_key"}, status_code=400)
        await database.save_enemy_village_detail(guild_id, player_name, coords_key, detail)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/guild/{guild_id}/enemies/{player_name}/meta")
async def enemy_update_meta(request: Request, guild_id: str, player_name: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    form = await request.form()
    danger_level = (form.get("danger_level") or "").strip()
    tags_raw     = (form.get("tags") or "").strip()
    # Normalise tags: split on comma/space, dedupe, rejoin
    tags = ",".join(t.strip() for t in re.split(r"[,\s]+", tags_raw) if t.strip())
    await database.update_enemy_meta(guild_id, player_name, danger_level, tags)
    return RedirectResponse(
        f"/guild/{guild_id}/enemies/{player_name}?flash=meta_saved", status_code=303
    )


@app.post("/guild/{guild_id}/enemies/{player_name}/delete")
async def enemy_delete(request: Request, guild_id: str, player_name: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_enemy(guild_id, player_name)
    return RedirectResponse(f"/guild/{guild_id}/enemies?saved=deleted", status_code=303)


@app.get("/guild/{guild_id}/allianz/mitglieder", response_class=HTMLResponse)
async def alliance_members_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_alliance(guild, guild_id)
    if err: return err

    members           = await database.get_alliance_members(guild_id)
    meta              = await database.get_alliance_members_meta(guild_id)
    alliance_name     = await database.get_tw_alliance_name(guild_id)
    snapshot_alliances = await database.get_alliance_names_from_snapshot(guild_id)
    player_names      = [m["player_name"] for m in members]
    strike_info       = await database.get_strike_info_for_players(guild_id, player_names)
    top_alliances     = await database.get_top_alliances_from_snapshot(guild_id)
    meta_groups       = await database.get_meta_groups(guild_id)

    # Auto-detect own alliance from snapshot: look up the current user's travian villages
    # in the latest snapshot to find which alliance they actually belong to.
    discord_id = session.get("uid", "")
    detected_alliance = None
    try:
        import aiosqlite as _asql_am
        async with _asql_am.connect(database.DB_PATH) as _db:
            _db.row_factory = _asql_am.Row
            # Get travian_name of the current user
            async with _db.execute(
                "SELECT travian_name FROM member_troops WHERE guild_id=? AND discord_id=?",
                (guild_id, discord_id)
            ) as _cur:
                _row = await _cur.fetchone()
            travian_name = (_row["travian_name"] if _row else "") or ""
            if travian_name:
                # Look up this player in the latest snapshot
                async with _db.execute("""
                    SELECT m.alliance_name FROM map_snapshots m
                    INNER JOIN (
                        SELECT guild_id, MAX(fetched_at) as max_ts FROM map_snapshots
                        WHERE guild_id=? GROUP BY guild_id
                    ) lts ON m.guild_id=lts.guild_id AND m.fetched_at=lts.max_ts
                    WHERE m.guild_id=? AND m.player_name=? LIMIT 1
                """, (guild_id, guild_id, travian_name)) as _cur:
                    _snap = await _cur.fetchone()
                if _snap:
                    detected_alliance = _snap["alliance_name"]
    except Exception:
        pass

    return templates.TemplateResponse("alliance_members.html", {
        "request": request,
        "guild": guild,
        "members": members,
        "meta": meta,
        "alliance_name": alliance_name,
        "snapshot_alliances": snapshot_alliances,
        "strike_info": strike_info,
        "top_alliances": top_alliances,
        "meta_groups": meta_groups,
        "detected_alliance": detected_alliance,
        "imported": request.query_params.get("imported"),
        "cleared": request.query_params.get("cleared"),
        "synced": request.query_params.get("synced"),
    })


@app.post("/guild/{guild_id}/settings/tw-alliance")
async def settings_set_tw_alliance(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"ok": False}, status_code=403)
    body = await request.json()
    name = (body.get("tw_alliance_name") or "").strip()
    if name:
        await database.set_tw_alliance_name(guild_id, name)
    return JSONResponse({"ok": True})


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


@app.get("/guild/{guild_id}/allianz/mitglieder/player-detail")
async def alliance_player_detail(request: Request, guild_id: str, player_name: str = ""):
    """JSON endpoint: village list + growth for a single player from map snapshots."""
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)

    import aiosqlite as _aio
    async with _aio.connect(database.DB_PATH) as db:
        db.row_factory = _aio.Row

        # Latest snapshot time
        async with db.execute(
            "SELECT MAX(fetched_at) as ts FROM map_snapshots WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            latest_ts = row["ts"] if row else None

        # First snapshot time
        async with db.execute(
            "SELECT MIN(fetched_at) as ts FROM map_snapshots WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            first_ts = row["ts"] if row else None

        villages = []
        if latest_ts:
            async with db.execute("""
                SELECT village_name, x, y, population, tribe
                FROM map_snapshots
                WHERE guild_id=? AND fetched_at=? AND player_name=?
                ORDER BY population DESC
            """, (guild_id, latest_ts, player_name)) as cur:
                villages = [dict(r) for r in await cur.fetchall()]

        # Growth: population per village between first and last snapshot
        growth = []
        if first_ts and latest_ts and first_ts != latest_ts:
            async with db.execute("""
                SELECT
                    v_late.village_name,
                    v_late.x, v_late.y,
                    v_late.population AS pop_now,
                    v_early.population AS pop_then,
                    v_late.population - COALESCE(v_early.population, 0) AS delta
                FROM map_snapshots v_late
                LEFT JOIN map_snapshots v_early
                    ON v_early.guild_id = v_late.guild_id
                    AND v_early.village_name = v_late.village_name
                    AND v_early.x = v_late.x AND v_early.y = v_late.y
                    AND v_early.fetched_at = ?
                WHERE v_late.guild_id=? AND v_late.fetched_at=? AND v_late.player_name=?
                ORDER BY delta DESC
            """, (first_ts, guild_id, latest_ts, player_name)) as cur:
                growth = [dict(r) for r in await cur.fetchall()]

        # Alliance member record
        async with db.execute("""
            SELECT points, villages, population, rank, tribe
            FROM alliance_members WHERE guild_id=? AND player_name=?
            ORDER BY rowid DESC LIMIT 1
        """, (guild_id, player_name)) as cur:
            member_row = await cur.fetchone()
            member = dict(member_row) if member_row else {}

        # Conquest detection: villages that appeared in latest but not in first snapshot
        # (new player or conquered from enemy)
        new_villages = []
        if first_ts and latest_ts and first_ts != latest_ts:
            async with db.execute("""
                SELECT v_new.village_name, v_new.x, v_new.y, v_new.population
                FROM map_snapshots v_new
                LEFT JOIN map_snapshots v_old
                    ON v_old.guild_id = v_new.guild_id
                    AND v_old.x = v_new.x AND v_old.y = v_new.y
                    AND v_old.player_name = v_new.player_name
                    AND v_old.fetched_at = ?
                WHERE v_new.guild_id=? AND v_new.fetched_at=? AND v_new.player_name=?
                  AND v_old.x IS NULL
                ORDER BY v_new.population DESC
            """, (first_ts, guild_id, latest_ts, player_name)) as cur:
                new_villages = [dict(r) for r in await cur.fetchall()]

    return JSONResponse({
        "player_name": player_name,
        "member": member,
        "villages": villages,
        "growth": growth,
        "new_villages": new_villages,
        "snapshot_count": 2 if (first_ts and latest_ts and first_ts != latest_ts) else 1,
        "first_snap": first_ts[:16].replace("T", " ") + " UTC" if first_ts else None,
        "last_snap": latest_ts[:16].replace("T", " ") + " UTC" if latest_ts else None,
    })


@app.post("/guild/{guild_id}/allianz/mitglieder/note")
async def set_member_note(
    request: Request, guild_id: str,
    player_name: str = Form(""),
    notes: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    # Sanitise
    player_name = player_name.strip()[:100]
    notes = notes.strip()[:500]
    if not player_name:
        return JSONResponse({"ok": False, "error": "missing player"}, status_code=400)
    await database.set_alliance_member_note(guild_id, player_name, notes)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/allianz/meta/create")
async def meta_create(request: Request, guild_id: str, meta_name: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    meta_name = meta_name.strip()[:64]
    if meta_name:
        result = await database.create_meta_group(guild_id, meta_name)
        if result is None:
            return RedirectResponse(
                f"/guild/{guild_id}/allianz/mitglieder?meta_error=limit", status_code=303
            )
    return RedirectResponse(f"/guild/{guild_id}/allianz/mitglieder", status_code=303)


@app.post("/guild/{guild_id}/allianz/meta/{group_id}/delete")
async def meta_delete(request: Request, guild_id: str, group_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_meta_group(guild_id, group_id)
    return RedirectResponse(f"/guild/{guild_id}/allianz/mitglieder", status_code=303)


@app.post("/guild/{guild_id}/allianz/meta/{group_id}/add-alliance")
async def meta_add_alliance(
    request: Request, guild_id: str, group_id: int, alliance_name: str = Form("")
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if alliance_name:
        await database.add_alliance_to_meta(guild_id, group_id, alliance_name)
    return RedirectResponse(f"/guild/{guild_id}/allianz/mitglieder", status_code=303)


@app.post("/guild/{guild_id}/allianz/meta/{group_id}/remove-alliance")
async def meta_remove_alliance(
    request: Request, guild_id: str, group_id: int, alliance_name: str = Form("")
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if alliance_name:
        await database.remove_alliance_from_meta(guild_id, group_id, alliance_name)
    return RedirectResponse(f"/guild/{guild_id}/allianz/mitglieder", status_code=303)


@app.get("/guild/{guild_id}/allianz/meta/{group_id}/stats")
async def meta_group_stats(request: Request, guild_id: str, group_id: int):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    stats = await database.get_meta_group_stats(guild_id, group_id)
    return JSONResponse({"stats": stats})


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


# ---------------------------------------------------------------------------
# Blueprints
# ---------------------------------------------------------------------------

TRIBE_META = {
    "alle":      {"emoji": "🌍", "label": "Alle Stämme", "color": "#94a3b8"},
    "Romans":    {"emoji": "🏛️", "label": "Römer",       "color": "#e2b96f"},
    "Teutons":   {"emoji": "⚔️",  "label": "Germanen",    "color": "#ef4444"},
    "Gauls":     {"emoji": "🌿", "label": "Gallier",     "color": "#22c55e"},
    "Egyptians": {"emoji": "🏺", "label": "Ägypter",     "color": "#f59e0b"},
    "Huns":      {"emoji": "🏹", "label": "Hunnen",      "color": "#8b5cf6"},
    "Spartans":  {"emoji": "🛡️", "label": "Spartaner",   "color": "#3b82f6"},
}


@app.get("/guild/{guild_id}/blueprints")
async def blueprints_main(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    templates_list = await database.get_blueprint_templates(guild_id)
    player_bps = await database.get_player_blueprints(guild_id)
    # Group templates by tribe
    from collections import defaultdict as _dd
    by_tribe = _dd(list)
    for t in templates_list:
        by_tribe[t["tribe"]].append(t)
    current_username = session.get("username", "") if session else ""
    return templates.TemplateResponse("blueprints.html", {
        "request": request,
        "guild": guild,
        "templates_by_tribe": dict(by_tribe),
        "player_blueprints": player_bps,
        "all_templates": templates_list,
        "tribe_meta": TRIBE_META,
        "current_username": current_username,
    })


@app.get("/guild/{guild_id}/blueprints/templates/new")
async def blueprint_template_new_form(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err
    return templates.TemplateResponse("blueprint_template_new.html", {
        "request": request,
        "guild": guild,
        "tribe_meta": TRIBE_META,
    })


@app.post("/guild/{guild_id}/blueprints/templates/new")
async def blueprint_template_new_save(
    request: Request,
    guild_id: str,
    tribe: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    if tribe not in TRIBE_META:
        return RedirectResponse(f"/guild/{guild_id}/blueprints", status_code=303)
    tid = await database.create_blueprint_template(guild_id, tribe, name.strip(), description.strip())
    return RedirectResponse(f"/guild/{guild_id}/blueprints/templates/{tid}", status_code=303)


@app.get("/guild/{guild_id}/blueprints/templates/{template_id}")
async def blueprint_template_edit(request: Request, guild_id: str, template_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err
    tmpl = await database.get_blueprint_template(template_id, guild_id)
    if not tmpl:
        return RedirectResponse(f"/guild/{guild_id}/blueprints", status_code=303)
    return templates.TemplateResponse("blueprint_template_edit.html", {
        "request": request,
        "guild": guild,
        "tmpl": tmpl,
        "tribe_meta": TRIBE_META,
    })


@app.post("/guild/{guild_id}/blueprints/templates/{template_id}/step/add")
async def blueprint_step_add(
    request: Request,
    guild_id: str,
    template_id: int,
    step_type: str = Form("building"),
    title: str = Form(...),
    target: str = Form(""),
    description: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    tmpl = await database.get_blueprint_template(template_id, guild_id)
    if not tmpl:
        return RedirectResponse(f"/guild/{guild_id}/blueprints", status_code=303)
    order_num = len(tmpl["steps"]) + 1
    await database.add_blueprint_step(
        template_id, guild_id, step_type,
        title.strip(), description.strip(), target.strip(), order_num
    )
    return RedirectResponse(f"/guild/{guild_id}/blueprints/templates/{template_id}", status_code=303)


@app.post("/guild/{guild_id}/blueprints/templates/{template_id}/step/{step_id}/delete")
async def blueprint_step_delete(request: Request, guild_id: str, template_id: int, step_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    await database.delete_blueprint_step(guild_id, step_id)
    await database.reorder_blueprint_steps(template_id)
    return RedirectResponse(f"/guild/{guild_id}/blueprints/templates/{template_id}", status_code=303)


@app.post("/guild/{guild_id}/blueprints/templates/{template_id}/delete")
async def blueprint_template_delete(request: Request, guild_id: str, template_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    await database.delete_blueprint_template(guild_id, template_id)
    return RedirectResponse(f"/guild/{guild_id}/blueprints", status_code=303)


@app.post("/guild/{guild_id}/blueprints/player/new")
async def blueprint_player_new(
    request: Request,
    guild_id: str,
    player_name: str = Form(...),
    village_name: str = Form(...),
    village_coords: str = Form(""),
    template_id: int = Form(...),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    await database.create_player_blueprint(
        guild_id, player_name.strip(), village_name.strip(),
        village_coords.strip(), template_id
    )
    return RedirectResponse(f"/guild/{guild_id}/blueprints", status_code=303)


@app.post("/guild/{guild_id}/blueprints/templates/{template_id}/activate")
async def blueprint_self_activate(
    request: Request,
    guild_id: str,
    template_id: int,
    village_name: str = Form(""),
    village_coords: str = Form(""),
):
    """Allow any logged-in user to activate a blueprint for themselves."""
    session, err = _require_session(request)
    if err: return err
    player_name = session.get("username", "Unbekannt")
    tmpl = await database.get_blueprint_template(template_id, guild_id)
    if not tmpl:
        return RedirectResponse(f"/guild/{guild_id}/blueprints", status_code=303)
    bp_id = await database.create_player_blueprint(
        guild_id, player_name, village_name.strip() or "Hauptdorf",
        village_coords.strip(), template_id
    )
    return RedirectResponse(f"/guild/{guild_id}/blueprints/player/{bp_id}", status_code=303)


@app.post("/guild/{guild_id}/blueprints/player/{blueprint_id}/delete")
async def blueprint_player_delete(request: Request, guild_id: str, blueprint_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    err = await _require_premium(guild, guild_id)
    if err: return err
    await database.delete_player_blueprint(guild_id, blueprint_id)
    return RedirectResponse(f"/guild/{guild_id}/blueprints", status_code=303)


@app.get("/guild/{guild_id}/blueprints/player/{blueprint_id}")
async def blueprint_player_detail(request: Request, guild_id: str, blueprint_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err
    bp = await database.get_player_blueprint(blueprint_id, guild_id)
    if not bp:
        return RedirectResponse(f"/guild/{guild_id}/blueprints", status_code=303)
    total = len(bp["steps"])
    done = sum(1 for s in bp["steps"] if s["completed"])
    return templates.TemplateResponse("blueprint_player.html", {
        "request": request,
        "guild": guild,
        "bp": bp,
        "total": total,
        "done": done,
        "tribe_meta": TRIBE_META,
    })


@app.post("/guild/{guild_id}/blueprints/player/{blueprint_id}/step/{step_id}/toggle")
async def blueprint_step_toggle(request: Request, guild_id: str, blueprint_id: int, step_id: int):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    new_state = await database.toggle_blueprint_step(blueprint_id, step_id)
    return JSONResponse({"completed": new_state})


# ---------------------------------------------------------------------------
# Blueprint Preset routes
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/blueprints/presets")
async def blueprint_presets_list(request: Request, guild_id: str):
    # No auth needed — static preset data
    return JSONResponse({"presets": blueprint_presets.PRESET_BLUEPRINTS})


@app.post("/guild/{guild_id}/blueprints/import-preset")
async def blueprint_import_preset(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    preset_index = int(body.get("preset_index", 0))
    presets = blueprint_presets.PRESET_BLUEPRINTS
    if preset_index < 0 or preset_index >= len(presets):
        return JSONResponse({"error": "invalid preset_index"}, status_code=400)
    preset = presets[preset_index]
    tid = await database.create_blueprint_template(
        guild_id, preset["tribe"], preset["name"], preset["description"]
    )
    for s in preset["steps"]:
        await database.add_blueprint_step(
            template_id=tid,
            guild_id=guild_id,
            step_type="task",
            title=s["title"],
            description=s.get("notes", ""),
            target=s.get("target", ""),
            order_num=s["step"],
        )
    return JSONResponse({"ok": True, "template_id": tid, "redirect": f"/guild/{guild_id}/blueprints"})


# ---------------------------------------------------------------------------
# Village Layout Blueprint routes
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/blueprints/layouts")
async def village_layouts_list(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err
    admin_layouts = await database.get_village_layouts(guild_id, is_template=True)
    player_layouts = await database.get_village_layouts(guild_id, is_template=False)
    return templates.TemplateResponse("village_layouts.html", {
        "request": request,
        "guild": guild,
        "admin_layouts": admin_layouts,
        "player_layouts": player_layouts,
        "tribe_meta": TRIBE_META,
    })


@app.get("/guild/{guild_id}/blueprints/layouts/new")
async def village_layout_new_form(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err
    return templates.TemplateResponse("village_layout_new.html", {
        "request": request,
        "guild": guild,
        "tribe_meta": TRIBE_META,
    })


@app.post("/guild/{guild_id}/blueprints/layouts/new")
async def village_layout_new_save(
    request: Request,
    guild_id: str,
    name: str = Form(""),
    tribe: str = Form(""),
    created_by: str = Form("admin"),
    is_template: int = Form(1),
    description: str = Form(""),
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
    if not name.strip():
        return RedirectResponse(f"/guild/{guild_id}/blueprints/layouts/new", status_code=303)
    lid = await database.create_village_layout(guild_id, name.strip(), tribe, created_by, is_template, description.strip())
    return RedirectResponse(f"/guild/{guild_id}/blueprints/layouts/{lid}", status_code=303)


@app.get("/guild/{guild_id}/blueprints/layouts/{layout_id}")
async def village_layout_editor(request: Request, guild_id: str, layout_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    err = await _require_premium(guild, guild_id)
    if err: return err
    layout = await database.get_village_layout(layout_id, guild_id)
    if not layout:
        return RedirectResponse(f"/guild/{guild_id}/blueprints/layouts", status_code=303)
    slots_by_num = {s["slot_num"]: s for s in layout["slots"]}
    return templates.TemplateResponse("village_layout_editor.html", {
        "request": request,
        "guild": guild,
        "layout": layout,
        "slots_by_num": slots_by_num,
        "tribe_meta": TRIBE_META,
    })


@app.post("/guild/{guild_id}/blueprints/layouts/{layout_id}/slot")
async def village_layout_set_slot(request: Request, guild_id: str, layout_id: int):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    slot_num      = int(body.get("slot_num", 0))
    zone          = body.get("zone", "")
    building_type = body.get("building_type", "")
    target_level  = int(body.get("target_level", 0))
    notes         = body.get("notes", "")
    pos_x         = float(body.get("pos_x", 50))
    pos_y         = float(body.get("pos_y", 50))
    await database.set_village_slot(
        layout_id, guild_id, slot_num, zone, building_type, target_level, notes, pos_x, pos_y
    )
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/blueprints/layouts/{layout_id}/slot/delete")
async def village_layout_delete_slot(request: Request, guild_id: str, layout_id: int):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    slot_id = int(body.get("slot_id", 0))
    await database.delete_village_slot_by_id(slot_id, layout_id, guild_id)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/blueprints/layouts/{layout_id}/slot/clear")
async def village_layout_clear_slot(request: Request, guild_id: str, layout_id: int):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    slot_num = int(body.get("slot_num", 0))
    await database.clear_village_slot(layout_id, guild_id, slot_num)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/blueprints/layouts/{layout_id}/delete")
async def village_layout_delete(request: Request, guild_id: str, layout_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_village_layout(guild_id, layout_id)
    return RedirectResponse(f"/guild/{guild_id}/blueprints/layouts", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Tools
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/tools/hero-tasks", response_class=HTMLResponse)
async def hero_tasks_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("hero_tasks.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
    })



# ── Hero Scout ────────────────────────────────────────────────────────────────

HERO_SCOUT_IMAGES_DIR = Path("/app/data/hero_scout_images")

async def _get_hero_scout_channel(guild_id: str) -> str | None:
    import aiosqlite
    db_path = Path("/app/data/scouter.db")
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT channel_id FROM hero_scout_channels WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def _get_hero_scout_entries(guild_id: str) -> list:
    import aiosqlite
    db_path = Path("/app/data/scouter.db")
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute("ALTER TABLE hero_scout_entries ADD COLUMN source TEXT DEFAULT 'screenshot'")
            await db.commit()
        except Exception:
            pass
        db.row_factory = aiosqlite.Row
        # Latest entry per player
        async with db.execute("""
            SELECT e.*, COALESCE(e.source, 'screenshot') as source,
                   GROUP_CONCAT(s.img_hash ORDER BY s.slot_index) as slot_hashes,
                   GROUP_CONCAT(s.image_path ORDER BY s.slot_index, '|||') as slot_paths
            FROM hero_scout_entries e
            LEFT JOIN hero_scout_slots s ON s.entry_id = e.id
            WHERE e.id IN (
                SELECT MAX(id) FROM hero_scout_entries
                WHERE guild_id=? GROUP BY lower(player_name)
            )
            GROUP BY e.id
            ORDER BY e.created_at DESC
        """, (guild_id,)) as cur:
            entries = [dict(r) for r in await cur.fetchall()]

        # Per-slot item names for latest entry + previous entry for change detection
        SLOT_NAMES = ['helm', 'armor', 'boots', 'weapon', 'mount', 'misc']
        for entry in entries:
            player = entry['player_name']
            entry_id = entry['id']

            # Fetch current slot items
            async with db.execute("""
                SELECT slot_name, item_name FROM hero_scout_slots WHERE entry_id=?
            """, (entry_id,)) as scur:
                cur_slots = {r['slot_name']: (r['item_name'] or '') for r in await scur.fetchall()}

            # Fetch previous entry id for same player
            async with db.execute("""
                SELECT id FROM hero_scout_entries
                WHERE guild_id=? AND lower(player_name)=lower(?) AND id < ?
                ORDER BY id DESC LIMIT 1
            """, (guild_id, player, entry_id)) as pcur:
                prev_row = await pcur.fetchone()

            changed_slots = set()
            if prev_row:
                prev_id = prev_row['id']
                async with db.execute("""
                    SELECT slot_name, item_name FROM hero_scout_slots WHERE entry_id=?
                """, (prev_id,)) as pscur:
                    prev_slots = {r['slot_name']: (r['item_name'] or '') for r in await pscur.fetchall()}
                for slot in SLOT_NAMES:
                    if cur_slots.get(slot, '') != prev_slots.get(slot, ''):
                        changed_slots.add(slot)

            entry['changed_slots'] = list(changed_slots)
            entry['changed'] = bool(changed_slots)
            # Also expose slot item names for template
            for slot in SLOT_NAMES:
                entry[f'slot_{slot}'] = cur_slots.get(slot, '')

        return entries

async def _get_hero_scout_history(guild_id: str, player_name: str) -> list:
    import aiosqlite
    db_path = Path("/app/data/scouter.db")
    async with aiosqlite.connect(db_path) as db:
        # Migration sicherstellen
        try:
            await db.execute("ALTER TABLE hero_scout_entries ADD COLUMN source TEXT DEFAULT 'screenshot'")
            await db.commit()
        except Exception:
            pass
        db.row_factory = aiosqlite.Row
        # Einträge laden
        async with db.execute("""
            SELECT e.*, COALESCE(e.source, 'screenshot') as source
            FROM hero_scout_entries e
            WHERE e.guild_id=? AND lower(e.player_name)=lower(?)
            ORDER BY e.created_at DESC
            LIMIT 40
        """, (guild_id, player_name)) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        # Für jeden Eintrag die Slot-Item-Namen laden
        for row in rows:
            entry_id = row["id"]
            async with db.execute("""
                SELECT slot_name, item_name, image_path FROM hero_scout_slots
                WHERE entry_id=? ORDER BY slot_index
            """, (entry_id,)) as scur:
                for s in await scur.fetchall():
                    row[f"slot_{s['slot_name']}"] = s["item_name"] or ""
                    row[f"img_{s['slot_name']}"] = s["image_path"] or ""

        return rows

async def _get_discord_channels(guild_id: str) -> list:
    """Fragt die Bot-API nach verfügbaren Text-Channels."""
    bot_api = os.environ.get("BOT_API_URL", "http://bot:7777")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"{bot_api}/api/list-channels", json={"guild_id": guild_id})
            if r.status_code == 200:
                return r.json().get("channels", [])
    except Exception:
        pass
    return []


@app.get("/guild/{guild_id}/defense/hero-scout", response_class=HTMLResponse)
async def hero_scout_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    uid = session.get("uid", "")
    # Hero scout is open to all approved ally members (no specific perm needed)
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err

    entries = await _get_hero_scout_entries(guild_id)
    scout_channel = await _get_hero_scout_channel(guild_id)

    return templates.TemplateResponse("hero_scout.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
        "entries": entries,
        "scout_channel": scout_channel,
        "flash": request.query_params.get("flash", ""),
    })


@app.get("/guild/{guild_id}/defense/hero-scout/{player_name}")
async def hero_scout_detail(request: Request, guild_id: str, player_name: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    uid = session.get("uid", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err

    history = await _get_hero_scout_history(guild_id, player_name)

    if "application/json" in request.headers.get("Accept", ""):
        return JSONResponse({"player_name": player_name, "history": history})

    return templates.TemplateResponse("hero_scout_detail.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
        "player_name": player_name,
        "history": history,
    })


@app.post("/guild/{guild_id}/defense/hero-scout/set-channel")
async def hero_scout_set_channel(request: Request, guild_id: str, channel_id: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    bot_api = os.environ.get("BOT_API_URL", "http://bot:7777")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"{bot_api}/api/set-hero-scout-channel",
                                  json={"guild_id": guild_id, "channel_id": channel_id})
            if r.status_code == 200:
                return RedirectResponse(
                    f"/guild/{guild_id}/defense/hero-scout?flash=channel_saved",
                    status_code=303
                )
    except Exception as e:
        pass
    return RedirectResponse(
        f"/guild/{guild_id}/defense/hero-scout?flash=channel_error",
        status_code=303
    )


@app.get("/guild/{guild_id}/defense/hero-scout/library-status")
async def hero_scout_library_status(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False, "error": "auth"})
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"ok": False, "error": "guild"})
    bot_api = os.environ.get("BOT_API_URL", "http://bot:7777")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{bot_api}/api/hero-scout-library-status")
            return JSONResponse(r.json())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/guild/{guild_id}/defense/hero-scout/build-library")
async def hero_scout_build_library(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"ok": False, "error": "auth"})
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"ok": False, "error": "guild"})
    guild = await database.get_guild(guild_id)
    world_url = guild.get("tw_world", "") if guild else ""
    if not world_url:
        return JSONResponse({"ok": False, "error": "Keine Travian-World-URL konfiguriert"})
    bot_api = os.environ.get("BOT_API_URL", "http://bot:7777")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{bot_api}/api/hero-scout-build-library",
                                  json={"world_url": world_url})
            return JSONResponse(r.json())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/guild/{guild_id}/defense/hero-scout/img/{entry_id}/{slot_name}")
async def hero_scout_slot_image(request: Request, guild_id: str, entry_id: int, slot_name: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    path = HERO_SCOUT_IMAGES_DIR / guild_id / str(entry_id) / f"{slot_name}.png"
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(str(path), media_type="image/png")


# ──────────────────────────────────────────────────────────────────────
# Manueller Held-Scout
# ──────────────────────────────────────────────────────────────────────

# Alle bekannten Hero-Items — Travian Legends, Tier 1/2/3, offizielle deutsche Namen
# Format: (item_id, "Anzeigename [T1/T2/T3]")
_HERO_ITEMS_BY_CAT = {
    "helmet": [
        # XP helmets
        ("helm_awareness_1",     "Helmet of Awareness [T1] +15% XP"),
        ("helm_awareness_2",     "Helmet of Enlightenment [T2] +20% XP"),
        ("helm_awareness_3",     "Helmet of Wisdom [T3] +25% XP"),
        # Regen helmets
        ("helm_regen_1",         "Helmet of Renewal [T1] +10 HP/day"),
        ("helm_regen_2",         "Helmet of Health [T2] +15 HP/day"),
        ("helm_regen_3",         "Helmet of Healing [T3] +20 HP/day"),
        # CP helmets
        ("helm_kp_1",            "Helmet of the Gladiator [T1] +100 CP/day"),
        ("helm_kp_2",            "Helmet of the Tribune [T2] +400 CP/day"),
        ("helm_kp_3",            "Helmet of the Consul [T3] +800 CP/day"),
        # Barracks helmets
        ("helm_barracks_1",      "Helmet of the Mercenary [T1] -10% barracks"),
        ("helm_barracks_2",      "Helmet of the Warrior [T2] -15% barracks"),
        ("helm_barracks_3",      "Helmet of the Archon [T3] -20% barracks"),
        # Stable helmets
        ("helm_stable_1",        "Helmet of the Horseman [T1] -10% stable"),
        ("helm_stable_2",        "Helmet of the Cavalry [T2] -15% stable"),
        ("helm_stable_3",        "Helmet of the Heavy Cavalry [T3] -20% stable"),
    ],
    "armor": [
        # Breastplate
        ("armor_breast_1",       "Light Breastplate [T1] +500 fighting str."),
        ("armor_breast_2",       "Breastplate [T2] +2,000 fighting str."),
        ("armor_breast_3",       "Heavy Breastplate [T3] +4,000 fighting str."),
        # Lamellar armor
        ("armor_seg_1",          "Light Lamellar Armor [T1] +500 fighting str."),
        ("armor_seg_2",          "Lamellar Armor [T2] +2,000 fighting str."),
        ("armor_seg_3",          "Heavy Lamellar Armor [T3] +4,000 fighting str."),
        # Regen armor
        ("armor_regen_1",        "Armor of Renewal [T1] +20 HP/day"),
        ("armor_regen_2",        "Armor of Health [T2] +30 HP/day"),
        ("armor_regen_3",        "Armor of Healing [T3] +40 HP/day"),
        # Scale armor
        ("armor_scale_1",        "Light Scale Armor [T1] +500 fighting str."),
        ("armor_scale_2",        "Scale Armor [T2] +2,000 fighting str."),
        ("armor_scale_3",        "Heavy Scale Armor [T3] +4,000 fighting str."),
    ],
    "boots": [
        # Spurs
        ("boots_spurs_1",        "Small Spurs [T1] +3 sq/h"),
        ("boots_spurs_2",        "Spurs [T2] +4 sq/h"),
        ("boots_spurs_3",        "Large Spurs [T3] +5 sq/h"),
        # Army speed boots
        ("boots_army_1",         "Boots of the Mercenary [T1] +25% army speed"),
        ("boots_army_2",         "Boots of the Warrior [T2] +50% army speed"),
        ("boots_army_3",         "Boots of the Archon [T3] +75% army speed"),
        # Regen boots
        ("boots_regen_1",        "Boots of Renewal [T1] +10 HP/day"),
        ("boots_regen_2",        "Boots of Health [T2] +15 HP/day"),
        ("boots_regen_3",        "Boots of Healing [T3] +20 HP/day"),
    ],
    "weapon": [
        # Romans
        ("wpn_leg_1",            "Short Sword of the Legionnaire [T1] 🏛️"),
        ("wpn_leg_2",            "Sword of the Legionnaire [T2] 🏛️"),
        ("wpn_leg_3",            "Long Sword of the Legionnaire [T3] 🏛️"),
        ("wpn_prae_1",           "Short Sword of the Praetorian [T1] 🏛️"),
        ("wpn_prae_2",           "Sword of the Praetorian [T2] 🏛️"),
        ("wpn_prae_3",           "Long Sword of the Praetorian [T3] 🏛️"),
        ("wpn_imp_1",            "Short Sword of the Imperian [T1] 🏛️"),
        ("wpn_imp_2",            "Sword of the Imperian [T2] 🏛️"),
        ("wpn_imp_3",            "Long Sword of the Imperian [T3] 🏛️"),
        ("wpn_imperatoris_1",    "Short Sword of the Imperatoris [T1] 🏛️"),
        ("wpn_imperatoris_2",    "Sword of the Imperatoris [T2] 🏛️"),
        ("wpn_imperatoris_3",    "Long Sword of the Imperatoris [T3] 🏛️"),
        ("wpn_caesaris_1",       "Light Lance of the Caesaris [T1] 🏛️"),
        ("wpn_caesaris_2",       "Lance of the Caesaris [T2] 🏛️"),
        ("wpn_caesaris_3",       "Heavy Lance of the Caesaris [T3] 🏛️"),
        # Teutons
        ("wpn_club_1",           "Club of the Clubswinger [T1] ⚒️"),
        ("wpn_club_2",           "Morning Star of the Clubswinger [T2] ⚒️"),
        ("wpn_club_3",           "Mace of the Clubswinger [T3] ⚒️"),
        ("wpn_spear_1",          "Spear of the Spearman [T1] ⚒️"),
        ("wpn_spear_2",          "Head of the Spearman [T2] ⚒️"),
        ("wpn_spear_3",          "Lance of the Spearman [T3] ⚒️"),
        ("wpn_axe_1",            "Hatchet of the Axeman [T1] ⚒️"),
        ("wpn_axe_2",            "Axe of the Axeman [T2] ⚒️"),
        ("wpn_axe_3",            "Battle Axe of the Axeman [T3] ⚒️"),
        ("wpn_paladin_1",        "Light Hammer of the Paladin [T1] ⚒️"),
        ("wpn_paladin_2",        "Hammer of the Paladin [T2] ⚒️"),
        ("wpn_paladin_3",        "Heavy Hammer of the Paladin [T3] ⚒️"),
        ("wpn_knight_1",         "Short Sword of the Teutonic Knight [T1] ⚒️"),
        ("wpn_knight_2",         "Sword of the Teutonic Knight [T2] ⚒️"),
        ("wpn_knight_3",         "Long Sword of the Teutonic Knight [T3] ⚒️"),
        # Gauls
        ("wpn_phalanx_1",        "Spear of the Phalanx [T1] 🍀"),
        ("wpn_phalanx_2",        "Pike of the Phalanx [T2] 🍀"),
        ("wpn_phalanx_3",        "Lance of the Phalanx [T3] 🍀"),
        ("wpn_sword_1",          "Short Sword of the Swordsman [T1] 🍀"),
        ("wpn_sword_2",          "Sword of the Swordsman [T2] 🍀"),
        ("wpn_sword_3",          "Long Sword of the Swordsman [T3] 🍀"),
        ("wpn_theutates_1",      "Short Bow of the Theutates [T1] 🍀"),
        ("wpn_theutates_2",      "Bow of the Theutates [T2] 🍀"),
        ("wpn_theutates_3",      "Long Bow of the Theutates [T3] 🍀"),
        ("wpn_druid_1",          "Walking Staff of the Druidrider [T1] 🍀"),
        ("wpn_druid_2",          "Staff of the Druidrider [T2] 🍀"),
        ("wpn_druid_3",          "Large Staff of the Druidrider [T3] 🍀"),
        ("wpn_haeduan_1",        "Light Lance of the Haeduan [T1] 🍀"),
        ("wpn_haeduan_2",        "Lance of the Haeduan [T2] 🍀"),
        ("wpn_haeduan_3",        "Heavy Lance of the Haeduan [T3] 🍀"),
        # Huns
        ("wpn_merc_hun_1",       "Hatchet of the Mercenary [T1] 🏹"),
        ("wpn_merc_hun_2",       "Axe of the Mercenary [T2] 🏹"),
        ("wpn_merc_hun_3",       "Battle Axe of the Mercenary [T3] 🏹"),
        ("wpn_bowman_1",         "Short Bow of the Bowman [T1] 🏹"),
        ("wpn_bowman_2",         "Bow of the Bowman [T2] 🏹"),
        ("wpn_bowman_3",         "Long Bow of the Bowman [T3] 🏹"),
        ("wpn_steppe_1",         "Spatha of the Steppe Rider [T1] 🏹"),
        ("wpn_steppe_2",         "Sword of the Steppe Rider [T2] 🏹"),
        ("wpn_steppe_3",         "Long Sword of the Steppe Rider [T3] 🏹"),
        ("wpn_marksman_1",       "Composite Bow of the Marksman [T1] 🏹"),
        ("wpn_marksman_2",       "Reinforced Bow of the Marksman [T2] 🏹"),
        ("wpn_marksman_3",       "Large Composite Bow of the Marksman [T3] 🏹"),
        ("wpn_marauder_1",       "Spatha of the Marauder [T1] 🏹"),
        ("wpn_marauder_2",       "Sword of the Marauder [T2] 🏹"),
        ("wpn_marauder_3",       "Long Sword of the Marauder [T3] 🏹"),
        # Egyptians
        ("wpn_slave_1",          "Club of the Slave Militia [T1] ☀️"),
        ("wpn_slave_2",          "Morning Star of the Slave Militia [T2] ☀️"),
        ("wpn_slave_3",          "Mace of the Slave Militia [T3] ☀️"),
        ("wpn_ash_1",            "Hatchet of the Ash Warden [T1] ☀️"),
        ("wpn_ash_2",            "Axe of the Ash Warden [T2] ☀️"),
        ("wpn_ash_3",            "Battle Axe of the Ash Warden [T3] ☀️"),
        ("wpn_khopesh_1",        "Khopesh of the Khopesh Warrior [T1] ☀️"),
        ("wpn_khopesh_2",        "Large Khopesh of the Khopesh Warrior [T2] ☀️"),
        ("wpn_khopesh_3",        "Giant Khopesh of the Khopesh Warrior [T3] ☀️"),
        ("wpn_anhur_1",          "Spear of the Anhur Guard [T1] ☀️"),
        ("wpn_anhur_2",          "Lance of the Anhur Guard [T2] ☀️"),
        ("wpn_anhur_3",          "Heavy Lance of the Anhur Guard [T3] ☀️"),
        ("wpn_resheph_1",        "Lance of the Resheph Chariot [T1] ☀️"),
        ("wpn_resheph_2",        "Heavy Lance of the Resheph Chariot [T2] ☀️"),
        ("wpn_resheph_3",        "Large Lance of the Resheph Chariot [T3] ☀️"),
    ],
    "mount": [
        ("mount_gelding",        "Gelding [T1] 14 sq/h"),
        ("mount_thoroughbred",   "Thoroughbred [T2] 17 sq/h"),
        ("mount_warhorse",       "Warhorse [T3] 20 sq/h"),
    ],
    "misc": [
        # Shields
        ("misc_shield_1",        "Small Shield [T1] +500 fighting str."),
        ("misc_shield_2",        "Shield [T2] +2,000 fighting str."),
        ("misc_shield_3",        "Large Shield [T3] +4,000 fighting str."),
        # Horn of the Natarian
        ("misc_horn_1",          "Small Horn of the Natarian [T1] +20% vs. Natars"),
        ("misc_horn_2",          "Horn of the Natarian [T2] +25% vs. Natars"),
        ("misc_horn_3",          "Large Horn of the Natarian [T3] +30% vs. Natars"),
        # Thief's bag
        ("misc_thief_1",         "Thief's Gloves [T1] +10% plunder"),
        ("misc_thief_2",         "Thief's Bag [T2] +15% plunder"),
        ("misc_thief_3",         "Thief's Sack [T3] +20% plunder"),
        # Map (return speed)
        ("misc_map_1",           "Small Map [T1] +30% return speed"),
        ("misc_map_2",           "Map [T2] +40% return speed"),
        ("misc_map_3",           "Large Map [T3] +50% return speed"),
        # Pennant (own villages)
        ("misc_pennant_1",       "Small Pennant [T1] +30% speed own villages"),
        ("misc_pennant_2",       "Pennant [T2] +40% speed own villages"),
        ("misc_pennant_3",       "Large Pennant [T3] +50% speed own villages"),
        # Banner (alliance)
        ("misc_banner_1",        "Small Banner [T1] +15% alliance speed"),
        ("misc_banner_2",        "Banner [T2] +20% alliance speed"),
        ("misc_banner_3",        "Large Banner [T3] +25% alliance speed"),
        # Consumables
        ("misc_bandage_25",      "Small Bandage (heals 25%)"),
        ("misc_bandage_33",      "Bandage (heals 33%)"),
        ("misc_ointment",        "Ointment (+1% hero health)"),
        ("misc_scroll",          "Scroll (+10 XP)"),
        ("misc_bucket",          "Bucket (revive hero instantly)"),
        ("misc_cage",            "Cage (catch animal)"),
        ("misc_artwork",         "Artwork (culture points)"),
        ("misc_tablet",          "Stone Tablet (+1% loyalty)"),
        ("misc_book",            "Book of Wisdom (reset attribute points)"),
    ],
}

_SLOT_NAMES_DE = {
    "helm": "Helm", "armor": "Rüstung", "boots": "Schuhe",
    "weapon": "Waffe", "mount": "Pferd/Reittier", "misc": "Sonstiges",
}
_CAT_TO_SLOT = {
    "helmet": "helm", "armor": "armor", "boots": "boots",
    "weapon": "weapon", "mount": "mount", "misc": "misc",
}


async def _init_manual_hero_table():
    import aiosqlite
    db_path = Path("/app/data/scouter.db")
    async with aiosqlite.connect(db_path) as db:
        # hero_scout_entries hat bereits alle nötigen Felder.
        # Wir ergänzen eine Spalte 'source' um manuelle von Screenshot-Einträgen zu unterscheiden.
        try:
            await db.execute("ALTER TABLE hero_scout_entries ADD COLUMN source TEXT DEFAULT 'screenshot'")
            await db.commit()
        except Exception:
            pass
        # item_name Spalte für hero_scout_slots (falls noch nicht vorhanden)
        try:
            await db.execute("ALTER TABLE hero_scout_slots ADD COLUMN item_name TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
        await db.commit()


@app.get("/guild/{guild_id}/defense/hero-scout/manual/new", response_class=HTMLResponse)
async def hero_scout_manual_new(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    await _init_manual_hero_table()
    flash = request.query_params.get("flash", "")
    return templates.TemplateResponse("hero_scout_manual.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
        "items_by_cat": _HERO_ITEMS_BY_CAT,
        "slot_names_de": _SLOT_NAMES_DE,
        "cat_to_slot": _CAT_TO_SLOT,
        "prefill": {},
        "flash": flash,
        "edit_player": None,
    })


@app.get("/guild/{guild_id}/defense/hero-scout/manual/{player_name}/add", response_class=HTMLResponse)
async def hero_scout_manual_add_version(request: Request, guild_id: str, player_name: str):
    """Neue Version für bestehenden Spieler anlegen — Felder vorausfüllen."""
    from urllib.parse import unquote
    player_name = unquote(player_name)
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    await _init_manual_hero_table()

    # Letzten Eintrag laden zum Vorausfüllen
    import aiosqlite
    db_path = Path("/app/data/scouter.db")
    prefill: dict = {}
    prefill_slots: dict = {}
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM hero_scout_entries
            WHERE guild_id=? AND lower(player_name)=lower(?)
            ORDER BY created_at DESC LIMIT 1
        """, (guild_id, player_name)) as cur:
            row = await cur.fetchone()
            if row:
                prefill = dict(row)
        if prefill.get("id"):
            async with db.execute("""
                SELECT slot_name, item_name FROM hero_scout_slots
                WHERE entry_id=? ORDER BY slot_index
            """, (prefill["id"],)) as cur:
                for r in await cur.fetchall():
                    prefill_slots[r["slot_name"]] = r["item_name"]
    prefill["slots"] = prefill_slots

    return templates.TemplateResponse("hero_scout_manual.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
        "items_by_cat": _HERO_ITEMS_BY_CAT,
        "slot_names_de": _SLOT_NAMES_DE,
        "cat_to_slot": _CAT_TO_SLOT,
        "prefill": prefill,
        "flash": "",
        "edit_player": player_name,
    })


@app.post("/guild/{guild_id}/defense/hero-scout/manual/save")
async def hero_scout_manual_save(
    request: Request,
    guild_id: str,
    player_name: str = Form(""),
    tribe: str = Form(""),
    alliance: str = Form(""),
    hero_level: int = Form(0),
    hero_xp: int = Form(0),
    villages: int = Form(0),
    attacker_rank: int = Form(0),
    defender_rank: int = Form(0),
    server_time: str = Form(""),
    slot_helm: str = Form(""),
    slot_armor: str = Form(""),
    slot_boots: str = Form(""),
    slot_weapon: str = Form(""),
    slot_mount: str = Form(""),
    slot_misc: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await _init_manual_hero_table()

    import aiosqlite, hashlib as _hl
    db_path = Path("/app/data/scouter.db")
    now = __import__("datetime").datetime.utcnow().isoformat()

    reporter_name = session.get("username", "?")
    reporter_id   = session.get("user_id", "")

    # Slots zusammenfassen
    slot_vals = {
        "helm": slot_helm, "armor": slot_armor, "boots": slot_boots,
        "weapon": slot_weapon, "mount": slot_mount, "misc": slot_misc,
    }
    slots_hash_str = _hl.md5("".join(slot_vals.values()).encode()).hexdigest()

    # Änderung erkennen
    changed = 0
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("""
            SELECT e.id, e.slots_hash FROM hero_scout_entries e
            WHERE guild_id=? AND lower(player_name)=lower(?)
            ORDER BY created_at DESC LIMIT 1
        """, (guild_id, player_name.strip())) as cur:
            prev = await cur.fetchone()
            if prev and prev[1] and prev[1] != slots_hash_str:
                changed = 1

    # Eintrag speichern
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("""
            INSERT INTO hero_scout_entries
                (guild_id, player_name, tribe, alliance, villages, hero_level, hero_xp,
                 attacker_rank, defender_rank, server_time, reporter_id, reporter_name,
                 discord_url, slots_hash, changed, created_at, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            guild_id, player_name.strip(), tribe.strip(), alliance.strip(),
            villages, hero_level, hero_xp, attacker_rank, defender_rank,
            server_time.strip(), reporter_id, reporter_name,
            "", slots_hash_str, changed, now, "manual",
        ))
        await db.commit()
        entry_id = cur.lastrowid

        # item_name Spalte sicherstellen
        try:
            await db.execute("ALTER TABLE hero_scout_slots ADD COLUMN item_name TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass

        for idx, (sname, item_name) in enumerate(slot_vals.items()):
            await db.execute("""
                INSERT INTO hero_scout_slots (entry_id, guild_id, slot_index, slot_name,
                    image_path, img_hash, item_name)
                VALUES (?,?,?,?,?,?,?)
            """, (entry_id, guild_id, idx, sname, "", "", item_name))
        await db.commit()

    from urllib.parse import quote
    flash_param = "changed" if changed else "saved"
    return RedirectResponse(
        f"/guild/{guild_id}/defense/hero-scout/{quote(player_name.strip())}?flash={flash_param}",
        status_code=303,
    )


# ── Held löschen (einzeln oder alle) ──────────────────────────────────────

@app.post("/guild/{guild_id}/defense/hero-scout/delete-player")
async def hero_scout_delete_player(
    request: Request,
    guild_id: str,
    player_name: str = Form(""),
):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    import aiosqlite, shutil
    db_path = Path("/app/data/scouter.db")
    async with aiosqlite.connect(db_path) as db:
        # Alle Entry-IDs holen um Bilder zu löschen
        async with db.execute(
            "SELECT id FROM hero_scout_entries WHERE guild_id=? AND lower(player_name)=lower(?)",
            (guild_id, player_name)
        ) as cur:
            ids = [r[0] for r in await cur.fetchall()]
        # Slots + Einträge löschen
        for eid in ids:
            await db.execute("DELETE FROM hero_scout_slots WHERE entry_id=?", (eid,))
            img_dir = HERO_SCOUT_IMAGES_DIR / guild_id / str(eid)
            if img_dir.exists():
                shutil.rmtree(str(img_dir), ignore_errors=True)
        await db.execute(
            "DELETE FROM hero_scout_entries WHERE guild_id=? AND lower(player_name)=lower(?)",
            (guild_id, player_name)
        )
        await db.commit()

    return RedirectResponse(
        f"/guild/{guild_id}/defense/hero-scout?flash=deleted",
        status_code=303,
    )


@app.post("/guild/{guild_id}/defense/hero-scout/delete-all")
async def hero_scout_delete_all(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err

    import aiosqlite, shutil
    db_path = Path("/app/data/scouter.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM hero_scout_slots WHERE guild_id=?", (guild_id,))
        await db.execute("DELETE FROM hero_scout_entries WHERE guild_id=?", (guild_id,))
        await db.commit()
    # Alle Bilder dieser Guild löschen
    guild_img_dir = HERO_SCOUT_IMAGES_DIR / guild_id
    if guild_img_dir.exists():
        shutil.rmtree(str(guild_img_dir), ignore_errors=True)

    return RedirectResponse(
        f"/guild/{guild_id}/defense/hero-scout?flash=deleted_all",
        status_code=303,
    )


@app.get("/guild/{guild_id}/tools/crop-calculator", response_class=HTMLResponse)
async def crop_calculator_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("crop_calculator.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
    })


@app.get("/guild/{guild_id}/tools/crop-supply", response_class=HTMLResponse)
async def crop_supply_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("crop_supply.html", {
        "request": request,
        "guild": guild,
        "guild_id": guild_id,
    })


# ---------------------------------------------------------------------------
# Routes — Grain Supply Simulations
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/tools/grain-simulations", response_class=HTMLResponse)
async def grain_simulations_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if not await has_perm(request, guild_id, "grain_sim_view"):
        return HTMLResponse("Kein Zugriff — Rolle 'grain_sim_view' erforderlich.", status_code=403)
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)
    sims = await database.get_grain_simulations(guild_id)
    is_manager = await has_perm(request, guild_id, "ally_manage")
    uid = session.get("uid", "")
    flash = request.query_params.get("flash", "")
    return templates.TemplateResponse("grain_simulations.html", {
        "request": request, "guild": guild, "guild_id": guild_id,
        "sims": sims, "is_manager": is_manager, "uid": uid, "flash": flash,
    })


@app.post("/guild/{guild_id}/tools/grain-simulations/save")
async def grain_simulation_save(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    username = session.get("username", "") or session.get("global_name", "") or uid
    form = await request.form()
    name = (form.get("name") or "").strip() or "Simulation"
    await database.save_grain_simulation(
        guild_id=guild_id,
        name=name[:80],
        village_label=(form.get("village_label") or "").strip()[:60],
        created_by_id=uid,
        created_by_name=username,
        current_grain=int(form.get("current_grain") or 0),
        net_per_h=int(form.get("net_per_h") or 0),
        granary_capacity=int(form.get("granary_capacity") or 0),
        duration_h=int(form.get("duration_h") or 48),
        marketplace_text=(form.get("marketplace_text") or ""),
        notes=(form.get("notes") or "").strip()[:500],
    )
    return RedirectResponse(f"/guild/{guild_id}/tools/grain-simulations?flash=saved", status_code=303)


@app.post("/guild/{guild_id}/tools/grain-simulations/{sim_id}/delete")
async def grain_simulation_delete(request: Request, guild_id: str, sim_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "")
    is_manager = await has_perm(request, guild_id, "ally_manage")
    deleted = await database.delete_grain_simulation(guild_id, sim_id, uid, is_manager)
    if not deleted:
        return JSONResponse({"error": "not found or no permission"}, status_code=403)
    return RedirectResponse(f"/guild/{guild_id}/tools/grain-simulations?flash=deleted", status_code=303)


@app.get("/guild/{guild_id}/tools/grain-simulations/{sim_id}", response_class=HTMLResponse)
async def grain_simulation_load(request: Request, guild_id: str, sim_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if not await has_perm(request, guild_id, "grain_sim_view"):
        return HTMLResponse("Kein Zugriff.", status_code=403)
    sim = await database.get_grain_simulation(guild_id, sim_id)
    if not sim:
        return RedirectResponse(f"/guild/{guild_id}/tools/grain-simulations", status_code=303)
    guild = await database.get_guild(guild_id)
    # Redirect to crop calculator with sim data encoded as query params
    import urllib.parse as _up
    params = _up.urlencode({
        "grain": sim["current_grain"],
        "net": sim["net_per_h"],
        "granary": sim["granary_capacity"],
        "hours": sim["duration_h"],
        "label": sim["village_label"] or "",
        "sim_id": sim_id,
        "sim_name": sim["name"],
    })
    return RedirectResponse(
        f"/guild/{guild_id}/tools/crop-calculator?load_sim=1&{params}",
        status_code=303
    )


# ---------------------------------------------------------------------------
# Routes — Travian Statistics Import & Trends
# ---------------------------------------------------------------------------

_STATS_IMPORT_COOLDOWN_MINUTES = 60


def _last_snapshot_at(snapshots: list[dict]) -> str | None:
    """Return the most recent created_at (= when the import happened) or None."""
    values = [s["created_at"] for s in snapshots if s.get("created_at")]
    return max(values) if values else None   # ISO string max works correctly


def _minutes_since(iso: str) -> float | None:
    from datetime import datetime as _dt, timezone
    try:
        t = _dt.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        now = _dt.now(timezone.utc)
        return (now - t).total_seconds() / 60
    except Exception:
        return None


@app.get("/guild/{guild_id}/travian-stats", response_class=HTMLResponse)
async def travian_stats_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    all_snapshots = await database.get_stats_snapshots(guild_id)
    player_snapshots   = [s for s in all_snapshots if s.get("stats_type","player") == "player"]
    alliance_snapshots = [s for s in all_snapshots if s.get("stats_type") == "alliance"]
    player_trends   = await database.get_stats_trend_data(guild_id, stats_type="player")
    alliance_trends = await database.get_stats_trend_data(guild_id, stats_type="alliance")

    # Cooldown info for both types
    player_last_at   = _last_snapshot_at(player_snapshots)
    alliance_last_at = _last_snapshot_at(alliance_snapshots)
    player_mins_ago   = _minutes_since(player_last_at)   if player_last_at   else None
    alliance_mins_ago = _minutes_since(alliance_last_at) if alliance_last_at else None

    return templates.TemplateResponse("travian_stats.html", {
        "request": request,
        "guild": guild,
        "player_snapshots": player_snapshots,
        "alliance_snapshots": alliance_snapshots,
        "player_trends": player_trends,
        "alliance_trends": alliance_trends,
        "flash": request.query_params.get("flash", ""),
        "error": request.query_params.get("error", ""),
        "active_tab": request.query_params.get("tab", "player"),
        "player_last_at":   player_last_at,
        "alliance_last_at": alliance_last_at,
        "player_mins_ago":   player_mins_ago,
        "alliance_mins_ago": alliance_mins_ago,
        "cooldown_minutes": _STATS_IMPORT_COOLDOWN_MINUTES,
    })


@app.post("/guild/{guild_id}/travian-stats/import")
async def travian_stats_import(request: Request, guild_id: str):
    from stats_parser import parse_travian_stats_smart
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    form = await request.form()
    raw_text    = (form.get("stats_text") or "").strip()
    snapshot_at = (form.get("snapshot_at") or "").strip()
    stats_type  = (form.get("stats_type") or "player").strip()
    imported_by = session.get("username", "")

    if not raw_text:
        return RedirectResponse(f"/guild/{guild_id}/travian-stats?error=empty&tab={stats_type}", status_code=303)

    # Cooldown check
    all_snaps = await database.get_stats_snapshots(guild_id)
    type_snaps = [s for s in all_snaps if s.get("stats_type", "player") == stats_type]
    last_at = _last_snapshot_at(type_snaps)
    if last_at:
        mins_ago = _minutes_since(last_at)
        if mins_ago is not None and mins_ago < _STATS_IMPORT_COOLDOWN_MINUTES:
            wait = int(_STATS_IMPORT_COOLDOWN_MINUTES - mins_ago)
            return RedirectResponse(
                f"/guild/{guild_id}/travian-stats?error=cooldown&wait={wait}&tab={stats_type}",
                status_code=303
            )

    if not snapshot_at:
        from datetime import datetime as _dt
        snapshot_at = _dt.utcnow().strftime("%Y-%m-%dT%H:%M")

    entries = parse_travian_stats_smart(raw_text)
    if not entries:
        return RedirectResponse(f"/guild/{guild_id}/travian-stats?error=parse&tab={stats_type}", status_code=303)

    await database.save_stats_snapshot(guild_id, imported_by, snapshot_at, raw_text, entries, stats_type)
    return RedirectResponse(
        f"/guild/{guild_id}/travian-stats?flash=imported&count={len(entries)}&tab={stats_type}", status_code=303
    )


@app.post("/guild/{guild_id}/travian-stats/snapshot/{snap_id}/delete")
async def travian_stats_delete(request: Request, guild_id: str, snap_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_stats_snapshot(snap_id, guild_id)
    return RedirectResponse(f"/guild/{guild_id}/travian-stats?flash=deleted", status_code=303)


@app.get("/guild/{guild_id}/travian-stats/api/trends")
async def travian_stats_trends_api(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "unauthorized"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)
    stats_type = request.query_params.get("type", "player")
    data = await database.get_stats_trend_data(guild_id, stats_type=stats_type)
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# Routes — Chrome Extension download
# ---------------------------------------------------------------------------

@app.get("/extension", response_class=HTMLResponse)
async def extension_page(request: Request):
    return templates.TemplateResponse("extension_download.html", {"request": request})


@app.get("/download/extension")
async def download_extension():
    """Serve the Chrome extension as a ZIP file."""
    buf = io.BytesIO()
    ext_dir = Path(__file__).parent / "static" / "extension"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in ext_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(ext_dir))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=travops-extension.zip"},
    )


# ---------------------------------------------------------------------------
# Routes — Player Intelligence (enemy player lookup)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Battle Reports
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/reports", response_class=HTMLResponse)
async def reports_page(request: Request, guild_id: str,
                       q: str = "", rtype: str = "", page: int = 1):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    reports = await database.get_battle_reports(guild_id, limit=100,
                                                player_name=q, report_type=rtype)
    import json as _json
    for r in reports:
        for jf in ("troops_sent_json", "troops_lost_json", "def_troops_json",
                   "spy_resources_json", "plunder_json"):
            try:
                r[jf.replace("_json", "")] = _json.loads(r.get(jf) or "{}")
            except Exception:
                r[jf.replace("_json", "")] = {}
        override = r.get("fake_override")
        if override == "fake":
            r["fake_confidence"] = "high"
            r["fake_reason"]     = "Manually marked as fake"
        elif override == "real":
            r["fake_confidence"] = "none"
            r["fake_reason"]     = ""
        else:
            fake = _detect_fake(r.get("troops_sent") or {})
            r["fake_confidence"] = fake["fake_confidence"]
            r["fake_reason"]     = fake["fake_reason"]

    return templates.TemplateResponse("reports.html", {
        "request": request, "guild": guild,
        "reports": reports, "q": q, "rtype": rtype,
    })


@app.post("/guild/{guild_id}/reports/submit", response_class=HTMLResponse)
async def report_submit(request: Request, guild_id: str,
                        report_text: str = Form("")):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "") or session.get("discord_id", "")

    text = report_text.strip()
    if not text:
        return RedirectResponse(f"/guild/{guild_id}/reports?error=empty", status_code=303)

    parsed = _parse_battle_report(text)

    # ── World validation ──────────────────────────────────────────────────────
    # Check that attacker / defender are known in this guild's map snapshots.
    # We warn but don't hard-block in case the snapshot is stale.
    guild = await database.get_guild(guild_id)
    warnings: list[str] = []
    att_name = parsed.get("attacker_name")
    def_name = parsed.get("defender_name")
    if att_name:
        if not await database.player_exists_in_world(guild_id, att_name):
            warnings.append(f'Attacker "{att_name}" was not found in this world\'s latest map snapshot.')
    if def_name:
        if not await database.player_exists_in_world(guild_id, def_name):
            warnings.append(f'Defender "{def_name}" was not found in this world\'s latest map snapshot.')

    if warnings:
        # Show a confirmation page — user can force-save or cancel
        import json as _json
        return templates.TemplateResponse("report_world_check.html", {
            "request": request, "guild": guild,
            "warnings": warnings,
            "parsed_preview": {
                "report_type": parsed.get("report_type"),
                "attacker_name": att_name,
                "defender_name": def_name,
                "attacker_village": parsed.get("attacker_village"),
                "defender_village": parsed.get("defender_village"),
                "plunder_total": parsed.get("plunder_total", 0),
            },
            "raw_text": text,
        })

    try:
        report_id = await database.save_battle_report(guild_id, uid, parsed)
    except ValueError as e:
        if str(e).startswith("duplicate:"):
            existing_id = str(e).split(":", 1)[1]
            return RedirectResponse(f"/guild/{guild_id}/reports/{existing_id}?duplicate=1", status_code=303)
        raise
    return RedirectResponse(f"/guild/{guild_id}/reports/{report_id}", status_code=303)


@app.post("/guild/{guild_id}/reports/submit-force", response_class=HTMLResponse)
async def report_submit_force(request: Request, guild_id: str,
                              report_text: str = Form("")):
    """Force-save a report even when world-check warnings were raised."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    uid = session.get("uid", "") or session.get("discord_id", "")
    text = report_text.strip()
    if not text:
        return RedirectResponse(f"/guild/{guild_id}/reports?error=empty", status_code=303)
    parsed = _parse_battle_report(text)
    try:
        report_id = await database.save_battle_report(guild_id, uid, parsed)
    except ValueError as e:
        if str(e).startswith("duplicate:"):
            existing_id = str(e).split(":", 1)[1]
            return RedirectResponse(f"/guild/{guild_id}/reports/{existing_id}?duplicate=1", status_code=303)
        raise
    return RedirectResponse(f"/guild/{guild_id}/reports/{report_id}", status_code=303)


@app.get("/guild/{guild_id}/reports/intel", response_class=HTMLResponse)
async def combat_intel_page(request: Request, guild_id: str, q: str = ""):
    # NOTE: must be defined BEFORE /reports/{report_id} to avoid "intel" being parsed as int
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    if q.strip():
        profile = await database.get_player_combat_profile(guild_id, q.strip())
        import json as _json
        for r in profile.get("reports", []):
            for jf in ("troops_json", "losses_json"):
                try:
                    r[jf.replace("_json", "")] = _json.loads(r.get(jf) or "{}")
                except Exception:
                    r[jf.replace("_json", "")] = {}
        return templates.TemplateResponse("combat_intel_player.html", {
            "request": request, "guild": guild,
            "profile": profile, "q": q,
        })

    overview = await database.get_combat_intel_overview(guild_id)
    return templates.TemplateResponse("combat_intel.html", {
        "request": request, "guild": guild,
        "players": overview, "q": q,
    })


@app.get("/guild/{guild_id}/reports/{report_id}", response_class=HTMLResponse)
async def report_detail(request: Request, guild_id: str, report_id: int, duplicate: int = 0):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    r = await database.get_battle_report(report_id, guild_id)
    if not r:
        return RedirectResponse(f"/guild/{guild_id}/reports", status_code=303)

    import json as _json
    for jf in ("troops_sent_json", "troops_lost_json", "troops_hospital_json",
               "def_troops_json", "def_troops_lost_json", "def_troops_hospital_json",
               "spy_resources_json", "plunder_json"):
        try:
            r[jf.replace("_json", "")] = _json.loads(r.get(jf) or "{}")
        except Exception:
            r[jf.replace("_json", "")] = {}
    try:
        r["buildings_hit"] = _json.loads(r.get("buildings_hit_json") or "[]")
    except Exception:
        r["buildings_hit"] = []
    override = r.get("fake_override")  # 'fake', 'real', or None
    if override == "fake":
        r["fake_confidence"] = "high"
        r["fake_reason"]     = "Manually marked as fake"
        r["fake_override"]   = "fake"
    elif override == "real":
        r["fake_confidence"] = "none"
        r["fake_reason"]     = ""
        r["fake_override"]   = "real"
    else:
        fake = _detect_fake(r.get("troops_sent") or {})
        r["fake_confidence"] = fake["fake_confidence"]
        r["fake_reason"]     = fake["fake_reason"]
        r["fake_override"]   = None

    return templates.TemplateResponse("report_detail.html", {
        "request": request, "guild": guild, "report": r,
        "duplicate_warning": bool(duplicate),
    })


@app.post("/guild/{guild_id}/reports/{report_id}/set-fake")
async def report_set_fake(request: Request, guild_id: str, report_id: int,
                          override: str = Form("")):
    """Toggle fake override: 'fake', 'real', or '' (auto)."""
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    val = override.strip() if override.strip() in ("fake", "real") else None
    await database.set_fake_override(report_id, guild_id, val)
    return RedirectResponse(f"/guild/{guild_id}/reports/{report_id}", status_code=303)


@app.post("/guild/{guild_id}/reports/{report_id}/delete")
async def report_delete(request: Request, guild_id: str, report_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    await database.delete_battle_report(report_id, guild_id)
    return RedirectResponse(f"/guild/{guild_id}/reports", status_code=303)


@app.get("/guild/{guild_id}/intel", response_class=HTMLResponse)
async def player_intel_page(request: Request, guild_id: str, q: str = ""):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard", status_code=303)

    intel = None
    suggestions = []
    error = ""
    no_world = False

    tw_world = guild.get("tw_world", "") if isinstance(guild, dict) else getattr(guild, "tw_world", "")

    if not tw_world:
        no_world = True
    elif q.strip():
        intel = await database.get_player_intel(guild_id, q.strip())
        if not intel:
            # Try autocomplete to give helpful suggestions
            suggestions = await database.search_players_in_snapshot(guild_id, q.strip(), limit=10)
            if not suggestions:
                error = f"No player found matching '{q}'. Make sure a map snapshot has been loaded."
    return templates.TemplateResponse("player_intel.html", {
        "request": request,
        "guild": guild,
        "q": q,
        "intel": intel,
        "suggestions": suggestions,
        "error": error,
        "no_world": no_world,
        "tw_world": (tw_world or "").rstrip("/"),
    })


@app.get("/api/world-suggestions")
async def world_suggestions(request: Request, q: str = ""):
    """Return Travian world URL suggestions based on known configured worlds + pattern matching."""
    session = _get_session(request)
    if not session:
        return JSONResponse([])
    q = q.strip().lower()

    # 1. Known worlds already configured in the platform (distinct, non-empty)
    async with __import__("aiosqlite").connect(database.DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT tw_world FROM guild_configs WHERE tw_world IS NOT NULL AND tw_world != '' ORDER BY tw_world"
        ) as cur:
            known = [r[0] for r in await cur.fetchall()]

    # Filter known worlds by query
    if q:
        known_filtered = [w for w in known if q in w.lower()]
    else:
        known_filtered = known[:8]

    # 2. Pattern-based suggestions from what the user typed
    pattern_hits = []
    if q and len(q) >= 2:
        # Try to parse partial input like "ts3", "ts3.x1", "arabics", "com.x3" etc.
        # Common Travian URL patterns:
        #   https://<server>.<speed>.<region>.travian.com
        #   https://<server>.<speed>.travian.<tld>
        #   https://ts<n>.x<n>.travian.com
        import itertools
        servers  = ["ts1","ts2","ts3","ts4","ts5","ts6","ts7","ts8","ts20","ts30","fire","fun","classic","t4","t3"]
        speeds   = ["x1","x2","x3","x5","x10"]
        regions  = ["arabics","anglo","france","germany","balkans","nordics","italy","spain","latam","poland","czech","turkey","netherlands","arabia","asia","russia","ukraine","global"]
        tlds     = ["com","de","fr","it","es","pl","cz","nl","net","org","uk"]

        # Build candidate URLs
        candidates = set()
        for s in servers:
            for sp in speeds:
                # International standard
                candidates.add(f"https://{s}.{sp}.travian.com")
                # Regional
                for reg in regions:
                    candidates.add(f"https://{s}.{sp}.{reg}.travian.com")
                for tld in tlds:
                    candidates.add(f"https://{s}.{sp}.travian.{tld}")

        pattern_hits = sorted([c for c in candidates if q in c.lower()])[:12]

    # Merge: known first (exact matches), then patterns, deduplicate
    seen = set()
    results = []
    for w in known_filtered + pattern_hits:
        w_clean = w.rstrip("/")
        if w_clean not in seen:
            seen.add(w_clean)
            results.append(w_clean)
        if len(results) >= 10:
            break

    return JSONResponse(results)


@app.post("/guild/{guild_id}/admin/set-world")
async def guild_set_world_inline(request: Request, guild_id: str):
    """Quick-setup endpoint: save tw_world URL without visiting the admin page."""
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "Not authenticated"}, status_code=401)
    err = _require_guild(session, guild_id)
    if err: return JSONResponse({"error": "Access denied"}, status_code=403)
    # Allow: platform admin, Discord guild admin, or personal workspace owner
    is_platform_admin = _get_is_admin(request)
    is_guild_admin    = session.get("guild_role") == "admin"
    is_ws_owner       = guild_id.startswith("ws_") and session.get("uid") is not None
    if not (is_platform_admin or is_guild_admin or is_ws_owner):
        return JSONResponse({"error": "Admin rights required"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    url = (body.get("tw_world") or "").strip().rstrip("/")
    if not url or not re.match(r"^https?://[a-zA-Z0-9.\-]+(:[0-9]+)?$", url):
        return JSONResponse({"error": "Ungültige URL — bitte vollständige Welt-URL angeben (z.B. https://ts3.x1.travian.com)"}, status_code=422)
    await database.update_tw_world(guild_id, url)
    # Kick off first snapshot in background so data is ready ASAP
    asyncio.create_task(_run_snapshot_background(guild_id, url))
    return JSONResponse({"ok": True, "loading": True})


@app.get("/guild/{guild_id}/intel/autocomplete")
async def player_intel_autocomplete(request: Request, guild_id: str, q: str = ""):
    session = _get_session(request)
    if not session or not (can_access_guild(session, guild_id) or await can_access_guild_async(session, guild_id)):
        return JSONResponse([])
    if len(q.strip()) < 2:
        return JSONResponse([])
    names = await database.search_players_in_snapshot(guild_id, q.strip(), limit=15)
    return JSONResponse(names)


@app.get("/guild/{guild_id}/intel/batch-summary")
async def player_intel_batch_summary(request: Request, guild_id: str, names: str = ""):
    """Return lightweight summary cards for a comma-separated list of player names."""
    session = _get_session(request)
    if not session or not (can_access_guild(session, guild_id) or await can_access_guild_async(session, guild_id)):
        return JSONResponse([])
    name_list = [n.strip() for n in names.split(",") if n.strip()][:20]
    if not name_list:
        return JSONResponse([])
    results = await database.get_player_summaries(guild_id, name_list)
    return JSONResponse(results)


# ---------------------------------------------------------------------------
# Routes — Scout Incidents (enemy scouted our members)
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/scout-incidents", response_class=HTMLResponse)
async def scout_incidents_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    enemy_filter = request.query_params.get("enemy", "").strip()
    incidents = await database.get_scout_incidents(guild_id, enemy_filter=enemy_filter)
    stats = await database.get_scout_incident_stats(guild_id)

    return templates.TemplateResponse("scout_incidents.html", {
        "request": request,
        "guild": guild,
        "incidents": incidents,
        "stats": stats,
        "enemy_filter": enemy_filter,
        "flash": request.query_params.get("flash", ""),
    })


@app.post("/guild/{guild_id}/scout-incidents/delete/{incident_id}")
async def delete_scout_incident(request: Request, guild_id: str, incident_id: int):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    async with __import__("aiosqlite").connect(database.DB_PATH) as db:
        await db.execute("DELETE FROM scout_incidents WHERE id=? AND guild_id=?", (incident_id, guild_id))
        await db.commit()
    return RedirectResponse(f"/guild/{guild_id}/scout-incidents?flash=deleted", status_code=303)


# ── Alliance Bonuses ──────────────────────────────────────────────────────────

ALLIANCE_BONUS_DEFS = [
    {"key": "recruitment",  "label": "Recruitment",  "icon": "⚔️",  "desc": "Faster troop production",     "levels": [2, 4, 6, 8, 10],  "unit": "%"},
    {"key": "philosophy",   "label": "Philosophy",   "icon": "🎓",  "desc": "Culture Points production",   "levels": [4, 8, 12, 16, 20], "unit": "%"},
    {"key": "metallurgy",   "label": "Metallurgy",   "icon": "⚒️",  "desc": "Weapons & armor strength",    "levels": [2, 4, 6, 8, 10],  "unit": "%"},
    {"key": "commerce",     "label": "Commerce",     "icon": "🏪",  "desc": "Merchant capacity",           "levels": [30, 60, 90, 120, 150], "unit": "%"},
    {"key": "bowyer",       "label": "Bowyer",       "icon": "🏹",  "desc": "Bow unit attack bonus",       "levels": [2, 4, 6, 8, 10],  "unit": "%"},
    {"key": "artisanship",  "label": "Artisanship",  "icon": "🏗️",  "desc": "Building speed bonus",        "levels": [2, 4, 6, 8, 10],  "unit": "%"},
    {"key": "healing",      "label": "Healing",      "icon": "💊",  "desc": "Hospital healing capacity",   "levels": [10, 20, 30, 40, 50], "unit": "%"},
    {"key": "scouting",     "label": "Scouting",     "icon": "🔭",  "desc": "Spy attack & defense bonus",  "levels": [10, 20, 30, 40, 50], "unit": "%"},
]


@app.get("/guild/{guild_id}/my-ally/bonuses", response_class=HTMLResponse)
async def alliance_bonuses_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if guild_id.startswith("ws_"):
        real_guild_id = await database.get_ally_membership_guild_id(session.get("discord_id", ""))
        if real_guild_id:
            return RedirectResponse(f"/guild/{real_guild_id}/my-ally/bonuses", status_code=302)
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "") or session.get("discord_id", "")
    ally_group = await database.get_ally_group_for_guild(guild_id)
    if not ally_group:
        return RedirectResponse(f"/guild/{guild_id}/my-ally")
    is_editor = (ally_group.get("owner_discord_id") == uid or await has_perm(request, guild_id, "ally_manage"))
    bonuses = await database.get_alliance_bonuses(guild_id)
    return templates.TemplateResponse("alliance_bonuses.html", {
        "request": request, "guild": guild, "ally_group": ally_group,
        "is_editor": is_editor, "bonuses": bonuses,
        "bonus_defs": ALLIANCE_BONUS_DEFS,
    })


@app.post("/guild/{guild_id}/my-ally/bonuses/save")
async def alliance_bonuses_save(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = _require_guild(session, guild_id)
    if err: return err
    if not await has_perm(request, guild_id, "ally_manage"):
        uid = session.get("uid", "") or session.get("discord_id", "")
        ally_group = await database.get_ally_group_for_guild(guild_id)
        if not ally_group or ally_group.get("owner_discord_id") != uid:
            return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    bonuses = {}
    for b in ALLIANCE_BONUS_DEFS:
        val = form.get(b["key"], "0")
        try:
            bonuses[b["key"]] = max(0, min(5, int(val)))
        except (ValueError, TypeError):
            bonuses[b["key"]] = 0
    await database.save_alliance_bonuses(guild_id, bonuses)
    return RedirectResponse(f"/guild/{guild_id}/my-ally/bonuses?saved=1", status_code=303)


# ═══════════════════════════════════════════════════════════════════════════════
# ARTIFACT SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

ARTIFACT_TYPES = [
    ("builder",      "🏗️ Baumeister"),
    ("troops",       "⚡ Schnellere Truppen"),
    ("scout",        "🔭 Bessere Späher"),
    ("crop",         "🌾 Weniger Getreideverbrauch"),
    ("training",     "🪖 Schnellerer Truppenbau"),
    ("storage_plan", "📐 Bauplan große Lager"),
    ("hideout",      "🏚️ Großes Versteck"),
    ("ww_blueprint", "🏛️ Weltwunder Bauplan"),
    ("fool",         "🤡 Narrenartefakt"),
    ("other",        "❓ Sonstiges"),
]

ARTIFACT_TYPE_LABELS = {k: v for k, v in ARTIFACT_TYPES}


def _is_leader(session: dict, guild_id: str) -> bool:
    """Quick check: is user an admin/guild owner. For artifact management."""
    uid = session.get("uid", "") or session.get("discord_id", "")
    if uid in ADMIN_DISCORD_IDS:
        return True
    for g in (session.get("guilds") or []):
        if str(g.get("id")) == guild_id:
            perms = g.get("permissions_new", 0)
            try:
                p = int(perms)
                if p & PERM_ADMINISTRATOR or p & PERM_MANAGE_GUILD:
                    return True
            except Exception:
                pass
    return False


async def _artifact_access(request: Request, guild_id: str):
    """Returns (session, None) or (None, error_response)."""
    session, err = _require_session(request)
    if err:
        return None, JSONResponse({"error": "unauthorized"}, status_code=401)
    err = await _require_guild_async(session, guild_id)
    if err:
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    guild = await database.get_guild(guild_id)
    if not guild:
        return None, JSONResponse({"error": "not_found"}, status_code=404)
    uid = session.get("uid", "") or session.get("discord_id", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err:
        return None, err
    return session, None


# ── Treasury ────────────────────────────────────────────────────────────────

@app.get("/guild/{guild_id}/my-treasury", response_class=HTMLResponse)
async def my_treasury_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "") or session.get("discord_id", "")
    treasuries = await database.get_my_treasuries(guild_id, uid)
    saved = request.query_params.get("saved")
    # Load own villages for village picker
    own_villages_raw = await database.get_own_villages(guild_id, uid)
    # Deduplicate by village_name, prefer entries with coordinates
    _seen = {}
    for v in own_villages_raw:
        name = v.get("village_name") or ""
        if not name:
            continue
        if name not in _seen or (not _seen[name].get("x") and v.get("x")):
            _seen[name] = v
    own_villages = list(_seen.values())
    # Derive player name from uploaded_by of first village
    auto_player_name = ""
    if own_villages_raw:
        auto_player_name = own_villages_raw[0].get("uploaded_by", "") or ""
    return templates.TemplateResponse("my_treasury.html", {
        "request": request, "guild": guild,
        "treasuries": treasuries, "saved": saved,
        "own_villages": own_villages,
        "auto_player_name": auto_player_name,
    })


@app.post("/guild/{guild_id}/my-treasury/save")
async def my_treasury_save(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    uid = session.get("uid", "") or session.get("discord_id", "")
    form = await request.form()
    tid = form.get("treasury_id", "").strip()
    player_name = form.get("player_name", "").strip()
    village_name = form.get("village_name", "").strip()
    notes = form.get("notes", "").strip()
    level = int(form.get("level", "10"))
    try:
        x = int(form.get("x", "0") or 0)
        y = int(form.get("y", "0") or 0)
    except (ValueError, TypeError):
        x = y = 0
    await database.save_treasury(
        guild_id, uid, player_name, village_name, x, y, level, notes,
        treasury_id=int(tid) if tid else None
    )
    return RedirectResponse(f"/guild/{guild_id}/my-treasury?saved=1", status_code=303)


@app.post("/guild/{guild_id}/my-treasury/{tid}/delete")
async def my_treasury_delete(request: Request, guild_id: str, tid: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    uid = session.get("uid", "") or session.get("discord_id", "")
    await database.delete_treasury(tid, guild_id, uid)
    return RedirectResponse(f"/guild/{guild_id}/my-treasury?saved=1", status_code=303)


@app.get("/guild/{guild_id}/all-treasuries", response_class=HTMLResponse)
async def all_treasuries_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "") or session.get("discord_id", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    treasuries = await database.get_all_treasuries(guild_id)
    is_leader = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    return templates.TemplateResponse("all_treasuries.html", {
        "request": request, "guild": guild,
        "treasuries": treasuries, "is_leader": is_leader,
    })


@app.get("/guild/{guild_id}/api/treasuries")
async def api_treasuries(request: Request, guild_id: str):
    session, err = await _artifact_access(request, guild_id)
    if err: return err
    rows = await database.get_all_treasuries(guild_id)
    return JSONResponse(rows)


@app.post("/guild/{guild_id}/all-treasuries/bulk-delete")
async def treasuries_bulk_delete(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    is_leader = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    if not is_leader:
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    body = await request.json()
    ids = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    n = await database.bulk_delete_treasuries(guild_id, ids)
    return JSONResponse({"deleted": n})


@app.post("/guild/{guild_id}/all-treasuries/bulk-set-level")
async def treasuries_bulk_set_level(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    is_leader = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    if not is_leader:
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    body = await request.json()
    ids   = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    level = int(body.get("level", 10))
    if level not in (10, 20):
        return JSONResponse({"error": "Invalid level"}, status_code=400)
    n = await database.bulk_set_treasury_level(guild_id, ids, level)
    return JSONResponse({"updated": n})


@app.post("/guild/{guild_id}/all-treasuries/seed-test")
async def treasuries_seed_test(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    is_leader = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    if not is_leader:
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    n = await database.seed_test_treasuries(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/all-treasuries?msg=test_added&n={n}", status_code=303)


@app.post("/guild/{guild_id}/all-treasuries/clear-test")
async def treasuries_clear_test(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    is_leader = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    if not is_leader:
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    n = await database.clear_test_treasuries(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/all-treasuries?msg=test_cleared&n={n}", status_code=303)


# ── Artifacts ───────────────────────────────────────────────────────────────

def _parse_world_artifacts(text: str) -> list[dict]:
    """
    Parse Travian Schatzkammer copy-paste.

    Two supported formats:
    A) With coordinates:
       Stiefel des Kriegers  Klein  Baumeister  (−44|32)   [CC] Hitamtoni
    B) Distance-only (Schatzkammer default — no coords shown):
       Stiefel des Kriegers  Klein  Baumeister  234  Nataren
       Boot of the Warrior   Small  Builder     1234 km  Nataren

    Returns list of dicts: {name, artifact_type, artifact_size, owner, x, y, distance}
    x/y are 0 when unknown; distance is None when coords are known.
    """
    import re as _re

    _TYPE_MAP = {
        "baumeister": "builder", "builder": "builder", "master builder": "builder",
        "ausbilder": "trainer", "trainer": "trainer",
        "versorger": "diet", "diet": "diet",
        "späher": "scout", "scout": "scout",
        "architect": "architect", "architekt": "architect",
        "fool": "fool", "narr": "fool",
        "storage": "storage", "lager": "storage",
        "confuser": "confuser", "verwirrer": "confuser",
        "artisan": "artisan",
        "boots": "boots", "stiefel": "boots",
        "eyes": "eyes", "augen": "eyes",
        "helmet": "helmet", "helm": "helmet",
        "horse": "horse", "pferd": "horse",
        "legion": "legion",
        "shield": "shield", "schild": "shield",
        "sword": "sword", "schwert": "sword",
    }
    _SIZE_MAP = {
        "small": "small", "klein": "small",
        "large": "large", "groß": "large", "gross": "large", "großes": "large",
        "unique": "unique", "einzigartig": "unique",
    }
    _NATAR = {"nataren", "natars", "natar"}
    # All known keywords to skip when extracting the artifact name
    _SKIP = set(_TYPE_MAP) | set(_SIZE_MAP) | {"km", "felder", "fields", "tiles"}

    coord_re    = _re.compile(r'\(?\s*([−–\-]?\d+)\s*[|│]\s*([−–\-]?\d+)\s*\)?')
    dist_re     = _re.compile(r'\b(\d{1,5})\s*(?:km|felder|fields|tiles)?\b')
    alliance_re = _re.compile(r'^\s*\[[^\]]{1,12}\]\s*')

    results: list[dict] = []
    clean = _re.sub(r'[‎‏‪-‮⁦-⁩­​‌‍]', '', text)
    lines = [l.strip() for l in clean.splitlines() if l.strip()]

    for line in lines:
        low = line.lower()

        # Skip pure header/separator lines
        if not any(c.isalpha() for c in line):
            continue
        # Skip lines with no artifact size indicator at all (likely headers)
        if not any(s in low for s in _SIZE_MAP):
            continue

        x = y = 0
        distance = None

        # ── Format A: has coordinates ──────────────────────────────────────────
        cm = coord_re.search(line)
        if cm:
            try:
                x = int(cm.group(1).replace('−', '-').replace('–', '-'))
                y = int(cm.group(2).replace('−', '-').replace('–', '-'))
            except Exception:
                continue
            before = line[:cm.start()].strip()
            after  = line[cm.end():].strip()
        # ── Format B: distance only ────────────────────────────────────────────
        else:
            # Find the distance number — typically a standalone integer (100-9999)
            # We look for a number that is NOT part of a coordinate
            dm = dist_re.search(line)
            if dm:
                distance = int(dm.group(1))
                before = line[:dm.start()].strip()
                after  = line[dm.end():].strip()
            else:
                # No coords and no distance → try to parse name/size at minimum
                before = line
                after  = ""

        # ── Owner ──────────────────────────────────────────────────────────────
        owner_raw = alliance_re.sub('', after).strip()
        owner = owner_raw if owner_raw else "Nataren"

        # ── Size ──────────────────────────────────────────────────────────────
        size = "small"
        for kw, val in _SIZE_MAP.items():
            if kw in low:
                size = val
                break

        # ── Type ──────────────────────────────────────────────────────────────
        atype = "other"
        for kw, val in _TYPE_MAP.items():
            if kw in low:
                atype = val
                break

        # ── Name: tokens before first skip-keyword ────────────────────────────
        tokens = _re.split(r'[\t]+|\s{2,}|\s*–\s*', before)
        # Fall back to splitting on any whitespace if no multi-space found
        if len(tokens) == 1:
            tokens = before.split()
        name_parts = []
        for tok in tokens:
            tl = tok.lower().rstrip('s')  # strip trailing 's' for plurals
            if tl in _SKIP or tok.lower() in _SKIP:
                break
            if tok:
                name_parts.append(tok)
        name = " ".join(name_parts).strip()
        if not name and before.split():
            name = before.split()[0]
        if not name:
            name = "Artifact"

        results.append({
            "name": name[:80],
            "artifact_type": atype,
            "artifact_size": size,
            "x": x,
            "y": y,
            "distance": distance,
            "owner": owner,
            "natar": owner_raw.lower() in _NATAR or not owner_raw,
        })

    return results


@app.post("/guild/{guild_id}/artifacts/import-world")
async def artifact_import_world(request: Request, guild_id: str,
                                artifact_text: str = Form("")):
    """Parse and import artifacts from Travian Schatzkammer copy-paste."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    entries = _parse_world_artifacts(artifact_text.strip())
    count   = await database.upsert_world_artifacts(guild_id, entries)
    return RedirectResponse(f"/guild/{guild_id}/artifacts/run-map?imported={count}", status_code=303)


@app.get("/guild/{guild_id}/artifacts/api/world-artifacts")
async def world_artifacts_api(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    data = await database.get_world_artifacts(guild_id)
    return JSONResponse(data)


@app.post("/guild/{guild_id}/artifacts/api/world-artifact/{artifact_id}/set-coords")
async def set_world_artifact_coords(request: Request, guild_id: str, artifact_id: int):
    session, err = _require_session(request)
    if err: return err
    body = await request.json()
    x, y = int(body.get("x",0)), int(body.get("y",0))
    async with __import__("aiosqlite").connect(database.DB_PATH, timeout=30) as db:
        await db.execute(
            "UPDATE artifacts SET x=?, y=?, distance=NULL, updated_at=datetime('now') WHERE id=? AND guild_id=?",
            (x, y, artifact_id, guild_id)
        )
        await db.commit()
    return JSONResponse({"ok": True})


@app.get("/guild/{guild_id}/artifacts/run-map", response_class=HTMLResponse)
async def artifact_run_map_page(request: Request, guild_id: str):
    """Artifact run planning map — who clears/destroys/picks each artifact."""
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    runs           = await database.get_artifact_runs(guild_id)
    treasuries     = await database.get_all_treasuries(guild_id)
    treasury_players = {t["player_name"] for t in treasuries}
    is_leader      = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    bg_villages    = await database.get_map_villages_for_guild(guild_id) if hasattr(database, "get_map_villages_for_guild") else []
    world_artifacts = await database.get_world_artifacts(guild_id)
    imported       = request.query_params.get("imported")
    # Home coords for distance circles
    ms = await database.get_march_settings(guild_id, session.get("user_id",""))
    home_x = ms.get("home_x", 0) if ms else 0
    home_y = ms.get("home_y", 0) if ms else 0
    # Alliance name for filter
    ally_group = await database.get_ally_group(guild_id) if hasattr(database, "get_ally_group") else None
    ally_name  = ally_group.get("ally_name","") if ally_group else ""
    return templates.TemplateResponse("artifact_run_map.html", {
        "request": request, "guild": guild, "session": session,
        "runs": runs, "treasury_players": list(treasury_players),
        "is_leader": is_leader, "artifact_types": ARTIFACT_TYPES,
        "bg_villages": bg_villages,
        "world_artifacts": world_artifacts,
        "imported": imported,
        "home_x": home_x or 0,
        "home_y": home_y or 0,
        "ally_name": ally_name or "",
    })


@app.get("/guild/{guild_id}/artifacts/api/runs")
async def artifact_runs_api(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    runs = await database.get_artifact_runs(guild_id)
    treasuries = await database.get_all_treasuries(guild_id)
    treasury_players = {t["player_name"] for t in treasuries}
    return JSONResponse({"runs": runs, "treasury_players": list(treasury_players)})


@app.post("/guild/{guild_id}/artifacts/api/runs/add")
async def artifact_run_add(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    data = await request.json()
    data["guild_id"] = guild_id
    run_id = await database.save_artifact_run(guild_id, data)
    return JSONResponse({"id": run_id})


@app.post("/guild/{guild_id}/artifacts/api/runs/{run_id}/delete")
async def artifact_run_delete(request: Request, guild_id: str, run_id: int):
    session, err = _require_session(request)
    if err: return err
    await database.delete_artifact_run(guild_id, run_id)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/artifacts/api/runs/seed-test")
async def artifact_run_seed_test(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    TEST_RUNS = [
        {"artifact_name":"Boot of Warrior","artifact_type":"boots","artifact_size":"small","artifact_x":-20,"artifact_y":30,
         "cleaner_name":"Leonidas","cleaner_x":-35,"cleaner_y":45,
         "destroyer_name":"Maximus","destroyer_x":-18,"destroyer_y":28,
         "picker_name":"Achilles","picker_x":-22,"picker_y":32,"picker_has_treasury":True,"is_test":True},
        {"artifact_name":"Eye of the Falcon","artifact_type":"eye","artifact_size":"large","artifact_x":55,"artifact_y":-10,
         "cleaner_name":"Spartacus","cleaner_x":70,"cleaner_y":-5,
         "destroyer_name":"Brutus","destroyer_x":53,"destroyer_y":-12,
         "picker_name":"Julius","picker_x":58,"picker_y":-8,"picker_has_treasury":False,"is_test":True},
        {"artifact_name":"Helmet of Confusion","artifact_type":"helmet","artifact_size":"unique","artifact_x":5,"artifact_y":80,
         "cleaner_name":"Ragnar","cleaner_x":-10,"cleaner_y":95,
         "destroyer_name":"Sigurd","destroyer_x":3,"destroyer_y":78,
         "picker_name":"Ragnar","picker_x":-10,"picker_y":95,"picker_has_treasury":True,"is_test":True},
        {"artifact_name":"Book of Wisdom","artifact_type":"book","artifact_size":"small","artifact_x":-70,"artifact_y":-45,
         "cleaner_name":"Caesar","cleaner_x":-85,"cleaner_y":-30,
         "destroyer_name":"Augustus","destroyer_x":-72,"destroyer_y":-47,
         "picker_name":"Pompey","picker_x":-68,"picker_y":-50,"picker_has_treasury":True,"is_test":True},
        {"artifact_name":"Architect Plans","artifact_type":"architect","artifact_size":"small","artifact_x":40,"artifact_y":60,
         "cleaner_name":"Vercingetorix","cleaner_x":55,"cleaner_y":75,
         "destroyer_name":"Ambiorix","destroyer_x":42,"destroyer_y":62,
         "picker_name":"Dumnorix","picker_x":38,"picker_y":58,"picker_has_treasury":False,"is_test":True},
    ]
    for run in TEST_RUNS:
        run["guild_id"] = guild_id
        await database.save_artifact_run(guild_id, run)
    return JSONResponse({"ok": True, "count": len(TEST_RUNS)})


@app.post("/guild/{guild_id}/artifacts/api/runs/clear-test")
async def artifact_run_clear_test(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    await database.delete_test_artifact_runs(guild_id)
    return JSONResponse({"ok": True})


@app.get("/guild/{guild_id}/artifacts/planning", response_class=HTMLResponse)
async def artifact_planning_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "") or session.get("discord_id", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    is_leader = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    settings = await database.get_artifact_plan_settings(guild_id)
    from artifact_spawns import ARTIFACT_TYPE_LABELS as SPAWN_TYPE_LABELS
    guild_cfg = await database.get_guild(guild_id)
    tw_world = guild_cfg.get("tw_world", "") if guild_cfg else ""
    world_speed = 1
    try:
        import re as _re
        m = _re.search(r'x(\d+)', tw_world or "")
        if m: world_speed = int(m.group(1))
    except Exception:
        pass
    return templates.TemplateResponse("artifact_planning.html", {
        "request": request, "guild": guild,
        "is_leader": is_leader, "settings": settings,
        "spawn_type_labels": SPAWN_TYPE_LABELS,
        "world_speed": world_speed,
        "tw_world": tw_world,
    })


@app.post("/guild/{guild_id}/artifacts/planning/settings")
async def artifact_planning_save_settings(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    import json as _j
    types = form.getlist("artifact_types")
    sizes = form.getlist("artifact_sizes")
    await database.save_artifact_plan_settings(guild_id, {
        "sector_x1": int(form.get("sector_x1", -200)),
        "sector_y1": int(form.get("sector_y1", -200)),
        "sector_x2": int(form.get("sector_x2", 200)),
        "sector_y2": int(form.get("sector_y2", 200)),
        "artifact_types": types,
        "artifact_sizes": sizes,
        "troop_speed": float(form.get("troop_speed", 7.0)),
    })
    return RedirectResponse(f"/guild/{guild_id}/artifacts/planning?saved=1", status_code=303)


@app.get("/guild/{guild_id}/artifacts/planning/analyze")
async def artifact_planning_analyze(request: Request, guild_id: str):
    """Return full artifact planning analysis as JSON."""
    import math, json as _j
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "auth"}, status_code=401)
    err = await _require_guild_async(session, guild_id)
    if err: return JSONResponse({"error": "forbidden"}, status_code=403)

    settings = await database.get_artifact_plan_settings(guild_id)
    spawns = await database.get_artifact_plan_spawns(guild_id)

    # Allow query params to override saved settings (so UI changes take effect without saving)
    qp = request.query_params
    x1 = int(qp.get("x1", settings["sector_x1"]))
    y1 = int(qp.get("y1", settings["sector_y1"]))
    x2 = int(qp.get("x2", settings["sector_x2"]))
    y2 = int(qp.get("y2", settings["sector_y2"]))
    sel_types_raw = qp.get("types", None)
    sel_types = sel_types_raw.split(",") if sel_types_raw else (settings.get("artifact_types") or [])
    sel_sizes_raw = qp.get("sizes", None)
    sel_sizes = sel_sizes_raw.split(",") if sel_sizes_raw else (settings.get("artifact_sizes") or ["unique", "great", "slight"])
    troop_speed = float(qp.get("troop_speed", settings.get("troop_speed") or 7.0))

    # World speed
    guild_cfg = await database.get_guild(guild_id)
    tw_world = guild_cfg.get("tw_world", "") if guild_cfg else ""
    world_speed = 1
    try:
        import re as _re2
        m2 = _re2.search(r'x(\d+)', tw_world or "")
        if m2: world_speed = int(m2.group(1))
    except Exception:
        pass

    if sel_types:
        spawns = [s for s in spawns if s["type"] in sel_types]
    if sel_sizes:
        spawns = [s for s in spawns if s["size"] in sel_sizes]
    # Filter by sector bounds (use min/max so order doesn't matter)
    sx1, sx2 = min(x1, x2), max(x1, x2)
    sy1, sy2 = min(y1, y2), max(y1, y2)
    spawns = [s for s in spawns if sx1 <= s["x"] <= sx2 and sy1 <= s["y"] <= sy2]

    # Load all member troops via ally_members → discord_id → latest troop data (any guild)
    member_troops = await database.get_all_member_troops_for_guild(guild_id)
    troops_by_id = {m["discord_id"]: m for m in member_troops}

    # TS levels
    ts_by_player: dict[str, dict] = {}
    for m in member_troops:
        did = m["discord_id"]
        ts_levels = await database.get_village_ts_levels(did)
        ts_by_player[did] = ts_levels

    def calc_travel(dist: float, speed_tiles_hr: float, ts: int, world_spd: int) -> float:
        """Returns travel time in hours."""
        if speed_tiles_hr <= 0:
            return 9999
        effective_speed = speed_tiles_hr * world_spd * (1 + ts * 0.20)
        return dist / effective_speed

    def dist(x1, y1, x2, y2):
        return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

    results = []
    for spawn in spawns:
        sx, sy = spawn["x"], spawn["y"]
        member_ranking = []
        for m in member_troops:
            did = m["discord_id"]
            villages = m.get("villages") or []
            if not villages:
                continue
            ts_map = ts_by_player.get(did, {})
            # Find closest village
            best_village = None
            best_time = 9999
            for v in villages:
                vx, vy = v.get("x"), v.get("y")
                if vx is None or vy is None:
                    continue
                d = dist(vx, vy, sx, sy)
                ts_key = f"{vx}_{vy}"
                ts_lvl = ts_map.get(ts_key, 0)
                t = calc_travel(d, troop_speed, ts_lvl, world_speed)
                if t < best_time:
                    best_time = t
                    best_village = {
                        "village_name": v.get("name", v.get("village_name", "?")),
                        "x": vx, "y": vy,
                        "distance": round(d, 1),
                        "travel_hours": round(t, 2),
                        "ts": ts_lvl,
                    }
            if best_village:
                member_ranking.append({
                    "discord_name": m.get("discord_name", "?"),
                    "travian_name": m.get("travian_name", "?"),
                    "total_off": m.get("total_off", 0),
                    "best_village": best_village,
                })
        # Sort by travel time
        member_ranking.sort(key=lambda r: r["best_village"]["travel_hours"])

        # Kata Recommendation A: best existing village (score: off_strength / travel_time²)
        kata_existing = None
        if member_ranking:
            def kata_score(r):
                t = r["best_village"]["travel_hours"]
                off = r["total_off"] or 1
                return off / max(t, 0.1) ** 2
            best = max(member_ranking, key=kata_score)
            kata_existing = {
                "player": best["travian_name"],
                "village": best["best_village"]["village_name"],
                "x": best["best_village"]["x"],
                "y": best["best_village"]["y"],
                "travel_hours": best["best_village"]["travel_hours"],
                "off": best["total_off"],
                "distance": best["best_village"]["distance"],
            }

        # Kata Recommendation B: optimal new village position
        # Geometric midpoint between member centroid (weighted by off) and artifact spawn
        if member_ranking:
            total_off = sum(r["total_off"] or 1 for r in member_ranking)
            cx = sum(r["best_village"]["x"] * (r["total_off"] or 1) for r in member_ranking) / total_off
            cy = sum(r["best_village"]["y"] * (r["total_off"] or 1) for r in member_ranking) / total_off
            # Midpoint biased 70% toward artifact
            opt_x = round(cx * 0.3 + sx * 0.7)
            opt_y = round(cy * 0.3 + sy * 0.7)
            opt_dist = dist(opt_x, opt_y, sx, sy)
            opt_time = calc_travel(opt_dist, troop_speed, 0, world_speed)
            kata_optimal = {
                "x": opt_x, "y": opt_y,
                "distance_to_artifact": round(opt_dist, 1),
                "travel_hours": round(opt_time, 2),
            }
        else:
            kata_optimal = None

        results.append({
            "spawn": spawn,
            "member_count": len(member_ranking),
            "members": member_ranking[:20],  # top 20
            "kata_existing": kata_existing,
            "kata_optimal": kata_optimal,
        })

    # Sort results: unique first, then great, then slight; within same size by type
    size_order = {"unique": 0, "great": 1, "slight": 2}
    results.sort(key=lambda r: (size_order.get(r["spawn"]["size"], 9), r["spawn"]["type"]))

    return JSONResponse({
        "settings": {**settings, "world_speed": world_speed, "troop_speed": troop_speed},
        "results": results,
        "member_count": len(member_troops),
    })


@app.post("/guild/{guild_id}/artifacts/planning/spawn/{spawn_key}")
async def artifact_planning_update_spawn(request: Request, guild_id: str, spawn_key: str):
    session, err = _require_session(request)
    if err: return JSONResponse({"error": "auth"}, status_code=401)
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    x = int(form.get("x", 0))
    y = int(form.get("y", 0))
    await database.update_artifact_plan_spawn(guild_id, spawn_key, x, y)
    return JSONResponse({"ok": True})


@app.get("/guild/{guild_id}/artifacts", response_class=HTMLResponse)
async def artifacts_page(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "") or session.get("discord_id", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    artifacts = await database.get_artifacts(guild_id)
    is_leader = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    saved = request.query_params.get("saved")
    return templates.TemplateResponse("artifacts.html", {
        "request": request, "guild": guild,
        "artifacts": artifacts, "is_leader": is_leader,
        "artifact_types": ARTIFACT_TYPES,
        "artifact_type_labels": ARTIFACT_TYPE_LABELS,
        "saved": saved,
    })


@app.get("/guild/{guild_id}/artifacts/api/village-lookup")
async def artifact_village_lookup(request: Request, guild_id: str, x: int = 0, y: int = 0):
    """Return village/player info for given coords from latest map snapshot."""
    session, err = _require_session(request)
    if err: return err
    result = await database.get_village_by_coords(guild_id, x, y)
    if not result:
        return JSONResponse({"found": False})
    return JSONResponse({"found": True, **result})


@app.post("/guild/{guild_id}/artifacts/add")
async def artifact_add(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    aid = await database.create_artifact(
        guild_id=guild_id,
        name=form.get("name","").strip(),
        artifact_type=form.get("artifact_type","other"),
        artifact_size=form.get("artifact_size","small"),
        effect_scope=form.get("effect_scope","village"),
        conquered_at=form.get("conquered_at","").strip(),
        activated_at=form.get("activated_at","").strip(),
        current_holder=form.get("current_holder","").strip(),
        current_village=form.get("current_village","").strip(),
        notes=form.get("notes","").strip(),
        x=int(form.get("x") or 0),
        y=int(form.get("y") or 0),
    )
    return RedirectResponse(f"/guild/{guild_id}/artifacts/{aid}?saved=1", status_code=303)


def _compute_artifact_stats(artifact: dict, handoffs: list[dict]) -> dict:
    """Compute per-player and overall statistics from handoff history."""
    from datetime import datetime as _dt, timezone as _tz
    import math

    def _parse_dt(s: str):
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
            try:
                return _dt.strptime(s, fmt)
            except Exception:
                pass
        return None

    now = _dt.utcnow()

    # Only include confirmed + skipped handoffs for history stats
    confirmed = [h for h in handoffs if h.get("status") == "confirmed"]
    pending   = [h for h in handoffs if h.get("status") == "pending"]

    # ── Per-player stats ─────────────────────────────────────────────────────
    # Build timeline: list of (player, received_at, gave_away_at)
    # Each "confirmed" handoff: to_player received at confirmed_to_time (or picked_up_at)
    # They gave it away when the NEXT confirmed handoff's from_player confirmed
    player_stats: dict[str, dict] = {}

    def _get_or_create(name: str) -> dict:
        if name not in player_stats:
            player_stats[name] = {
                "player": name,
                "times_held": 0,
                "total_hours": 0.0,
                "on_time": 0,
                "late": 0,
                "avg_delay_h": 0.0,
                "delays": [],
            }
        return player_stats[name]

    for i, h in enumerate(confirmed):
        to_p = h.get("to_player", "")
        if not to_p:
            continue
        ps = _get_or_create(to_p)
        ps["times_held"] += 1

        # When did they receive it?
        recv_dt = _parse_dt(h.get("confirmed_to_time") or h.get("picked_up_at") or "")
        # When did they give it away? (next confirmed handoff where from_player == to_p)
        gave_dt = None
        for j in range(i + 1, len(handoffs)):
            nxt = handoffs[j]
            if nxt.get("from_player") == to_p and nxt.get("status") in ("confirmed", "pending"):
                gave_dt = _parse_dt(nxt.get("confirmed_from_time") or nxt.get("picked_up_at") or "")
                if gave_dt is None and nxt.get("status") == "pending":
                    gave_dt = now  # still holding
                break

        if recv_dt:
            end = gave_dt or now
            hold_h = max(0.0, (end - recv_dt).total_seconds() / 3600)
            ps["total_hours"] += hold_h

        # On-time check: was it handed off before or after scheduled_at?
        sched = _parse_dt(h.get("scheduled_at") or "")
        actual = _parse_dt(h.get("confirmed_from_time") or h.get("picked_up_at") or "")
        from_p = h.get("from_player", "")
        if from_p and sched and actual:
            fps = _get_or_create(from_p)
            delay_h = (actual - sched).total_seconds() / 3600
            fps["delays"].append(delay_h)
            if delay_h <= 1.0:
                fps["on_time"] += 1
            else:
                fps["late"] += 1

    # ── Current holder duration ───────────────────────────────────────────────
    current_holder = artifact.get("current_holder", "")
    current_since_dt = None
    # Find the most recent confirmed handoff to current_holder
    for h in reversed(confirmed):
        if h.get("to_player") == current_holder:
            current_since_dt = _parse_dt(h.get("confirmed_to_time") or h.get("picked_up_at") or "")
            break
    current_hold_h = None
    if current_since_dt and current_holder:
        current_hold_h = round((now - current_since_dt).total_seconds() / 3600, 1)

    # Finalize per-player averages
    for ps in player_stats.values():
        if ps["times_held"] > 0:
            ps["avg_hold_h"] = round(ps["total_hours"] / ps["times_held"], 1)
        else:
            ps["avg_hold_h"] = 0.0
        ps["total_hours"] = round(ps["total_hours"], 1)
        handoff_count = ps["on_time"] + ps["late"]
        ps["reliability_pct"] = round(ps["on_time"] / handoff_count * 100) if handoff_count else None
        ps["avg_delay_h"] = round(sum(ps["delays"]) / len(ps["delays"]), 1) if ps["delays"] else None
        del ps["delays"]

    # Sort by times held desc
    players_list = sorted(player_stats.values(), key=lambda x: x["times_held"], reverse=True)

    # ── Timeline segments for Gantt chart ────────────────────────────────────
    # Each segment: {player, start_label, end_label, hours, pct, is_current, late}
    timeline_segments = []
    for i, h in enumerate(confirmed):
        to_p = h.get("to_player", "")
        if not to_p:
            continue
        recv_dt = _parse_dt(h.get("confirmed_to_time") or h.get("picked_up_at") or "")
        gave_dt = None
        is_current = False
        for j in range(i + 1, len(handoffs)):
            nxt = handoffs[j]
            if nxt.get("from_player") == to_p and nxt.get("status") in ("confirmed", "pending"):
                gave_dt = _parse_dt(nxt.get("confirmed_from_time") or nxt.get("picked_up_at") or "")
                if gave_dt is None and nxt.get("status") == "pending":
                    gave_dt = now
                    is_current = (to_p == current_holder)
                break
        if gave_dt is None:
            gave_dt = now
            is_current = (to_p == current_holder)

        if recv_dt:
            hold_h = max(0.1, (gave_dt - recv_dt).total_seconds() / 3600)
            # Check if this handoff was late (scheduled vs actual)
            sched = _parse_dt(h.get("scheduled_at") or "")
            late = False
            if sched and recv_dt:
                late = (recv_dt - sched).total_seconds() / 3600 > 1.0
            timeline_segments.append({
                "player": to_p,
                "start_label": recv_dt.strftime("%d.%m %H:%M"),
                "end_label": gave_dt.strftime("%d.%m %H:%M") if not is_current else "jetzt",
                "hours": round(hold_h, 1),
                "is_current": is_current,
                "late": late,
                "recv_ts": recv_dt.timestamp(),
                "gave_ts": gave_dt.timestamp(),
            })

    # Compute percentages relative to total span
    if timeline_segments:
        total_span = sum(s["hours"] for s in timeline_segments)
        for s in timeline_segments:
            s["pct"] = round(s["hours"] / total_span * 100, 1) if total_span else 0

    # Assign consistent colors per player (cycle through palette)
    _palette = [
        "#4ade80","#60a5fa","#f472b6","#fb923c","#a78bfa",
        "#34d399","#fbbf24","#e879f9","#38bdf8","#f87171",
    ]
    player_colors: dict[str, str] = {}
    _ci = 0
    for s in timeline_segments:
        p = s["player"]
        if p not in player_colors:
            player_colors[p] = _palette[_ci % len(_palette)]
            _ci += 1
        s["color"] = player_colors[p]

    return {
        "players": players_list,
        "total_confirmed": len(confirmed),
        "total_pending": len(pending),
        "total_skipped": len([h for h in handoffs if h.get("status") == "skipped"]),
        "current_holder": current_holder,
        "current_hold_h": current_hold_h,
        "timeline": timeline_segments,
        "player_colors": player_colors,
    }


@app.get("/guild/{guild_id}/artifacts/{artifact_id}", response_class=HTMLResponse)
async def artifact_detail_page(request: Request, guild_id: str, artifact_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    guild = await database.get_guild(guild_id)
    if not guild: return RedirectResponse("/dashboard")
    uid = session.get("uid", "") or session.get("discord_id", "")
    err = await _require_ally_or_plan(guild, guild_id, uid, redirect_path=str(request.url.path))
    if err: return err
    artifact = await database.get_artifact(artifact_id, guild_id)
    if not artifact: return RedirectResponse(f"/guild/{guild_id}/artifacts")
    rotation = await database.get_rotation(artifact_id, guild_id)
    handoffs = await database.get_handoffs(artifact_id, guild_id)
    is_leader = _is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")
    player_name = session.get("player_name", session.get("username", ""))
    saved = request.query_params.get("saved")
    stats = _compute_artifact_stats(artifact, handoffs)
    return templates.TemplateResponse("artifact_detail.html", {
        "request": request, "guild": guild,
        "artifact": artifact, "rotation": rotation,
        "handoffs": handoffs, "is_leader": is_leader,
        "artifact_types": ARTIFACT_TYPES,
        "artifact_type_labels": ARTIFACT_TYPE_LABELS,
        "player_name": player_name,
        "saved": saved,
        "stats": stats,
    })


@app.post("/guild/{guild_id}/artifacts/{artifact_id}/update")
async def artifact_update(request: Request, guild_id: str, artifact_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    await database.update_artifact(artifact_id, guild_id,
        name=form.get("name","").strip(),
        artifact_type=form.get("artifact_type","other"),
        artifact_size=form.get("artifact_size","small"),
        effect_scope=form.get("effect_scope","village"),
        conquered_at=form.get("conquered_at","").strip(),
        activated_at=form.get("activated_at","").strip(),
        activation_override=form.get("activation_override","").strip(),
        current_holder=form.get("current_holder","").strip(),
        current_village=form.get("current_village","").strip(),
        status=form.get("status","active"),
        notes=form.get("notes","").strip(),
        x=int(form.get("x") or 0),
        y=int(form.get("y") or 0),
    )
    return RedirectResponse(f"/guild/{guild_id}/artifacts/{artifact_id}?saved=1", status_code=303)


@app.post("/guild/{guild_id}/artifacts/{artifact_id}/delete")
async def artifact_delete(request: Request, guild_id: str, artifact_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.delete_artifact(artifact_id, guild_id)
    return RedirectResponse(f"/guild/{guild_id}/artifacts?saved=1", status_code=303)


@app.post("/guild/{guild_id}/artifacts/bulk-delete")
async def artifacts_bulk_delete(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    ids = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    n = 0
    for aid in ids:
        await database.delete_artifact(aid, guild_id)
        n += 1
    return JSONResponse({"deleted": n})


@app.post("/guild/{guild_id}/artifacts/bulk-set-status")
async def artifacts_bulk_set_status(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    ids    = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    status = body.get("status", "active")
    if status not in ("active", "inactive", "lost"):
        return JSONResponse({"error": "invalid status"}, status_code=400)
    if not ids: return JSONResponse({"updated": 0})
    ph = ",".join("?" * len(ids))
    import aiosqlite as _aio
    async with _aio.connect(database.DB_PATH, timeout=30) as db:
        cur = await db.execute(
            f"UPDATE artifacts SET status=?,updated_at=datetime('now') WHERE guild_id=? AND id IN ({ph})",
            [status, guild_id] + ids
        )
        await db.commit()
    return JSONResponse({"updated": cur.rowcount})


@app.post("/guild/{guild_id}/artifacts/bulk-set-size")
async def artifacts_bulk_set_size(request: Request, guild_id: str):
    session, err = _require_session(request)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    ids  = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    size = body.get("size", "small")
    if size not in ("small", "large", "unique"):
        return JSONResponse({"error": "invalid size"}, status_code=400)
    if not ids: return JSONResponse({"updated": 0})
    ph = ",".join("?" * len(ids))
    import aiosqlite as _aio
    async with _aio.connect(database.DB_PATH, timeout=30) as db:
        cur = await db.execute(
            f"UPDATE artifacts SET artifact_size=?,updated_at=datetime('now') WHERE guild_id=? AND id IN ({ph})",
            [size, guild_id] + ids
        )
        await db.commit()
    return JSONResponse({"updated": cur.rowcount})


@app.post("/guild/{guild_id}/artifacts/{artifact_id}/rotation/save")
async def artifact_rotation_save(request: Request, guild_id: str, artifact_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    import json as _json
    # Expect JSON body with players array
    body = await request.body()
    try:
        data = _json.loads(body)
        players = data.get("players", [])
    except Exception:
        players = []
        names = form.getlist("player_name")
        hours = form.getlist("hold_hours")
        notify = form.getlist("notify_hours")
        for i, n in enumerate(names):
            if n.strip():
                players.append({
                    "player_name": n.strip(),
                    "hold_hours": int(hours[i]) if i < len(hours) else 48,
                    "notify_hours": int(notify[i]) if i < len(notify) else 6,
                })
    await database.save_rotation(artifact_id, guild_id, players)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/artifacts/{artifact_id}/handoff/create")
async def artifact_handoff_create(request: Request, guild_id: str, artifact_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    form = await request.form()
    hid = await database.create_handoff(
        artifact_id=artifact_id,
        guild_id=guild_id,
        from_player=form.get("from_player","").strip(),
        to_player=form.get("to_player","").strip(),
        scheduled_at=form.get("scheduled_at","").strip(),
        notes=form.get("notes","").strip(),
    )
    return JSONResponse({"ok": True, "id": hid})


@app.post("/guild/{guild_id}/artifacts/{artifact_id}/handoff/{handoff_id}/confirm")
async def artifact_handoff_confirm(request: Request, guild_id: str,
                                    artifact_id: int, handoff_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    form = await request.form()
    side = form.get("side", "to")
    server_time = form.get("server_time", "").strip()
    ok = await database.confirm_handoff(handoff_id, artifact_id, guild_id, side, server_time)
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.post("/guild/{guild_id}/artifacts/{artifact_id}/handoff/{handoff_id}/skip")
async def artifact_handoff_skip(request: Request, guild_id: str,
                                 artifact_id: int, handoff_id: int):
    session, err = _require_session(request)
    if err: return err
    err = await _require_guild_async(session, guild_id)
    if err: return err
    if not (_is_leader(session, guild_id) or await has_perm(request, guild_id, "ally_manage")):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    await database.skip_handoff(handoff_id, artifact_id, guild_id)
    return JSONResponse({"ok": True})
