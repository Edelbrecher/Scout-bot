"""
hub.py — Global Request Hub cog.
One Discord channel with all request buttons.
Each button opens a modal and creates a specific channel in the configured category.
"""
import asyncio
import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils import require_premium, travops_footer
from i18n import t, get_guild_lang


# ---------------------------------------------------------------------------
# Helpers (shared with scout.py logic)
# ---------------------------------------------------------------------------

async def _get_config_and_category(guild: discord.Guild) -> tuple[dict | None, discord.CategoryChannel | None]:
    config = await database.get_guild_config(str(guild.id))
    if not config or not config.get("category_id"):
        return None, None
    category = guild.get_channel(int(config["category_id"]))
    if not category or not isinstance(category, discord.CategoryChannel):
        return config, None
    return config, category


def _build_overwrites(guild: discord.Guild, config: dict, requester: discord.Member) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            embed_links=True, attach_files=True, manage_channels=True,
        ),
        requester: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
    }
    for role_id_str in (config.get("allowed_role_ids") or "").split(","):
        role_id_str = role_id_str.strip()
        if not role_id_str:
            continue
        role = guild.get_role(int(role_id_str))
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)
    return overwrites


def _safe(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "-", s.lower())[:40].strip("-")


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class ScoutHubModal(discord.ui.Modal, title="🔍 Scout-Request"):
    player = discord.ui.TextInput(label="Spieler-Name", placeholder="z.B. Currax", max_length=100)
    coordinates = discord.ui.TextInput(label="Koordinaten", placeholder="z.B. (102|47)", max_length=30)
    village = discord.ui.TextInput(label="Dorfname", placeholder="z.B. Hauptdorf", required=False, max_length=100)
    time = discord.ui.TextInput(label="Bis wann?", placeholder="z.B. heute 22:00 UTC", max_length=60)
    additional_info = discord.ui.TextInput(
        label="Zusätzliche Infos", required=False,
        style=discord.TextStyle.paragraph, max_length=300,
    )

    def __init__(self, corn: bool = False, permanent: bool = False):
        if corn:
            self.title = "🌾 Kornspäh-Request"
        elif permanent:
            self.title = "📡 Permanent-Scout Anfrage"
        super().__init__()
        self.corn = corn
        self.permanent = permanent

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_premium(interaction):
            return
        guild = interaction.guild
        lang = await get_guild_lang(str(guild.id))
        config, category = await _get_config_and_category(guild)
        if not config:
            await interaction.response.send_message(t(lang, "not_configured"), ephemeral=True)
            return
        if not category:
            await interaction.response.send_message(t(lang, "category_not_found"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        prefix = "perm-scout" if self.permanent else ("corn-scout" if self.corn else "scout")
        channel_name = f"{prefix}-{_safe(self.player.value)}-{_safe(self.coordinates.value)}"[:100]

        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"{'Permanent-Scout' if self.permanent else ('Corn-Scout' if self.corn else 'Scout')}: {self.player.value} @ {self.coordinates.value}",
            overwrites=overwrites,
        )

        if self.permanent:
            color = discord.Color.teal()
            title = t(lang, "perm_scout.title")
            desc = t(lang, "perm_scout.desc", village=self.village.value or "—",
                     player=self.player.value, coords=self.coordinates.value)
        elif self.corn:
            color = discord.Color.gold()
            title = t(lang, "corn.title")
            desc = t(lang, "corn.desc", player=self.player.value, coords=self.coordinates.value)
        else:
            color = discord.Color.blurple()
            title = t(lang, "scout.title")
            desc = t(lang, "hub.scout.desc", player=self.player.value, coords=self.coordinates.value)

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name=t(lang, "hub.scout.field.player_embed"), value=self.player.value, inline=True)
        embed.add_field(name=t(lang, "hub.scout.field.coords_embed"), value=self.coordinates.value, inline=True)
        if self.village.value:
            embed.add_field(name=t(lang, "hub.scout.field.village_embed"), value=self.village.value, inline=True)
        embed.add_field(name=t(lang, "hub.scout.field.time_embed"), value=self.time.value, inline=True)
        if self.additional_info.value:
            embed.add_field(name=t(lang, "hub.scout.field.info_embed"), value=self.additional_info.value, inline=False)
        embed.set_footer(**travops_footer(t(lang, "requested_by", user=interaction.user.display_name)))

        from cogs.scout import ScoutActionView
        await new_channel.send(
            content=t(lang, "hub.scout.new_request", user=interaction.user.mention),
            embed=embed,
            view=ScoutActionView(),
        )

        # Save to DB (reuse scout channel registration)
        await database.add_scout_channel(
            channel_id=str(new_channel.id), guild_id=str(guild.id),
            player=self.player.value, coordinates=self.coordinates.value,
            village=self.village.value or "", scout_time=self.time.value,
            additional_info=self.additional_info.value or "",
            requested_by_id=str(interaction.user.id),
            requested_by_name=interaction.user.display_name,
            corn_scout=self.corn,
        )

        await interaction.followup.send(t(lang, "channel_created", channel=new_channel.mention), ephemeral=True)


def _parse_time(s: str) -> datetime | None:
    """Try to parse HH:MM (or HH:MM:SS) from a string. Returns a datetime on today's date."""
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if not m:
        return None
    h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if not (0 <= h < 24 and 0 <= mi < 60 and 0 <= sec < 60):
        return None
    now = datetime.utcnow().replace(second=0, microsecond=0)
    return now.replace(hour=h, minute=mi, second=sec)


def _between_time(t1: datetime, t2: datetime) -> str:
    """Return HH:MM UTC of the midpoint between t1 and t2.
    If t2 <= t1, assume t2 is the next day."""
    if t2 <= t1:
        t2 += timedelta(days=1)
    mid = t1 + (t2 - t1) / 2
    return mid.strftime("%H:%M") + " UTC"


async def _create_defend_channel(
    interaction: discord.Interaction,
    defender: str, attacker: str, coords: str, size: str, notes: str,
    arrival_1: str, arrival_2: str, timed: bool,
):
    """Shared logic for both Defend and TimedDefend modals."""
    guild = interaction.guild
    lang = await get_guild_lang(str(guild.id))
    config, category = await _get_config_and_category(guild)
    if not config:
        await interaction.response.send_message(t(lang, "not_configured"), ephemeral=True)
        return
    if not category:
        await interaction.response.send_message(t(lang, "category_not_found"), ephemeral=True)
        return

    # Validate & calculate between-time for timed defend
    between_str = ""
    if timed and arrival_2:
        t1 = _parse_time(arrival_1)
        t2 = _parse_time(arrival_2)
        if t1 and t2:
            if t2 <= t1:
                t2 += timedelta(days=1)
            delta = (t2 - t1).total_seconds()
            if delta <= 0:
                await interaction.response.send_message(
                    t(lang, "time_order_error"),
                    ephemeral=True,
                )
                return
            elif delta < 60:
                # Gap too small for a between-wave — defend on the first wave
                between_str = t1.strftime("%H:%M") + " UTC"
            else:
                between_str = _between_time(t1, t2)

    await interaction.response.defer(ephemeral=True)

    prefix = "timed-def" if timed else "defend"
    channel_name = f"{prefix}-{_safe(defender)}-{_safe(coords)}"[:100]
    topic = (f"{'Timed-Defend' if timed else 'Defend'}: {attacker} @ {coords} | "
             f"{arrival_1}{' → ' + arrival_2 if arrival_2 else ''}")

    overwrites = _build_overwrites(guild, config, interaction.user)
    new_channel = await guild.create_text_channel(
        name=channel_name, category=category, topic=topic, overwrites=overwrites,
    )

    embed = discord.Embed(
        title=t(lang, "defend.timed_title") if timed else t(lang, "defend.title"),
        color=discord.Color.from_rgb(239, 68, 68),
    )
    embed.add_field(name=t(lang, "defend.field.defender"), value=defender, inline=True)
    embed.add_field(name=t(lang, "defend.field.attacker"), value=attacker, inline=True)
    embed.add_field(name=t(lang, "defend.field.target"),   value=coords,   inline=True)

    if timed and arrival_2:
        embed.add_field(name=t(lang, "defend.field.wave1"), value=arrival_1, inline=True)
        embed.add_field(name=t(lang, "defend.field.wave2"), value=arrival_2, inline=True)
        if between_str:
            embed.add_field(
                name=t(lang, "defend.field.between"),
                value=t(lang, "defend.between_desc", time=between_str),
                inline=False,
            )
    else:
        embed.add_field(name=t(lang, "defend.field.arrival"), value=arrival_1, inline=True)

    if size:
        embed.add_field(name=t(lang, "defend.field.size"), value=size, inline=True)
    if notes:
        embed.add_field(name=t(lang, "defend.field.notes"), value=notes, inline=False)
    embed.set_footer(**travops_footer(t(lang, "reported_by", user=interaction.user.display_name)))

    timed_prefix = t(lang, "defend.timed_prefix") if timed else ""
    await new_channel.send(
        content=t(lang, "defend.ping", user=interaction.user.mention, prefix=timed_prefix),
        embed=embed,
        view=DefendCloseView(),
    )

    arrival_db = arrival_1
    if arrival_2:
        arrival_db = f"{arrival_1} → {arrival_2}"
        if between_str:
            arrival_db += f" (Zwischen: {between_str})"

    await database.add_defend_channel(
        channel_id=str(new_channel.id), guild_id=str(guild.id),
        type="timed_defend" if timed else "defend",
        attacker=attacker, coords=coords,
        arrival_time=arrival_db, notes=notes,
        requested_by_id=str(interaction.user.id),
        requested_by_name=interaction.user.display_name,
    )
    await interaction.followup.send(t(lang, "channel_created", channel=new_channel.mention), ephemeral=True)


class DefendModal(discord.ui.Modal, title="🛡️ Defend Anfrage"):
    """Plain defend — single arrival time."""
    defender = discord.ui.TextInput(label="Verteidiger (dein Spielername)", placeholder="z.B. Currax", max_length=100)
    attacker = discord.ui.TextInput(label="Angreifer (Spieler)", placeholder="z.B. Maximus", max_length=100)
    coords   = discord.ui.TextInput(label="Angriffsziel (Koords)", placeholder="z.B. (102|47)", max_length=30)
    arrival  = discord.ui.TextInput(label="Ankunftszeit", placeholder="z.B. 23:45 UTC", max_length=30)
    size     = discord.ui.TextInput(label="Angriffsgröße", placeholder="z.B. klein / mittel / ~500 Axt", required=False, max_length=60)

    async def on_submit(self, interaction: discord.Interaction):
        await _create_defend_channel(
            interaction,
            defender=self.defender.value.strip(),
            attacker=self.attacker.value.strip(),
            coords=self.coords.value.strip(),
            size=self.size.value.strip(),
            notes="",
            arrival_1=self.arrival.value.strip(),
            arrival_2="",
            timed=False,
        )


class TimedDefendModal(discord.ui.Modal, title="⏱️ Timed-Defend Anfrage"):
    """Timed defend — two arrival times, calculates between-defense window."""
    defender  = discord.ui.TextInput(label="Verteidiger (dein Spielername)", placeholder="z.B. Currax", max_length=100)
    attacker  = discord.ui.TextInput(label="Angreifer (Spieler)", placeholder="z.B. Maximus", max_length=100)
    coords    = discord.ui.TextInput(label="Angriffsziel (Koords)", placeholder="z.B. (102|47)", max_length=30)
    arrival   = discord.ui.TextInput(label="1. Ankunftszeit (frühere Welle)", placeholder="z.B. 23:45 UTC", max_length=30)
    arrival_2 = discord.ui.TextInput(label="2. Ankunftszeit (spätere Welle)", placeholder="z.B. 00:10 UTC (muss später sein)", max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        await _create_defend_channel(
            interaction,
            defender=self.defender.value.strip(),
            attacker=self.attacker.value.strip(),
            coords=self.coords.value.strip(),
            size=self.size.value.strip(),
            notes="",
            arrival_1=self.arrival.value.strip(),
            arrival_2=self.arrival_2.value.strip(),
            timed=True,
        )


# ---------------------------------------------------------------------------
# Defend close view
# ---------------------------------------------------------------------------

class DefendCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ Defend done",
        style=discord.ButtonStyle.success,
        custom_id="persistent:defend_done",
    )
    async def done_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        await database.close_defend_channel(str(interaction.channel.id))
        await interaction.response.send_message(
            t(lang, "defend.done", user=interaction.user.mention)
        )
        button.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(
        label="🔒 Close channel",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:defend_channel_close",
    )
    async def close_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        await interaction.response.send_message(
            t(lang, "defend.closing", user=interaction.user.mention)
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=t(lang, "defend.channel_closed_reason"))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Res-Push Modal
# ---------------------------------------------------------------------------

class HubResAnswerView(discord.ui.View):
    """Admin Accept/Reject/Hold view posted directly in the hub-created res-push channel."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _get_req_and_lang(self, interaction: discord.Interaction):
        from cogs.res_push import _is_authorized
        if not await _is_authorized(interaction):
            lang = await get_guild_lang(str(interaction.guild_id))
            await interaction.response.send_message(
                "⛔ Keine Berechtigung." if lang == "de" else "⛔ No permission.", ephemeral=True
            )
            return None, None
        req = await database.get_res_request_by_answer_msg(str(interaction.message.id))
        lang = await get_guild_lang(str(interaction.guild_id))
        if not req:
            await interaction.response.send_message("⚠️ Anfrage nicht gefunden." if lang == "de" else "⚠️ Request not found.", ephemeral=True)
            return None, lang
        return req, lang

    @discord.ui.button(label="✅ Annehmen", style=discord.ButtonStyle.success, custom_id="persistent:hub_res_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.res_push import _build_push_embed, ResPushChannelView
        req, lang = await self._get_req_and_lang(interaction)
        if not req:
            return

        await interaction.response.defer()

        # Transform this channel into the push tracking channel
        await database.update_res_request_status(
            answer_message_id=str(interaction.message.id),
            status="accepted",
            push_channel_id=str(interaction.channel.id),
        )

        # Fetch updated req (now has push_channel_id)
        req = await database.get_res_request_by_answer_msg(str(interaction.message.id))
        push_embed = _build_push_embed(req, [])

        accepted_label = "✅ Angenommen" if lang == "de" else "✅ Accepted"
        done_view = discord.ui.View()
        done_view.add_item(discord.ui.Button(label=accepted_label, style=discord.ButtonStyle.success, disabled=True))

        # Replace the pending embed/view with a simple accepted note
        from cogs.res_push import _build_request_embed
        accepted_embed = _build_request_embed(req, "accepted")
        await interaction.message.edit(
            content=(f"✅ Angenommen von {interaction.user.mention}" if lang == "de" else f"✅ Accepted by {interaction.user.mention}"),
            embed=accepted_embed,
            view=done_view,
        )

        # Post the live push tracking embed below
        await interaction.channel.send(embed=push_embed, view=ResPushChannelView())

    @discord.ui.button(label="❌ Ablehnen", style=discord.ButtonStyle.danger, custom_id="persistent:hub_res_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.res_push import _build_request_embed
        req, lang = await self._get_req_and_lang(interaction)
        if not req:
            return

        await database.update_res_request_status(str(interaction.message.id), "rejected")

        updated = _build_request_embed(req, "rejected")
        rejected_label = "❌ Abgelehnt" if lang == "de" else "❌ Rejected"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=rejected_label, style=discord.ButtonStyle.danger, disabled=True))
        await interaction.response.edit_message(
            content=(f"❌ Abgelehnt von {interaction.user.mention}" if lang == "de" else f"❌ Rejected by {interaction.user.mention}"),
            embed=updated, view=view,
        )

    @discord.ui.button(label="⏸️ Zurückstellen", style=discord.ButtonStyle.secondary, custom_id="persistent:hub_res_hold")
    async def hold(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.res_push import _build_request_embed
        req, lang = await self._get_req_and_lang(interaction)
        if not req:
            return

        await database.update_res_request_status(str(interaction.message.id), "hold")
        updated = _build_request_embed(req, "hold")
        await interaction.response.edit_message(
            content=(f"⏸️ Zurückgestellt von {interaction.user.mention}" if lang == "de" else f"⏸️ Put on hold by {interaction.user.mention}"),
            embed=updated, view=HubResAnswerView(),
        )


class ResPushHubModal(discord.ui.Modal, title="🪖 Res-Push Anfrage"):
    village = discord.ui.TextInput(label="Dein Dorf (Empfänger)", placeholder="z.B. Hauptdorf (102|47)", max_length=100)
    resources = discord.ui.TextInput(
        label="Was brauchst du?",
        placeholder="z.B. 50k Holz, 30k Lehm",
        style=discord.TextStyle.paragraph, max_length=300,
    )
    until = discord.ui.TextInput(label="Bis wann?", placeholder="z.B. heute 22:00 UTC", max_length=60)
    notes = discord.ui.TextInput(label="Weitere Infos", required=False, style=discord.TextStyle.paragraph, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        from cogs.res_push import _build_request_embed
        from datetime import datetime

        guild = interaction.guild
        lang = await get_guild_lang(str(guild.id))
        config, category = await _get_config_and_category(guild)
        if not config or not category:
            await interaction.response.send_message(t(lang, "not_configured"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Build channel + overwrites
        channel_name = f"res-push-{_safe(interaction.user.display_name)}"[:100]
        overwrites = _build_overwrites(guild, config, interaction.user)
        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"Res-Push: {interaction.user.display_name}: {self.resources.value[:80]}",
            overwrites=overwrites,
        )

        # Build request data
        reason_parts = []
        if lang == "de":
            reason_parts.append(f"Bis wann: {self.until.value}")
        else:
            reason_parts.append(f"Until: {self.until.value}")
        if self.notes.value:
            reason_parts.append(self.notes.value)

        data = {
            "player_name": interaction.user.display_name,
            "coordinates": self.village.value,
            "push_height": self.resources.value,
            "reason": "\n".join(reason_parts),
            "user_name": interaction.user.display_name,
            "user_id": str(interaction.user.id),
            "created_at": datetime.utcnow().isoformat(),
        }

        # Post the pending embed with admin buttons in the new channel
        embed = _build_request_embed(data, "pending")
        content = (
            f"🪖 {interaction.user.mention} — {'Neue Res-Push Anfrage' if lang == 'de' else 'New Res-Push Request'}"
        )
        msg = await new_channel.send(content=content, embed=embed, view=HubResAnswerView())

        # Save to DB (using the in-channel message id as the answer_message_id)
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

        await interaction.followup.send(
            t(lang, "res_push.channel_created", channel=new_channel.mention), ephemeral=True
        )


# ---------------------------------------------------------------------------
# Private Channel — Grant / Revoke Access
# ---------------------------------------------------------------------------

def _find_member(guild: discord.Guild, name: str) -> discord.Member | None:
    """Find a guild member by display name, username, @name, or <@id> mention."""
    name = name.strip()
    # Real Discord mention: <@123456> or <@!123456>
    m = re.match(r"<@!?(\d+)>", name)
    if m:
        return guild.get_member(int(m.group(1)))
    # Plain user ID
    if name.isdigit():
        return guild.get_member(int(name))
    # Strip leading @ if user typed "@matze"
    if name.startswith("@"):
        name = name[1:]
    low = name.lower()
    # 1. Exact match on display name, global name, or username
    for member in guild.members:
        if (member.display_name.lower() == low
                or member.name.lower() == low
                or (member.global_name or "").lower() == low):
            return member
    # 2. Starts-with match
    for member in guild.members:
        if (member.display_name.lower().startswith(low)
                or member.name.lower().startswith(low)):
            return member
    # 3. Contains match
    for member in guild.members:
        if low in member.display_name.lower() or low in member.name.lower():
            return member
    return None


class GrantAccessSelect(discord.ui.View):
    """Ephemeral view with a UserSelect — shown when owner clicks 'Zugriff gewähren'."""

    def __init__(self, lang: str = "de"):
        super().__init__(timeout=60)
        self.lang = lang
        placeholder = "Spieler auswählen…" if lang == "de" else "Select a member…"
        select = discord.ui.UserSelect(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            custom_id="private_grant_select",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        lang = self.lang
        selected: discord.Member = interaction.data["resolved"]["members"]
        # interaction.data gives us the member via the select values
        user_id = interaction.data["values"][0]
        member = interaction.guild.get_member(int(user_id))
        if not member:
            await interaction.response.send_message(
                "❌ Mitglied nicht gefunden." if lang == "de" else "❌ Member not found.", ephemeral=True
            )
            return
        overwrite = interaction.channel.overwrites_for(member)
        if overwrite.view_channel is True:
            await interaction.response.send_message(
                t(lang, "private.grant.already", mention=member.mention), ephemeral=True
            )
            return
        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True)
        await interaction.response.send_message(
            t(lang, "private.grant.success", mention=member.mention)
        )


class RevokeAccessSelect(discord.ui.View):
    """Ephemeral view with a UserSelect — shown when owner clicks 'Zugriff entziehen'."""

    def __init__(self, lang: str = "de"):
        super().__init__(timeout=60)
        self.lang = lang
        placeholder = "Zugriff entziehen für…" if lang == "de" else "Revoke access for…"
        select = discord.ui.UserSelect(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            custom_id="private_revoke_select",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        lang = self.lang
        user_id = interaction.data["values"][0]
        member = interaction.guild.get_member(int(user_id))
        if not member:
            await interaction.response.send_message(
                "❌ Mitglied nicht gefunden." if lang == "de" else "❌ Member not found.", ephemeral=True
            )
            return
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(
            t(lang, "private.revoke.success", mention=member.mention)
        )


class PrivateChannelView(discord.ui.View):
    """Persistent view pinned in a private channel — owner can grant/revoke access."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="➕ Zugriff gewähren",
        style=discord.ButtonStyle.success,
        custom_id="persistent:private_grant",
    )
    async def grant_access(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        rec = await database.get_private_channel_by_channel_id(str(interaction.channel.id))
        if not rec or str(interaction.user.id) != rec["owner_id"]:
            await interaction.response.send_message(t(lang, "private.grant.not_owner"), ephemeral=True)
            return
        # Send ephemeral UserSelect picker
        view = GrantAccessSelect(lang=lang)
        label = "Wähle ein Mitglied aus der Liste:" if lang == "de" else "Select a member from the list:"
        await interaction.response.send_message(label, view=view, ephemeral=True)

    @discord.ui.button(
        label="➖ Zugriff entziehen",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:private_revoke",
    )
    async def revoke_access(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = await get_guild_lang(str(interaction.guild_id))
        rec = await database.get_private_channel_by_channel_id(str(interaction.channel.id))
        if not rec or str(interaction.user.id) != rec["owner_id"]:
            await interaction.response.send_message(t(lang, "private.revoke.not_owner"), ephemeral=True)
            return
        view = RevokeAccessSelect(lang=lang)
        label = "Wähle ein Mitglied aus der Liste:" if lang == "de" else "Select a member from the list:"
        await interaction.response.send_message(label, view=view, ephemeral=True)


async def _get_or_create_private_category(guild: discord.Guild, lang: str) -> discord.CategoryChannel:
    """Return or create the 'Privat Channels' category.

    The category itself is visible to everyone so members can see the channels
    they personally have access to. Each individual channel has its own strict
    overwrites (view_channel=False for @everyone), so only the owner and
    explicitly granted members can see each channel inside.
    """
    cat_name = t(lang, "private.category_name")
    for cat in guild.categories:
        if cat.name.lower() == cat_name.lower():
            return cat
    # Category visible to all (so members can find their own channels),
    # but channels inside will deny @everyone individually.
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True, send_messages=True),
    }
    return await guild.create_category(cat_name, overwrites=overwrites)


# ---------------------------------------------------------------------------
# Private Channel Name Modal
# ---------------------------------------------------------------------------

class PrivateChannelNameModal(discord.ui.Modal):
    """Ask user for a channel name (usually their Travian player name)."""

    channel_label = discord.ui.TextInput(
        label="Channel-Name / Travian-Spielername",
        placeholder="z.B. Currax",
        max_length=40,
    )

    def __init__(self, lang: str = "de"):
        title = "🔒 Privaten Channel erstellen" if lang == "de" else "🔒 Create Private Channel"
        super().__init__(title=title)
        self.lang = lang

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await _do_create_private_channel(interaction, self.channel_label.value.strip())
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await interaction.followup.send(f"⚠️ Fehler: {e}", ephemeral=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Request Hub View (7 buttons, persistent)
# ---------------------------------------------------------------------------

class RequestHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Scout", emoji="🔍", style=discord.ButtonStyle.primary,
        custom_id="persistent:hub_scout", row=0,
    )
    async def hub_scout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(ScoutHubModal(corn=False, permanent=False))

    @discord.ui.button(
        label="Kornspäh", emoji="🌾", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_corn", row=0,
    )
    async def hub_corn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(ScoutHubModal(corn=True, permanent=False))

    @discord.ui.button(
        label="Permanent-Scout", emoji="📡", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_perm_scout", row=0,
    )
    async def hub_perm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        await interaction.response.send_modal(ScoutHubModal(corn=False, permanent=True))

    @discord.ui.button(
        label="Res-Push", emoji="🪖", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_res_push", row=1,
    )
    async def hub_res_push(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResPushHubModal())

    @discord.ui.button(
        label="Defend", emoji="🛡️", style=discord.ButtonStyle.danger,
        custom_id="persistent:hub_defend", row=1,
    )
    async def hub_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DefendModal())

    @discord.ui.button(
        label="Timed-Defend", emoji="⏱️", style=discord.ButtonStyle.danger,
        custom_id="persistent:hub_timed_defend", row=1,
    )
    async def hub_timed_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TimedDefendModal())

    @discord.ui.button(
        label="Privater Channel", emoji="🔒", style=discord.ButtonStyle.secondary,
        custom_id="persistent:hub_private_channel", row=2,
    )
    async def hub_private_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_premium(interaction):
            return
        lang = await get_guild_lang(str(interaction.guild_id))
        await interaction.response.send_modal(PrivateChannelNameModal(lang=lang))

    async def _create_private_channel(self, interaction: discord.Interaction, channel_label: str):
        await _do_create_private_channel(interaction, channel_label)


async def _do_create_private_channel(interaction: discord.Interaction, channel_label: str):
    """Standalone helper — called from both PrivateChannelNameModal and RequestHubView."""
    guild = interaction.guild
    lang = await get_guild_lang(str(guild.id))

    # Check if the user already has a private channel
    existing = await database.get_private_channel(str(guild.id), str(interaction.user.id))
    if existing:
        ch = guild.get_channel(int(existing["channel_id"]))
        if ch:
            await interaction.followup.send(
                t(lang, "private.already_exists", channel=ch.mention), ephemeral=True
            )
            return
        # Channel was deleted externally — clean up and recreate
        await database.delete_private_channel_by_id(existing["channel_id"])

    config = await database.get_guild_config(str(guild.id))

    # Get or create 'Privat Channels' category
    category = await _get_or_create_private_category(guild, lang)

    # Build permission overwrites
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            embed_links=True, manage_channels=True,
        ),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, attach_files=True,
        ),
    }
    # Grant access to configured private-channel roles (Lead, Co-Lead…)
    # Falls back to allowed_role_ids if private_channel_role_ids is not set.
    priv_role_ids = ((config or {}).get("private_channel_role_ids") or "").strip()
    role_source = priv_role_ids if priv_role_ids else ((config or {}).get("allowed_role_ids") or "")
    for role_id_str in role_source.split(","):
        role_id_str = role_id_str.strip()
        if not role_id_str:
            continue
        role = guild.get_role(int(role_id_str))
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True,
            )

    channel_name = f"private-{_safe(channel_label)}"[:100]
    print(f"[hub] Creating private channel '{channel_name}' for {interaction.user} in guild {guild.id}", flush=True)

    new_channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        topic=f"private:{interaction.user.id}",
        overwrites=overwrites,
    )

    # Save to DB
    await database.set_private_channel(str(guild.id), str(interaction.user.id), str(new_channel.id))

    # Post welcome embed with grant/revoke buttons
    embed = discord.Embed(
        title=t(lang, "private.welcome_title", user=interaction.user.display_name),
        description=t(lang, "private.welcome_desc"),
        color=discord.Color.from_rgb(124, 58, 237),
    )
    embed.set_footer(**travops_footer(interaction.user.display_name))
    msg = await new_channel.send(
        content=interaction.user.mention,
        embed=embed,
        view=PrivateChannelView(),
    )
    try:
        await msg.pin()
    except Exception:
        pass

    await interaction.followup.send(
        t(lang, "private.created", channel=new_channel.mention), ephemeral=True
    )


# ---------------------------------------------------------------------------
# Hub Cog
# ---------------------------------------------------------------------------

class Hub(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """Mark defend/private channel as closed when deleted in Discord."""
        await database.close_defend_channel(str(channel.id))
        await database.delete_private_channel_by_id(str(channel.id))

    @commands.Cog.listener()
    async def on_ready(self):
        """Restore persistent hub views on startup."""
        print("[hub] RequestHubView + PrivateChannelView registered.", flush=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hub(bot))
    bot.add_view(RequestHubView())
    bot.add_view(DefendCloseView())
    bot.add_view(PrivateChannelView())
    bot.add_view(HubResAnswerView())
