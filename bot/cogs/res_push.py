import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

import database


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
# Helpers
# ---------------------------------------------------------------------------

def _build_request_embed(data: dict, status: str) -> discord.Embed:
    colors = {
        "pending": discord.Color.blue(),
        "hold": discord.Color.orange(),
        "accepted": discord.Color.green(),
        "rejected": discord.Color.red(),
    }
    status_labels = {
        "pending": "⏳ Pending",
        "hold": "⏸️ On Hold",
        "accepted": "✅ Accepted",
        "rejected": "❌ Rejected",
    }
    embed = discord.Embed(
        title="🪖 Res-Push Request",
        color=colors.get(status, discord.Color.default()),
    )
    embed.add_field(name="Player", value=data["player_name"], inline=True)
    embed.add_field(name="Coordinates", value=data["coordinates"], inline=True)
    embed.add_field(name="Push Height", value=data["push_height"], inline=True)
    if data.get("reason"):
        embed.add_field(name="Reason", value=data["reason"], inline=False)
    embed.add_field(name="Status", value=status_labels.get(status, status), inline=False)
    embed.set_footer(text=f"Requested by {data['user_name']} • {data['created_at'][:16]}")
    return embed


def _build_push_embed(data: dict, contributions: list[dict]) -> discord.Embed:
    total = sum(int(c["amount"]) for c in contributions if c["amount"].isdigit())
    try:
        target = int(data["push_height"])
        progress = min(int(total / target * 100), 100) if target > 0 else 0
        bar_filled = progress // 10
        progress_bar = "█" * bar_filled + "░" * (10 - bar_filled)
        progress_text = f"{progress_bar} {progress}%  ({total:,} / {target:,})"
    except (ValueError, ZeroDivisionError):
        progress_text = f"Sent so far: {total:,}"

    embed = discord.Embed(title="🪖 Res-Push", color=discord.Color.green())
    embed.add_field(name="Player", value=data["player_name"], inline=True)
    embed.add_field(name="Coordinates", value=data["coordinates"], inline=True)
    embed.add_field(name="Push Goal", value=data["push_height"], inline=True)
    embed.add_field(name="Progress", value=progress_text, inline=False)
    if contributions:
        contrib_lines = "\n".join(
            f"• **{c['user_name']}**: {int(c['amount']):,}" if c["amount"].isdigit()
            else f"• **{c['user_name']}**: {c['amount']}"
            for c in contributions[-10:]
        )
        embed.add_field(name="Contributions", value=contrib_lines, inline=False)
    embed.set_footer(text=f"Requested by {data['user_name']}")
    return embed


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
    amount = discord.ui.TextInput(
        label="Amount Sent", placeholder="e.g. 5000",
        max_length=20,
    )

    def __init__(self, request_id: int):
        super().__init__()
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        await database.add_res_contribution(
            request_id=self.request_id,
            user_id=str(interaction.user.id),
            user_name=interaction.user.display_name,
            amount=self.amount.value.strip(),
        )

        req = await database.get_res_request_by_id(self.request_id)
        contribs = await database.get_res_contributions(self.request_id)
        if req and interaction.message:
            await interaction.message.edit(embed=_build_push_embed(req, contribs))

        await interaction.followup.send(
            f"✅ Recorded: **{self.amount.value}** sent. Thanks!", ephemeral=True
        )


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
        if not config or not config.get("res_push_channel_id"):
            await interaction.response.send_message("⚠️ Res-Push channel not configured.", ephemeral=True)
            return

        await interaction.response.defer()

        push_channel = interaction.guild.get_channel(int(config["res_push_channel_id"]))
        if not push_channel:
            await interaction.followup.send("⚠️ Res-push channel not found.", ephemeral=True)
            return

        push_embed = _build_push_embed(req, [])
        push_msg = await push_channel.send(embed=push_embed, view=ResPushView())

        await database.update_res_request_status(
            answer_message_id=str(interaction.message.id),
            status="accepted",
            push_message_id=str(push_msg.id),
        )

        updated = _build_request_embed(req, "accepted")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Accepted", style=discord.ButtonStyle.success, disabled=True))
        await interaction.message.edit(
            content=f"✅ Accepted by {interaction.user.mention}",
            embed=updated, view=view,
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
            embed=updated,
            view=ResAnswerView(),
        )


class ResPushView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="I sent", style=discord.ButtonStyle.primary,
        emoji="📦", custom_id="persistent:res_sent",
    )
    async def i_sent(self, interaction: discord.Interaction, button: discord.ui.Button):
        req = await database.get_res_request_by_push_msg(str(interaction.message.id))
        if not req:
            await interaction.response.send_message("⚠️ Request not found.", ephemeral=True)
            return
        await interaction.response.send_modal(ResSentModal(request_id=req["id"]))


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
        if not config or not config.get("res_answer_channel_id") or not config.get("res_push_channel_id"):
            await interaction.response.send_message(
                "⚠️ Please configure all Res-Push channel IDs in the web admin panel first.",
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
    bot.add_view(ResPushView())
    await bot.add_cog(ResPush(bot))
