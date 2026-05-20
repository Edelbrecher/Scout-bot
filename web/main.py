import os
import asyncio
import base64
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

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


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
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
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if await database.verify_password(username, password):
        token = create_session({"type": "admin", "uid": username, "username": username, "guilds": None})
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return response
    return RedirectResponse("/login?error=Invalid+credentials", status_code=303)


@app.get("/auth/discord")
async def auth_discord():
    client_id = get_client_id()
    if not client_id or not DISCORD_CLIENT_SECRET:
        return RedirectResponse("/login?error=Discord+OAuth2+not+configured")
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
    })
    return RedirectResponse(f"https://discord.com/api/oauth2/authorize?{params}")


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/login?error=Discord+authentication+cancelled")

    client_id = get_client_id()

    async with httpx.AsyncClient() as client:
        # Exchange code for access token
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
            return RedirectResponse("/login?error=Token+exchange+failed")
        access_token = r.json()["access_token"]

        # Fetch Discord user
        user_r = await client.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user = user_r.json()

        # Fetch user's guilds
        guilds_r = await client.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_guilds = guilds_r.json() if guilds_r.status_code == 200 else []

    # Intersect with guilds where the bot is present
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
    session = get_session(request)
    if not session:
        return RedirectResponse("/login")
    if not can_access_guild(session, guild_id):
        return RedirectResponse("/dashboard")
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    scout_channels = await database.get_scout_channels(guild_id)

    token = os.environ.get("DISCORD_TOKEN", "")
    roles = []
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/roles",
            headers={"Authorization": f"Bot {token}"},
        )
        if r.status_code == 200:
            roles = sorted(r.json(), key=lambda x: -x.get("position", 0))

    return templates.TemplateResponse(
        "guild.html",
        {"request": request, "guild": guild, "scout_channels": scout_channels, "saved": saved, "roles": roles},
    )


@app.post("/guild/{guild_id}")
async def guild_save(
    request: Request,
    guild_id: str,
    category_id: str = Form(""),
    archive_channel_id: str = Form(""),
    allowed_role_ids: str = Form(""),
    scout_channel_id: str = Form(""),
):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    normalized_roles = ",".join(r.strip() for r in allowed_role_ids.split(",") if r.strip())
    await database.update_guild_config(
        guild_id=guild_id,
        category_id=category_id.strip(),
        archive_channel_id=archive_channel_id.strip(),
        allowed_role_ids=normalized_roles,
        scout_channel_id=scout_channel_id.strip(),
    )
    # Sync archive channel permissions whenever config is saved
    if archive_channel_id.strip():
        await _sync_archive_permissions(guild_id, archive_channel_id.strip(), normalized_roles)
    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)


@app.post("/guild/{guild_id}/roles/{role_id}/toggle")
async def toggle_role(request: Request, guild_id: str, role_id: str, field: str = Form(...)):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    if field not in {"allowed_role_ids", "res_manager_role_ids"}:
        return JSONResponse({"error": "invalid field"}, status_code=400)
    added = await database.toggle_role_in_field(guild_id, role_id, field)
    # Sync archive channel permissions when scout roles change
    if field == "allowed_role_ids":
        guild = await database.get_guild(guild_id)
        if guild and guild.get("archive_channel_id"):
            await _sync_archive_permissions(guild_id, guild["archive_channel_id"], guild.get("allowed_role_ids") or "")
    return JSONResponse({"added": added})


@app.post("/guild/{guild_id}/reset-scout")
async def reset_scout(request: Request, guild_id: str):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    await database.reset_scout_config(guild_id)
    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)


@app.post("/guild/{guild_id}/res-push/reset")
async def reset_res_push(request: Request, guild_id: str):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    await database.reset_res_config(guild_id)
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


@app.post("/guild/{guild_id}/auto-setup")
async def auto_setup(request: Request, guild_id: str):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")

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
    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)


@app.get("/guild/{guild_id}/stats", response_class=HTMLResponse)
async def guild_stats(request: Request, guild_id: str):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    stats = await database.get_guild_stats(guild_id)
    token = os.environ.get("DISCORD_TOKEN", "")
    discord_guild = None
    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://discord.com/api/v10/guilds/{guild_id}?with_counts=true", headers={"Authorization": f"Bot {token}"})
        if r.status_code == 200:
            discord_guild = r.json()

    return templates.TemplateResponse("stats.html", {"request": request, "guild": guild, "stats": stats, "discord_guild": discord_guild})


@app.post("/guild/{guild_id}/post-button")
async def post_button(request: Request, guild_id: str):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")

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
        return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)
    return RedirectResponse(f"/guild/{guild_id}?error=discord_{resp.status_code}", status_code=303)


# ---------------------------------------------------------------------------
# Routes — res-push
# ---------------------------------------------------------------------------

@app.get("/guild/{guild_id}/res-push", response_class=HTMLResponse)
async def res_push_page(request: Request, guild_id: str, saved: str = ""):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
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
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    normalized = ",".join(r.strip() for r in res_manager_role_ids.split(",") if r.strip())
    await database.update_res_config(guild_id=guild_id, res_request_channel_id=res_request_channel_id.strip(), res_answer_channel_id=res_answer_channel_id.strip(), res_push_category_id=res_push_category_id.strip(), res_manager_role_ids=normalized)
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


@app.post("/guild/{guild_id}/res-push/post-button")
async def res_post_button(request: Request, guild_id: str):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
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
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
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

        r = await client.post(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, json={"name": "res-answer", "type": 0, "parent_id": category_id})
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
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    stats = await database.get_res_stats(guild_id)
    return templates.TemplateResponse("res_push_stats.html", {"request": request, "guild": guild, "stats": stats})


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/inactive")
async def res_request_inactive(request: Request, guild_id: str, request_id: int):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    await database.set_res_request_status_by_id(request_id, "inactive")
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/activate")
async def res_request_activate(request: Request, guild_id: str, request_id: int):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    await database.set_res_request_status_by_id(request_id, "accepted")
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


async def _close_scout_channel_after_delay(channel_id: str, token: str, delay: int = 120):
    await asyncio.sleep(delay)
    async with httpx.AsyncClient() as client:
        await client.delete(f"https://discord.com/api/v10/channels/{channel_id}", headers={"Authorization": f"Bot {token}"})
    await database.delete_scout_channel(channel_id)


@app.post("/guild/{guild_id}/scout-channels/{channel_id}/close")
async def scout_channel_close(request: Request, guild_id: str, channel_id: str):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    token = os.environ.get("DISCORD_TOKEN", "")
    # Post closing message in the channel
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json={"content": "🔒 Scout channel closed via dashboard. Channel will be deleted in 2 minutes."},
        )
    asyncio.create_task(_close_scout_channel_after_delay(channel_id, token, delay=120))
    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)


@app.post("/guild/{guild_id}/res-push/requests/{request_id}/remove")
async def res_request_remove(request: Request, guild_id: str, request_id: int):
    session = get_session(request)
    if not session or not can_access_guild(session, guild_id):
        return RedirectResponse("/login")
    req = await database.get_res_request_by_id_web(request_id)
    if req and req.get("push_channel_id"):
        bot_token = os.environ.get("DISCORD_TOKEN", "")
        async with httpx.AsyncClient() as client:
            await client.delete(f"https://discord.com/api/v10/channels/{req['push_channel_id']}", headers={"Authorization": f"Bot {bot_token}"})
    await database.delete_res_request(request_id)
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)
