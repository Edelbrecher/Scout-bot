import json
import re
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils import require_premium, PREMIUM_STATUSES


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_travian_attacks(text: str) -> list[dict]:
    """
    Parse Travian Legends rally point copy-paste (German UI).

    Real format from Versammlungsplatz (copy Strg+A / Strg+C):
        {atk_village}\t[Angriff markieren]{player} {action} {def_village} aus
        ({x}|{y})\t{Troop1}\t{Troop2}\t...
        Einheiten\t{n1}\t{n2}\t...
        Ankunft\tin H:MM:SS Std.um HH:MM:SS
    """
    attacks = []

    # Strip Unicode bidirectional / invisible formatting chars that Travian embeds
    text = re.sub(r"[​-‏‪-‮⁦-⁩﻿]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    TROOP_NAMES = [
        "Legionär", "Prätorianer", "Imperianer",
        "Equites Legati", "Equites Imperatoris", "Equites Caesaris",
        "Rammbock", "Feuerkatapult", "Senator", "Siedler", "Held",
        # Teuton
        "Keulenschwinger", "Speerkämpfer", "Axtkämpfer", "Späher",
        "Paladin", "Teut. Ritter", "Häuptling",
        # Gaul
        "Phalanx", "Schwertkämpfer", "Pathfinder", "Theutates-Blitz",
        "Druidentreiter", "Haeduer", "Stammesältester",
    ]

    ACTION_TYPE = {
        "raubt": "raid",
        "greift an": "attack",
        "greift": "attack",
        "verstärkt": "reinforce",
        "bespitzelt": "spy",
        "siedelt": "settle",
    }

    def clean_int(s: str) -> int:
        return int(re.sub(r"[^\d]", "", s)) if re.search(r"\d", s) else 0

    # ── Primary: real Travian rally point block ────────────────────────────
    # Line 1: atk_village TAB [Angriff markieren]player action def_village aus
    # Line 2: (x|y) TAB troop types
    # Line 3: Einheiten TAB counts
    # Line 4: Ankunft TAB arrival text
    block_re = re.compile(
        r"^([^\t\n]+?)\t"                                          # attacker village
        r"(?:Angriff\s*markieren)?"                                # optional UI button (no space before player)
        r"(.+?)\s+"                                                # player name
        r"(raubt|greift\s+an|greift|verstärkt|bespitzelt|siedelt)\s+"  # action keyword
        r"(.+?)\s+(?:aus|an)\s*\n"                                    # defender village + "aus"/"an"
        r"\((-?\d+)\|(-?\d+)\)[^\n]*\n"                           # (x|y) coords line
        r"Einheiten\t([^\n]+)\n"                                   # Einheiten counts
        r"Ankunft\t([^\n]+)",                                      # Ankunft arrival
        re.IGNORECASE | re.MULTILINE,
    )

    for m in block_re.finditer(text):
        atk_village = m.group(1).strip()
        player      = m.group(2).strip()
        action_raw  = m.group(3).strip().lower()
        def_village = m.group(4).strip()
        x, y        = m.group(5), m.group(6)
        units_raw   = m.group(7).strip()
        arrival_raw = m.group(8).strip()

        wave_type = next((wt for kw, wt in ACTION_TYPE.items() if kw in action_raw), "attack")

        # Parse troop counts
        counts = units_raw.split("\t")
        troops = {TROOP_NAMES[i]: clean_int(c) for i, c in enumerate(counts)
                  if i < len(TROOP_NAMES) and clean_int(c) > 0}

        # Clean arrival: "in 8:39:53 Std.um 09:52:17" → readable
        arrival = arrival_raw.replace("Std.", "Std. ").strip()

        attacks.append({
            "attacker":         player,
            "attacker_village": atk_village,
            "village":          def_village,
            "coords":           f"({x}|{y})",
            "arrival":          arrival,
            "wave_type":        wave_type,
            "troops":           troops,
        })

    if attacks:
        return attacks

    # ── Fallback: simpler scan for "Ankunft" lines and surrounding context ─
    # Handles edge cases where the block structure is slightly different
    arrival_re = re.compile(
        r"^([^\t\n]*(?:Angriff\s*markieren|raubt|greift)[^\n]*)\n"
        r"(?:\([^\n]*\n)?"          # coords + optional troop type names
        r"(?:[^\t\n]*\t[^\n]*\n)?"  # optional extra header line (troop names row)
        r"(?:Einheiten[^\n]*\n)?"
        r"Ankunft\t([^\n]+)",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in arrival_re.finditer(text):
        context = m.group(1)
        arrival = m.group(2).strip().replace("Std.", "Std. ")
        player_m = re.search(r"(?:markieren)(.+?)\s+(?:raubt|greift)", context, re.IGNORECASE)
        player = player_m.group(1).strip() if player_m else "Unbekannt"
        coords_m = re.search(r"\((-?\d+)\|(-?\d+)\)", context)
        coords = coords_m.group(0) if coords_m else ""
        attacks.append({
            "attacker": player,
            "attacker_village": "",
            "village": "",
            "coords": coords,
            "arrival": arrival,
            "wave_type": "attack",
            "troops": {},
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
        try:
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

            TYPE_EMOJI = {"raid": "🪖", "attack": "⚔️", "reinforce": "🛡️", "spy": "🕵️", "settle": "🏘️"}
            for i, atk in enumerate(attacks[:10], 1):
                wt = atk.get("wave_type", "attack")
                emoji = TYPE_EMOJI.get(wt, "⚔️")
                name = f"{emoji} Angriff {i}"
                parts = []
                if atk.get("attacker"):
                    parts.append(f"**Angreifer:** {atk['attacker']}")
                if atk.get("attacker_village"):
                    parts.append(f"**Von:** {atk['attacker_village']} {atk.get('coords','')}")
                if atk.get("village"):
                    parts.append(f"**Ziel:** {atk['village']}")
                if atk.get("arrival"):
                    parts.append(f"**Ankunft:** {atk['arrival']}")
                troops = atk.get("troops", {})
                if troops:
                    troop_str = " · ".join(f"{v}× {k}" for k, v in list(troops.items())[:5])
                    parts.append(f"**Truppen:** {troop_str}")
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
        except Exception as e:
            print(f"[attacks] on_submit error: {e}", flush=True)
            try:
                await interaction.response.send_message(
                    f"❌ Fehler beim Verarbeiten: {e}",
                    ephemeral=True,
                )
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"[attacks] Modal error: {error}", flush=True)
        try:
            await interaction.response.send_message(
                f"❌ Interner Fehler: {error}",
                ephemeral=True,
            )
        except Exception:
            pass


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
                # Skip non-premium guilds on startup restore
                sub_status = await database.get_subscription_status(guild_id)
                if sub_status not in PREMIUM_STATUSES:
                    continue
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
        if not await require_premium(interaction):
            return
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
