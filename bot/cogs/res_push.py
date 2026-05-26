from utils import travops_footer
import asyncio
import re
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

import database


# ---------------------------------------------------------------------------
# Amount parser — supports "90k", "1.5m", "50.000", "50,000", plain ints
# ---------------------------------------------------------------------------

def _parse_amount(s: str) -> int | None:
    """Parse a human-friendly number string into an integer.

    Supported formats:
      90k / 90K         → 90 000
      1.5k / 1,5k       → 1 500
      1m / 1.5m / 1,5m  → 1 000 000 / 1 500 000
      50.000 / 50,000   → 50 000  (thousand-separator dot/comma)
      50000             → 50 000
    Returns None if the string cannot be parsed.
    """
    s = s.strip().replace(" ", "").replace("_", "")
    if not s:
        return None

    multiplier = 1
    lower = s.lower()
    if lower.endswith("k"):
        multiplier = 1_000
        s = s[:-1]
    elif lower.endswith("m"):
        multiplier = 1_000_000
        s = s[:-1]

    # Normalise decimal separator: if both . and , appear, the one with
    # exactly 3 digits after it is the thousands separator.
    # Simple heuristic: if there's exactly one separator and ≤ 3 digits follow
    # it → decimal; if 3 digits follow it → thousands separator.
    # Replace European thousands sep (dot with 3 trailing digits) with nothing,
    # then replace comma decimal sep with dot.
    # E.g.  "50.000" → "50000"   "1.5" → "1.5"   "1,5" → "1.5"
    if "." in s and "," in s:
        # Both present — the one before the other is thousands sep
        if s.index(".") < s.index(","):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "." in s:
        # Dot: thousands sep if exactly 3 digits follow each dot
        parts = s.split(".")
        if all(len(p) == 3 for p in parts[1:]):
            s = s.replace(".", "")   # thousands sep → remove
        # else leave as decimal point
    elif "," in s:
        parts = s.split(",")
        if all(len(p) == 3 for p in parts[1:]):
            s = s.replace(",", "")   # thousands sep → remove
        else:
            s = s.replace(",", ".")  # decimal comma → dot

    try:
        return int(float(s) * multiplier)
    except (ValueError, OverflowError):
        return None


def _fmt(n: int) -> str:
    """Format integer with dot as thousands separator: 90000 → '90.000'."""
    return f"{n:,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Permission check
# ---------------------------------------------------------------------------

async def _is_authorized(interaction: discord.Interaction) -> bool:
    config = await database.get_guild_config(str(interaction.guild.id))
    if not config:
        return False
    role_ids_str = config.get("res_manager_role_ids") or ""
    allowed_ids = {r.strip() for r in role_ids_str.split(",") if r.strip()}
    if not allowed_ids:
        return interaction.user.guild_permissions.administrator
    user_role_ids = {str(r.id) for r in interaction.user.roles}
    return bool(user_role_ids & allowed_ids) or interaction.user.guild_permissions.administrator


async def _check_auth(interaction: discord.Interaction) -> bool:
    if not await _is_authorized(interaction):
        await interaction.response.send_message(
            "⛔ You don't have permission to manage res-push requests.", ephemeral=True
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def _build_request_embed(data: dict, status: str) -> discord.Embed:
    colors = {
        "pending": discord.Color.blue(),
        "hold": discord.Color.orange(),
        "accepted": discord.Color.green(),
        "rejected": discord.Color.red(),
        "inactive": discord.Color.dark_grey(),
        "completed": discord.Color.gold(),
    }
    status_labels = {
        "pending": "⏳ Pending",
        "hold": "⏸️ On Hold",
        "accepted": "✅ Accepted",
        "rejected": "❌ Rejected",
        "inactive": "🔒 Inactive",
        "completed": "🏆 Completed",
    }
    embed = discord.Embed(
        title="🪖 Res-Push Request",
        color=colors.get(status, discord.Color.default()),
    )
    parsed_goal = _parse_amount(data["push_height"]) if data.get("push_height") else None
    goal_display = _fmt(parsed_goal) if parsed_goal else (data.get("push_height") or "—")
    embed.add_field(name="Spieler", value=data["player_name"], inline=True)
    embed.add_field(name="Ort / Dorf", value=data["coordinates"], inline=True)
    embed.add_field(name="Ziel", value=goal_display, inline=True)
    if data.get("reason"):
        embed.add_field(name="Grund / Details", value=data["reason"], inline=False)
    embed.add_field(name="Status", value=status_labels.get(status, status), inline=False)
    embed.set_footer(**travops_footer(f"Angefragt von {data['user_name']} • {data['created_at'][:16]}"))
    return embed


def _build_push_embed(data: dict, contributions: list[dict], status: str = "active") -> discord.Embed:
    total = sum((_parse_amount(c["amount"]) or 0) for c in contributions)
    target = _parse_amount(data["push_height"]) if data.get("push_height") else None

    if target and target > 0:
        progress_pct = int(total / target * 100)
        # Bar always shows real 0-100%, but label shows actual % (can exceed 100)
        bar_filled = min(progress_pct, 100) // 10
        bar_color  = "🟩" if progress_pct >= 100 else "🟦"
        bar_empty  = "⬜"
        progress_bar = bar_color * bar_filled + bar_empty * (10 - bar_filled)
        overshoot = f" (+{_fmt(total - target)})" if total > target else ""
        progress_text = f"{progress_bar} **{progress_pct}%**  ({_fmt(total)} / {_fmt(target)}{overshoot})"
    else:
        progress_text = f"Gesendet: **{_fmt(total)}**"

    goal_reached = target and total >= target

    color = discord.Color.gold() if status == "completed" else \
            discord.Color.dark_grey() if status == "inactive" else \
            discord.Color.from_rgb(34, 197, 94) if goal_reached else \
            discord.Color.green()

    title = "🏆 Res-Push — Ziel erreicht!" if (goal_reached and status == "active") else \
            "🏆 Res-Push — ABGESCHLOSSEN!" if status == "completed" else \
            "🔒 Res-Push — Inaktiv" if status == "inactive" else \
            "🪖 Res-Push"

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Spieler", value=data["player_name"], inline=True)
    embed.add_field(name="Ort / Dorf", value=data["coordinates"], inline=True)
    goal_display = _fmt(target) if target else data.get("push_height", "—")
    embed.add_field(name="Ziel", value=goal_display, inline=True)
    embed.add_field(name="Fortschritt", value=progress_text, inline=False)
    if contributions:
        contrib_lines = "\n".join(
            f"• **{c['user_name']}**: {_fmt(_parse_amount(c['amount']))} " if _parse_amount(c['amount']) is not None
            else f"• **{c['user_name']}**: {c['amount']}"
            for c in contributions[-10:]
        )
        embed.add_field(name=f"Beiträge ({len(contributions)})", value=contrib_lines, inline=False)
    embed.set_footer(**travops_footer(f"Angefragt von {data['user_name']}"))
    return embed


def _disabled_push_view(label: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="I sent", emoji="📦", style=discord.ButtonStyle.primary, disabled=True))
    view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=True))
    view.add_item(discord.ui.Button(label="Remove Channel", style=discord.ButtonStyle.danger, disabled=True))
    return view


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class ResModal(discord.ui.Modal, title="Res-Push Request"):
    player_name = discord.ui.TextInput(label="Player Name", placeholder="Who needs the push?", max_length=100)
    coordinates = discord.ui.TextInput(label="Coordinates", placeholder="e.g. 500|500", max_length=50)
    push_height = discord.ui.TextInput(label="Push Height", placeholder="e.g. 50000 (troops/resources)", max_length=50)
    reason = discord.ui.TextInput(
        label="Reason", placeholder="Why is this push needed?",
        required=False, style=discord.TextStyle.paragraph, max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        config = await database.get_guild_config(str(guild.id))

        if not config or not config.get("res_answer_channel_id"):
            await interaction.response.send_message(
                "⚠️ Res-Push is not fully configured. Ask an admin to set it up.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        answer_channel = guild.get_channel(int(config["res_answer_channel_id"]))
        if not answer_channel:
            await interaction.followup.send("⚠️ Answer channel not found.", ephemeral=True)
            return

        data = {
            "player_name": self.player_name.value,
            "coordinates": self.coordinates.value,
            "push_height": self.push_height.value,
            "reason": self.reason.value or "",
            "user_name": interaction.user.display_name,
            "user_id": str(interaction.user.id),
            "created_at": datetime.utcnow().isoformat(),
        }

        embed = _build_request_embed(data, "pending")
        msg = await answer_channel.send(
            content=f"New res-push request from {interaction.user.mention}",
            embed=embed,
            view=ResAnswerView(),
        )

        await database.add_res_request(
            guild_id=str(guild.id),
            answer_message_id=str(msg.id),
            user_id=data["user_id"],
            user_name=data["user_name"],
            player_name=data["player_name"],
            coordinates=data["coordinates"],
            push_height=data["push_height"],
            reason=data["reason"],
        )

        await interaction.followup.send("✅ Your res-push request has been submitted!", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        try:
            await interaction.followup.send("❌ Something went wrong.", ephemeral=True)
        except Exception:
            pass
        raise error


class ResSentModal(discord.ui.Modal, title="How much did you send?"):
    amount = discord.ui.TextInput(label="Amount Sent", placeholder="e.g. 5000", max_length=20)

    def __init__(self, request_id: int):
        super().__init__()
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        raw = self.amount.value.strip()
        parsed = _parse_amount(raw)
        # Store the parsed integer as string; fall back to raw if unrecognised
        store_value = str(parsed) if parsed is not None else raw

        await database.add_res_contribution(
            request_id=self.request_id,
            user_id=str(interaction.user.id),
            user_name=interaction.user.display_name,
            amount=store_value,
        )

        req = await database.get_res_request_by_id(self.request_id)
        contribs = await database.get_res_contributions(self.request_id)

        if not req or not interaction.message:
            display = _fmt(parsed) if parsed is not None else raw
            await interaction.followup.send(f"✅ Eingetragen: **{display}** gesendet. Danke!", ephemeral=True)
            return

        total = sum((_parse_amount(c["amount"]) or 0) for c in contribs)
        target = _parse_amount(req["push_height"]) if req.get("push_height") else None
        goal_reached = target and total >= target

        # Keep the view active even after goal — allow further contributions
        updated_embed = _build_push_embed(req, contribs)
        edit_kwargs: dict = {"embed": updated_embed}
        if goal_reached:
            edit_kwargs["content"] = "🏆 **Ziel erreicht! Weitere Beiträge werden weiterhin gezählt.**"
        await interaction.message.edit(**edit_kwargs)

        display = _fmt(parsed) if parsed is not None else raw
        suffix = " 🏆 **Ziel erreicht!**" if goal_reached else ""
        await interaction.followup.send(f"✅ Eingetragen: **{display}** gesendet. Danke!{suffix}", ephemeral=True)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class ResRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Res Request", style=discord.ButtonStyle.primary,
        emoji="🪖", custom_id="persistent:res_request",
    )
    async def res_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResModal())


class ResAnswerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="persistent:res_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _check_auth(interaction):
            return

        req = await database.get_res_request_by_answer_msg(str(interaction.message.id))
        if not req:
            await interaction.response.send_message("⚠️ Request not found.", ephemeral=True)
            return

        config = await database.get_guild_config(str(interaction.guild.id))
        if not config:
            await interaction.response.send_message("⚠️ Guild not configured.", ephemeral=True)
            return

        # Resolve category: prefer res_push_category_id, fall back to parent of res_push_channel_id
        category = None
        if config.get("res_push_category_id"):
            category = interaction.guild.get_channel(int(config["res_push_category_id"]))
        elif config.get("res_push_channel_id"):
            ch = interaction.guild.get_channel(int(config["res_push_channel_id"]))
            if ch and ch.category:
                category = ch.category

        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "⚠️ Res-Push category not configured. Please reset and run Auto Setup in the dashboard.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        # Build permission overwrites for the push channel.
        # Default role is explicitly denied so the channel is private even if
        # the category is visible to everyone.
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                manage_channels=True, manage_messages=True,
            ),
        }
        # Give the requester access so they can see their own push channel
        overwrites[interaction.user] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
        )
        # Give manager roles explicit access
        for role_id_str in (config.get("res_manager_role_ids") or "").split(","):
            role_id_str = role_id_str.strip()
            if not role_id_str:
                continue
            role = interaction.guild.get_role(int(role_id_str))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_messages=True,
                )

        safe_player = re.sub(r"[^a-z0-9]", "-", req["player_name"].lower())
        channel_name = f"push-{safe_player}"[:100]

        push_channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=f"Res-Push: {req['player_name']} @ {req['coordinates']} — Goal: {req['push_height']}",
            overwrites=overwrites,
        )

        push_embed = _build_push_embed(req, [])
        await push_channel.send(embed=push_embed, view=ResPushChannelView())

        await database.update_res_request_status(
            answer_message_id=str(interaction.message.id),
            status="accepted",
            push_channel_id=str(push_channel.id),
        )

        updated = _build_request_embed(req, "accepted")
        done_view = discord.ui.View()
        done_view.add_item(discord.ui.Button(
            label="Accepted", style=discord.ButtonStyle.success, disabled=True
        ))
        await interaction.message.edit(
            content=f"✅ Accepted by {interaction.user.mention} → {push_channel.mention}",
            embed=updated, view=done_view,
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="persistent:res_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _check_auth(interaction):
            return

        req = await database.get_res_request_by_answer_msg(str(interaction.message.id))
        if not req:
            await interaction.response.send_message("⚠️ Request not found.", ephemeral=True)
            return

        await database.update_res_request_status(str(interaction.message.id), "rejected")

        updated = _build_request_embed(req, "rejected")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Rejected", style=discord.ButtonStyle.danger, disabled=True))
        await interaction.response.edit_message(
            content=f"❌ Rejected by {interaction.user.mention}",
            embed=updated, view=view,
        )

    @discord.ui.button(label="Hold", style=discord.ButtonStyle.secondary, custom_id="persistent:res_hold")
    async def hold(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _check_auth(interaction):
            return

        req = await database.get_res_request_by_answer_msg(str(interaction.message.id))
        if not req:
            await interaction.response.send_message("⚠️ Request not found.", ephemeral=True)
            return

        await database.update_res_request_status(str(interaction.message.id), "hold")

        updated = _build_request_embed(req, "hold")
        await interaction.response.edit_message(
            content=f"⏸️ Put on hold by {interaction.user.mention}",
            embed=updated, view=ResAnswerView(),
        )


class ResPushChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="I sent", style=discord.ButtonStyle.primary,
        emoji="📦", custom_id="persistent:res_push_sent",
    )
    async def i_sent(self, interaction: discord.Interaction, button: discord.ui.Button):
        req = await database.get_res_request_by_push_channel(str(interaction.channel.id))
        if not req:
            await interaction.response.send_message("⚠️ Request not found.", ephemeral=True)
            return
        await interaction.response.send_modal(ResSentModal(request_id=req["id"]))

    @discord.ui.button(
        label="Set Inactive", style=discord.ButtonStyle.secondary,
        emoji="⏸️", custom_id="persistent:res_push_inactive",
    )
    async def set_inactive(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _check_auth(interaction):
            return

        req = await database.get_res_request_by_push_channel(str(interaction.channel.id))
        if not req:
            await interaction.response.send_message("⚠️ Request not found.", ephemeral=True)
            return

        await database.update_res_request_status(req["answer_message_id"], "inactive")

        contribs = await database.get_res_contributions(req["id"])
        inactive_embed = _build_push_embed(req, contribs, status="inactive")
        await interaction.response.edit_message(
            content=f"🔒 Set inactive by {interaction.user.mention}",
            embed=inactive_embed,
            view=_disabled_push_view("Inactive"),
        )

    @discord.ui.button(
        label="Remove Channel", style=discord.ButtonStyle.danger,
        custom_id="persistent:res_push_remove",
    )
    async def remove_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _check_auth(interaction):
            return

        await interaction.response.send_message("🗑️ Deleting channel in 5 seconds...", ephemeral=False)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Res-Push removed by {interaction.user}")
        except discord.NotFound:
            pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ResPush(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup-res", description="Post the Res-Push Request button in this channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_res(self, interaction: discord.Interaction):
        config = await database.get_guild_config(str(interaction.guild.id))
        if not config or not config.get("res_answer_channel_id") or not config.get("res_push_category_id"):
            await interaction.response.send_message(
                "⚠️ Please configure all Res-Push settings in the web admin panel first.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🪖 Res-Push Request",
            description="Click the button below to submit a resource push request.",
            color=discord.Color.blurple(),
        )
        msg = await interaction.channel.send(embed=embed, view=ResRequestView())
        await database.update_res_button(
            guild_id=str(interaction.guild.id),
            res_request_channel_id=str(interaction.channel.id),
            res_button_message_id=str(msg.id),
        )
        await interaction.response.send_message("✅ Res-Push Request button posted!", ephemeral=True)


async def setup(bot: commands.Bot):
    bot.add_view(ResRequestView())
    bot.add_view(ResAnswerView())
    bot.add_view(ResPushChannelView())
    await bot.add_cog(ResPush(bot))
