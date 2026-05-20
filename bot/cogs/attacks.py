import json
import re
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import database


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_travian_attacks(text: str) -> list[dict]:
    """
    Parse Travian Legends rally point copy-paste (German UI).
    Returns list of dicts: {attacker, village, coords, arrival, wave}.
    Tries multiple patterns to be robust against format variations.
    """
    attacks = []

    # Normalize: collapse multiple spaces/tabs to single tab, strip CR
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Pattern A: tab-separated row
    # \t(Angriff|Welle N)\tPlayerName\tVillageName (X|Y)\tArrival
    pattern_full = re.compile(
        r"\t(Angriff|Welle\s*(\d+))\t([^\t]+?)\t([^\t(]*?\([^\t)]*\)[^\t]*?)\t([^\t\n]+)",
        re.IGNORECASE,
    )
    for m in pattern_full.finditer(text):
        wave_str, wave_num, attacker, village_coords, arrival = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        # Extract coords from village_coords
        coords_match = re.search(r"\((-?\d+)\|(-?\d+)\)", village_coords)
        coords = coords_match.group(0) if coords_match else ""
        village = re.sub(r"\s*\([-\d|]+\)\s*$", "", village_coords).strip()
        wave = int(wave_num) if wave_num else None
        attacks.append({
            "attacker": attacker.strip(),
            "village": village,
            "coords": coords,
            "arrival": arrival.strip(),
            "wave": wave,
        })

    if attacks:
        return attacks

    # Pattern B: lines without leading tab — "Angriff  PlayerName  Village (X|Y)  Arrival"
    pattern_b = re.compile(
        r"^[ \t]*(Angriff|Welle\s*(\d+))[ \t]+([^\t(]+?)[ \t]+([^\t(]*\([-\d|]+\)[^\t]*)[ \t]+([^\t\n]+)$",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in pattern_b.finditer(text):
        wave_str, wave_num, attacker, village_coords, arrival = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        coords_match = re.search(r"\((-?\d+)\|(-?\d+)\)", village_coords)
        coords = coords_match.group(0) if coords_match else ""
        village = re.sub(r"\s*\([-\d|]+\)\s*$", "", village_coords).strip()
        wave = int(wave_num) if wave_num else None
        attacks.append({
            "attacker": attacker.strip(),
            "village": village,
            "coords": coords,
            "arrival": arrival.strip(),
            "wave": wave,
        })

    if attacks:
        return attacks

    # Pattern C: minimal — just attacker + arrival, coords optional
    pattern_c = re.compile(
        r"(Angriff|Welle\s*(\d+))[\t ]+([^\t\n]+?)[\t ]+(?:[^\t\n]*?\([-\d|]+\)[^\t\n]*?[\t ]+)?([^\t\n]*(?:heute|morgen|\d{1,2}\.\d{1,2}\.)[\s\S]*?(?:\d{2}:\d{2}:\d{2}|\d{2}:\d{2}))",
        re.IGNORECASE,
    )
    for m in pattern_c.finditer(text):
        wave_str, wave_num, attacker, arrival = m.group(1), m.group(2), m.group(3), m.group(4)
        wave = int(wave_num) if wave_num else None
        attacks.append({
            "attacker": attacker.strip(),
            "village": "",
            "coords": "",
            "arrival": arrival.strip(),
            "wave": wave,
        })

    return attacks


# ---------------------------------------------------------------------------
# Modal
# ---------------------------------------------------------------------------

class AttackModal(discord.ui.Modal, title="Angriff melden"):
    attack_text = discord.ui.TextInput(
        label="Truppenplatz kopieren",
        style=discord.TextStyle.paragraph,
        placeholder="Strg+A → Strg+C im Truppenplatz, dann hier einfügen",
        required=True,
        custom_id="attack_text",
        max_length=4000,
    )

    def __init__(self, alert_channel_id: str):
        super().__init__(custom_id="attack_modal")
        self.alert_channel_id = alert_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.attack_text.value
        attacks = parse_travian_attacks(raw)

        if not attacks:
            await interaction.response.send_message(
                "❌ Keine Angriffe erkannt. Stelle sicher, dass du den kompletten Truppenplatz-Text (Strg+A + Strg+C) eingefügt hast.",
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild_id)
        reporter_id = str(interaction.user.id)
        reporter_name = interaction.user.display_name
        attacks_json_str = json.dumps(attacks, ensure_ascii=False)

        report_id = await database.save_attack_report(
            guild_id, reporter_id, reporter_name, raw, attacks_json_str
        )

        # Build embed
        embed = discord.Embed(
            title="⚔️ Angriff gemeldet!",
            color=0xe74c3c,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Gemeldet von", value=interaction.user.mention, inline=True)
        embed.add_field(name="Anzahl Angriffe", value=str(len(attacks)), inline=True)
        embed.set_footer(text=f"Report ID: {report_id}")

        for i, atk in enumerate(attacks[:10], 1):
            wave_label = f" (Welle {atk['wave']})" if atk.get("wave") else ""
            name = f"Angriff {i}{wave_label}"
            parts = []
            if atk.get("attacker"):
                parts.append(f"**Angreifer:** {atk['attacker']}")
            if atk.get("village"):
                parts.append(f"**Dorf:** {atk['village']}")
            if atk.get("coords"):
                parts.append(f"**Koordinaten:** {atk['coords']}")
            if atk.get("arrival"):
                parts.append(f"**Ankunft:** {atk['arrival']}")
            embed.add_field(name=name, value="\n".join(parts) or "—", inline=False)

        if len(attacks) > 10:
            embed.add_field(
                name="...",
                value=f"Und {len(attacks) - 10} weitere Angriffe (siehe Dashboard)",
                inline=False,
            )

        # Post to alert channel
        alert_channel = interaction.guild.get_channel(int(self.alert_channel_id))
        if alert_channel:
            await alert_channel.send(embed=embed)

        await interaction.response.send_message(
            f"✅ **{len(attacks)} Angriff(e)** wurden gemeldet und im Alarm-Kanal gepostet.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Persistent button view
# ---------------------------------------------------------------------------

class AttackReportView(discord.ui.View):
    def __init__(self, alert_channel_id: str):
        super().__init__(timeout=None)
        self.alert_channel_id = alert_channel_id

    @discord.ui.button(
        label="⚔️ Angriff melden",
        style=discord.ButtonStyle.danger,
        custom_id="report_attack",
    )
    async def report_attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild_id)
        channel_id = await database.get_attack_channel(guild_id)
        if not channel_id:
            await interaction.response.send_message(
                "❌ Kein Alarm-Kanal konfiguriert.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AttackModal(alert_channel_id=channel_id))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class AttacksCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register globally so any message with custom_id="report_attack" is handled
        bot.add_view(AttackReportView(alert_channel_id=""))

    @commands.Cog.listener()
    async def on_ready(self):
        """Restore persistent views for all guilds that have attack setup."""
        async with __import__("aiosqlite").connect(database.DB_PATH) as db:
            async with db.execute(
                "SELECT guild_id, attack_channel_id, attack_button_message_id FROM guild_configs "
                "WHERE attack_button_message_id IS NOT NULL AND attack_channel_id IS NOT NULL"
            ) as cur:
                rows = await cur.fetchall()

        for guild_id, channel_id, message_id in rows:
            try:
                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    continue
                channel = guild.get_channel(int(channel_id))
                if not channel:
                    continue
                try:
                    msg = await channel.fetch_message(int(message_id))
                    view = AttackReportView(alert_channel_id=channel_id)
                    self.bot.add_view(view, message_id=int(message_id))
                except discord.NotFound:
                    pass
            except Exception as e:
                print(f"[attacks] Failed to restore view for guild {guild_id}: {e}")

        print(f"[attacks] Restored persistent views for {len(rows)} guild(s).")

    @app_commands.command(name="attack-setup", description="Richtet den Angriff-Detection-Kanal ein (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def attack_setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        guild_id = str(guild.id)
        bot_member = guild.get_member(self.bot.user.id)

        # Create category
        overwrites_category = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        try:
            category = await guild.create_category(
                "Angriff-Detection", overwrites=overwrites_category
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Fehlende Berechtigungen um Kategorie zu erstellen.", ephemeral=True
            )
            return

        # Create alert channel inside category
        overwrites_channel = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        channel = await guild.create_text_channel(
            "angriff-alarm", category=category, overwrites=overwrites_channel
        )

        # Post persistent button
        view = AttackReportView(alert_channel_id=str(channel.id))
        embed = discord.Embed(
            title="⚔️ Angriff-Meldung",
            description=(
                "Siehst du eingehende Angriffe im Truppenplatz?\n\n"
                "**So meldest du einen Angriff:**\n"
                "1. Öffne den Truppenplatz in Travian\n"
                "2. Markiere alles (Strg+A) und kopiere (Strg+C)\n"
                "3. Klicke den Button unten und füge den Text ein\n"
            ),
            color=0xe74c3c,
        )
        msg = await channel.send(embed=embed, view=view)

        # Save to DB
        await database.set_attack_channel(guild_id, str(channel.id), str(msg.id))
        self.bot.add_view(view, message_id=msg.id)

        await interaction.followup.send(
            f"✅ Angriff-Detection eingerichtet!\n"
            f"Kanal: {channel.mention}\n"
            f"Kategorie: **{category.name}**",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AttacksCog(bot))
