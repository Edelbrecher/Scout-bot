import os
import base64
from contextlib import asynccontextmanager

import httpx

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import database

load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")

def get_client_id() -> str:
    token = os.environ.get("DISCORD_TOKEN", "")
    try:
        part = token.split(".")[0]
        padding = 4 - len(part) % 4
        return base64.b64decode(part + "=" * padding).decode()
    except Exception:
        return ""
SESSION_COOKIE = "scouter_session"
SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours

signer = URLSafeTimedSerializer(SECRET_KEY)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def create_session(username: str) -> str:
    return signer.dumps(username)


def get_session_user(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        return signer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def require_auth(request: Request):
    """Return username or None (caller redirects)."""
    return get_session_user(request)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not get_session_user(request):
        return RedirectResponse("/login")
    return RedirectResponse("/dashboard")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if await database.verify_password(username, password):
        token = create_session(username)
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return response
    return RedirectResponse("/login?error=Invalid+credentials", status_code=303)


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not get_session_user(request):
        return RedirectResponse("/login")
    guilds = await database.get_all_guilds()
    client_id = get_client_id()
    invite_url = f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions=536996880&scope=bot+applications.commands" if client_id else ""
    return templates.TemplateResponse("dashboard.html", {"request": request, "guilds": guilds, "invite_url": invite_url})


@app.get("/guild/{guild_id}", response_class=HTMLResponse)
async def guild_page(request: Request, guild_id: str, saved: str = ""):
    if not get_session_user(request):
        return RedirectResponse("/login")
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
    if not get_session_user(request):
        return RedirectResponse("/login")
    normalized_roles = ",".join(r.strip() for r in allowed_role_ids.split(",") if r.strip())
    await database.update_guild_config(
        guild_id=guild_id,
        category_id=category_id.strip(),
        archive_channel_id=archive_channel_id.strip(),
        allowed_role_ids=normalized_roles,
        scout_channel_id=scout_channel_id.strip(),
    )
    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)


@app.get("/guild/{guild_id}/res-push", response_class=HTMLResponse)
async def res_push_page(request: Request, guild_id: str, saved: str = ""):
    if not get_session_user(request):
        return RedirectResponse("/login")
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    res_requests = await database.get_res_requests(guild_id)
    return templates.TemplateResponse(
        "res_push.html",
        {"request": request, "guild": guild, "res_requests": res_requests, "saved": saved},
    )


@app.post("/guild/{guild_id}/res-push")
async def res_push_save(
    request: Request,
    guild_id: str,
    res_request_channel_id: str = Form(""),
    res_answer_channel_id: str = Form(""),
    res_push_channel_id: str = Form(""),
    res_manager_role_ids: str = Form(""),
):
    if not get_session_user(request):
        return RedirectResponse("/login")
    normalized = ",".join(r.strip() for r in res_manager_role_ids.split(",") if r.strip())
    await database.update_res_config(
        guild_id=guild_id,
        res_request_channel_id=res_request_channel_id.strip(),
        res_answer_channel_id=res_answer_channel_id.strip(),
        res_push_channel_id=res_push_channel_id.strip(),
        res_manager_role_ids=normalized,
    )
    return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)


@app.post("/guild/{guild_id}/res-push/post-button")
async def res_post_button(request: Request, guild_id: str):
    if not get_session_user(request):
        return RedirectResponse("/login")

    guild = await database.get_guild(guild_id)
    if not guild or not guild.get("res_request_channel_id"):
        return RedirectResponse(f"/guild/{guild_id}/res-push?error=no_channel", status_code=303)

    token = os.environ.get("DISCORD_TOKEN", "")
    channel_id = guild["res_request_channel_id"]

    payload = {
        "embeds": [{
            "title": "🪖 Res-Push Request",
            "description": "Click the button below to submit a resource push request.",
            "color": 5793266,
        }],
        "components": [{
            "type": 1,
            "components": [{
                "type": 2, "style": 1,
                "label": "Res Request",
                "emoji": {"name": "🪖"},
                "custom_id": "persistent:res_request",
            }]
        }]
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json=payload,
        )

    if resp.status_code in (200, 201):
        msg_id = resp.json().get("id", "")
        await database.update_res_button(guild_id, channel_id, msg_id)
        return RedirectResponse(f"/guild/{guild_id}/res-push?saved=1", status_code=303)
    else:
        return RedirectResponse(f"/guild/{guild_id}/res-push?error=discord_{resp.status_code}", status_code=303)


@app.get("/guild/{guild_id}/res-push/stats", response_class=HTMLResponse)
async def res_push_stats(request: Request, guild_id: str):
    if not get_session_user(request):
        return RedirectResponse("/login")
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")
    stats = await database.get_res_stats(guild_id)
    return templates.TemplateResponse(
        "res_push_stats.html",
        {"request": request, "guild": guild, "stats": stats},
    )


@app.post("/guild/{guild_id}/auto-setup")
async def auto_setup(request: Request, guild_id: str):
    if not get_session_user(request):
        return RedirectResponse("/login")

    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    token = os.environ.get("DISCORD_TOKEN", "")
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:
        # 1. Create category
        r = await client.post(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers,
            json={"name": "Scout", "type": 4},
        )
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=category_{r.status_code}", status_code=303)
        category_id = r.json()["id"]

        # 2. Create scout-requests channel inside category
        r = await client.post(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers,
            json={"name": "scout-requests", "type": 0, "parent_id": category_id},
        )
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=scout_ch_{r.status_code}", status_code=303)
        scout_channel_id = r.json()["id"]

        # 3. Create scout-archive channel inside category
        r = await client.post(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers,
            json={"name": "scout-archive", "type": 0, "parent_id": category_id},
        )
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=archive_ch_{r.status_code}", status_code=303)
        archive_channel_id = r.json()["id"]

        # 4. Post scout request button
        payload = {
            "embeds": [{
                "title": "📡 Scout Request",
                "description": "Click the button below to submit a scout request.\nFill in the coordinates, player, village and time.",
                "color": 5793266,
            }],
            "components": [{
                "type": 1,
                "components": [{
                    "type": 2, "style": 1,
                    "label": "Scout Request",
                    "emoji": {"name": "🔍"},
                    "custom_id": "persistent:scout_request",
                }]
            }]
        }
        r = await client.post(
            f"https://discord.com/api/v10/channels/{scout_channel_id}/messages",
            headers=headers,
            json=payload,
        )
        if r.status_code not in (200, 201):
            return RedirectResponse(f"/guild/{guild_id}?error=button_{r.status_code}", status_code=303)
        button_message_id = r.json()["id"]

    await database.auto_setup_guild(
        guild_id=guild_id,
        category_id=category_id,
        scout_channel_id=scout_channel_id,
        archive_channel_id=archive_channel_id,
        button_message_id=button_message_id,
    )
    return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)


@app.get("/guild/{guild_id}/stats", response_class=HTMLResponse)
async def guild_stats(request: Request, guild_id: str):
    if not get_session_user(request):
        return RedirectResponse("/login")
    guild = await database.get_guild(guild_id)
    if not guild:
        return RedirectResponse("/dashboard")

    stats = await database.get_guild_stats(guild_id)

    token = os.environ.get("DISCORD_TOKEN", "")
    discord_guild = None
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://discord.com/api/v10/guilds/{guild_id}?with_counts=true",
            headers={"Authorization": f"Bot {token}"},
        )
        if r.status_code == 200:
            discord_guild = r.json()

    return templates.TemplateResponse(
        "stats.html",
        {"request": request, "guild": guild, "stats": stats, "discord_guild": discord_guild},
    )


@app.post("/guild/{guild_id}/post-button")
async def post_button(request: Request, guild_id: str):
    if not get_session_user(request):
        return RedirectResponse("/login")

    guild = await database.get_guild(guild_id)
    if not guild or not guild.get("scout_channel_id"):
        return RedirectResponse(f"/guild/{guild_id}?error=no_channel", status_code=303)

    token = os.environ.get("DISCORD_TOKEN", "")
    channel_id = guild["scout_channel_id"]

    payload = {
        "embeds": [{
            "title": "📡 Scout Request",
            "description": "Click the button below to submit a scout request.\nFill in the coordinates, player, village and time.",
            "color": 5793266,
        }],
        "components": [{
            "type": 1,
            "components": [{
                "type": 2, "style": 1,
                "label": "Scout Request",
                "emoji": {"name": "🔍"},
                "custom_id": "persistent:scout_request",
            }]
        }]
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json=payload,
        )

    if resp.status_code == 200:
        msg_id = resp.json().get("id", "")
        await database.update_button_message(guild_id, channel_id, msg_id)
        return RedirectResponse(f"/guild/{guild_id}?saved=1", status_code=303)
    else:
        return RedirectResponse(f"/guild/{guild_id}?error=discord_{resp.status_code}", status_code=303)
