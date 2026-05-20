import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

import database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _delete_channel_after(channel: discord.TextChannel, delay: int = 120):
    await asyncio.sleep(delay)
    try:
        await channel.delete(reason="Scout channel closed")
    except discord.NotFound:
        pass
    except Exception as e:
        print(f"[scout] Failed to delete channel {channel.id}: {e}")


async def _do_close(interaction: discord.Interaction, label: str):
    await interaction.message.edit(view=_all_disabled_view())
    await interaction.response.send_message(
        f"🔒 **{label}** by {interaction.user.mention}.\n"
        "This channel will be **deleted in 2 minutes**."
    )
    asyncio.create_task(_delete_channel_after(interaction.channel, delay=120))


def _all_disabled_view(taken_label: str = "Taken by") -> discord.ui.View:
    """All buttons disabled — used as final state after cancel/close."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=taken_label, style=discord.ButtonStyle.success, disabled=True))
    view.add_item(discord.ui.Button(label="Can't do this job", style=discord.ButtonStyle.secondary, disabled=True))
    view.add_item(discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, disabled=True))
    view.add_item(discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary, disabled=True))
    return view


# ---------------------------------------------------------------------------
# View: job taken — "Can't do this job" still active to release
# ---------------------------------------------------------------------------

class ScoutTakenView(discord.ui.View):
    """Shown after someone claims the job. Can still be released."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Taken by …",
        style=discord.ButtonStyle.success,
        disabled=True,
        custom_id="persistent:scout_taken_label",
    )
    async def taken_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # disabled — never fires

    @discord.ui.button(
        label="Can't do this job",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_release",
    )
    async def cant_do(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Release the job back to open."""
        await interaction.message.edit(view=ScoutActionView())
        await interaction.response.send_message(
            f"↩️ {interaction.user.mention} can't do this job. The request is **open again**!"
        )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:scout_taken_cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_close(interaction, "Scout request cancelled")

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_taken_close",
    )
    async def close_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_close(interaction, "Scout channel closed")


# ---------------------------------------------------------------------------
# View: initial action buttons
# ---------------------------------------------------------------------------

class ScoutActionView(discord.ui.View):
    """Persistent view attached to the info message in each scout channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Taken by",
        style=discord.ButtonStyle.success,
        custom_id="persistent:scout_taken",
    )
    async def taken_by(self, interaction: discord.Interaction, button: discord.ui.Button):
        taken_view = ScoutTakenView()
        taken_view.taken_label.label = f"Taken by {interaction.user.display_name}"
        await interaction.message.edit(view=taken_view)
        await interaction.response.send_message(
            f"✋ **{interaction.user.mention}** has taken this scout job!"
        )

    @discord.ui.button(
        label="Can't do this job",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_cant",
    )
    async def cant_do(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"❌ **{interaction.user.mention}** can't do this job. Still looking for a scout..."
        )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:scout_cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_close(interaction, "Scout request cancelled")

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:scout_close",
    )
    async def close_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _do_close(interaction, "Scout channel closed")


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------

class ScoutModal(discord.ui.Modal, title="Scout Request"):
    coordinates = discord.ui.TextInput(label="Coordinates", placeholder="e.g. 500|500", required=True, max_length=50)
    player = discord.ui.TextInput(label="Player", placeholder="Player name", required=True, max_length=100)
    village = discord.ui.TextInput(label="Village", placeholder="Village name", required=True, max_length=100)
    time = discord.ui.TextInput(label="Time", placeholder="e.g. 14:30 UTC", required=True, max_length=50)
    additional_info = discord.ui.TextInput(
        label="Additional Info", placeholder="Any additional information...",
        required=False, style=discord.TextStyle.paragraph, max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        config = await database.get_guild_config(str(guild.id))

        if not config or not config.get("category_id"):
            await interaction.response.send_message(
                "⚠️ The bot is not fully configured yet. Ask an admin to set it up in the web panel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        category = guild.get_channel(int(config["category_id"]))
        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send("⚠️ Configured category not found.", ephemeral=True)
            return

        # Build channel permissions
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True, manage_channels=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }
        for role_id_str in (config.get("allowed_role_ids") or "").split(","):
            role_id_str = role_id_str.strip()
            if not role_id_str:
                continue
            role = guild.get_role(int(role_id_str))
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True)

        safe_player = re.sub(r"[^a-z0-9]", "-", self.player.value.lower())
        safe_coords = self.coordinates.value.replace("|", "-").replace(" ", "")
        channel_name = f"scout-{safe_player}-{safe_coords}"[:100]

        new_channel = await guild.create_text_channel(
            name=channel_name, category=category,
            topic=f"Scout: {self.player.value} @ {self.coordinates.value}",
            overwrites=overwrites,
        )

        await database.add_scout_channel(
            channel_id=str(new_channel.id), guild_id=str(guild.id),
            player=self.player.value, coordinates=self.coordinates.value,
            village=self.village.value, scout_time=self.time.value,
            additional_info=self.additional_info.value or "",
            requested_by_id=str(interaction.user.id),
            requested_by_name=interaction.user.display_name,
        )

        embed = discord.Embed(title="📡 Scout Request", color=discord.Color.blurple())
        embed.add_field(name="Player", value=self.player.value, inline=True)
        embed.add_field(name="Village", value=self.village.value, inline=True)
        embed.add_field(name="Coordinates", value=self.coordinates.value, inline=True)
        embed.add_field(name="Time", value=self.time.value, inline=True)
        if self.additional_info.value:
            embed.add_field(name="Additional Info", value=self.additional_info.value, inline=False)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")

        await new_channel.send(
            content=f"New scout request from {interaction.user.mention}",
            embed=embed,
            view=ScoutActionView(),
        )
        await interaction.followup.send(f"✅ Scout channel created: {new_channel.mention}", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        try:
            await interaction.followup.send("❌ Something went wrong.", ephemeral=True)
        except Exception:
            pass
        raise error


# ---------------------------------------------------------------------------
# Persistent Scout Request button
# ---------------------------------------------------------------------------

class ScoutRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Scout Request", style=discord.ButtonStyle.primary,
        emoji="🔍", custom_id="persistent:scout_request",
    )
    async def scout_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScoutModal())


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Scout(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup-scout", description="Post the Scout Request button in this channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_scout(self, interaction: discord.Interaction):
        config = await database.get_guild_config(str(interaction.guild.id))
        if not config or not config.get("category_id") or not config.get("archive_channel_id"):
            await interaction.response.send_message(
                "⚠️ Please configure **Category ID** and **Archive Channel ID** in the web admin panel first.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="📡 Scout Request",
            description="Click the button below to submit a scout request.\nFill in the coordinates, player, village and time.",
            color=discord.Color.blurple(),
        )
        msg = await interaction.channel.send(embed=embed, view=ScoutRequestView())
        await database.update_scout_channel_and_button(
            guild_id=str(interaction.guild.id),
            scout_channel_id=str(interaction.channel.id),
            button_message_id=str(msg.id),
        )
        await interaction.response.send_message("✅ Scout Request button posted!", ephemeral=True)


async def setup(bot: commands.Bot):
    bot.add_view(ScoutRequestView())
    bot.add_view(ScoutActionView())
    bot.add_view(ScoutTakenView())
    await bot.add_cog(Scout(bot))
