"""
digest.py — Weekly Alliance Digest

Every Monday at 07:00 UTC the bot posts a summary of the past 7 days
to the configured digest channel. Can also be triggered manually via /digest.
"""
import asyncio
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils import travops_footer


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_n(n: int | float) -> str:
    """Format a number with thousand separators (German style)."""
    return f"{int(n):,}".replace(",", ".")


def _medal(pos: int) -> str:
    medals = ["🥇", "🥈", "🥉"]
    return medals[pos] if pos < 3 else "▪️"


async def build_digest_embed(guild_id: str, guild_name: str, week_label: str) -> discord.Embed:
    since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    s = await database.get_weekly_stats(guild_id, since)

    # ── Colour: green if active week, grey if quiet ───────────────────────────
    activity = s["battle_total"] + s["scout_requests"] + s["defends"]
    color = discord.Color.from_rgb(34, 197, 94) if activity >= 5 else discord.Color.from_rgb(100, 116, 139)

    embed = discord.Embed(
        title=f"📰 Wöchentlicher Allianz-Digest",
        description=f"**{guild_name}** · {week_label}",
        color=color,
    )

    # ── Activity overview ─────────────────────────────────────────────────────
    _TYPE_ICON = {"attack": "⚔️", "defense": "🛡️", "spy": "👁️"}
    lines = []
    if s["battle_total"]:
        types = s.get("battle_by_type", {})
        detail = "  ".join(
            f"{_TYPE_ICON.get(k, k)}: {v}"
            for k, v in types.items() if v
        )
        lines.append(f"⚔️ **{s['battle_total']}** Kampfberichte  {detail}")
    if s["plunder_total"]:
        lines.append(f"💰 **{_fmt_n(s['plunder_total'])}** Ressourcen erbeutet")
    if s["scout_reports"]:
        lines.append(f"🔍 **{s['scout_reports']}** Scout-Berichte eingegangen")
    if s["scout_requests"]:
        lines.append(f"📡 **{s['scout_requests']}** Scout-Anfragen erstellt")
    if s["defends"]:
        lines.append(f"🛡️ **{s['defends']}** Defend-Anfragen koordiniert")
    if s["res_pushes"]:
        lines.append(f"🪖 **{s['res_pushes']}** Res-Push-Anfragen")
    if s["handoffs_confirmed"]:
        lines.append(f"🏺 **{s['handoffs_confirmed']}** Artefakt-Übergaben bestätigt")

    if lines:
        embed.add_field(
            name="📊 Diese Woche",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="📊 Diese Woche",
            value="_Keine Aktivität erfasst._",
            inline=False,
        )

    # ── Top contributors ──────────────────────────────────────────────────────
    reporters = s.get("top_reporters", [])
    if reporters:
        top_lines = []
        for i, r in enumerate(reporters[:5]):
            name = r.get("submitted_by") or "Unbekannt"
            top_lines.append(f"{_medal(i)} **{name}** — {r['n']} Bericht{'e' if r['n'] != 1 else ''}")
        embed.add_field(
            name="🏆 Top Beitragende",
            value="\n".join(top_lines),
            inline=True,
        )

    # ── Active artifacts ──────────────────────────────────────────────────────
    artifacts = s.get("active_artifacts", [])
    if artifacts:
        art_lines = []
        for a in artifacts[:6]:
            holder = a.get("current_holder") or "_frei_"
            art_lines.append(f"🏺 **{a['name']}** → {holder}")
        embed.add_field(
            name="🏺 Artefakte",
            value="\n".join(art_lines),
            inline=True,
        )

    # ── Upcoming handoffs ─────────────────────────────────────────────────────
    upcoming = s.get("upcoming_handoffs", [])
    if upcoming:
        hoff_lines = []
        for h in upcoming[:4]:
            sched = (h.get("scheduled_at") or "")[:16]
            hoff_lines.append(
                f"📤 **{h['from_player']}** → {h['to_player']}"
                + (f"  ⏰ {sched}" if sched else "")
            )
        embed.add_field(
            name="⏰ Nächste Übergaben",
            value="\n".join(hoff_lines),
            inline=False,
        )

    # ── Motivational footer ───────────────────────────────────────────────────
    quotes = [
        "Gemeinsam stark — weiter so! 💪",
        "Jede Woche besser als die letzte. 🚀",
        "Die Allianz schläft nicht. ⚔️",
        "Zusammen unbesiegbar. 🏰",
        "Danke an alle aktiven Mitglieder! 🙏",
    ]
    import hashlib
    qi = int(hashlib.md5(week_label.encode()).hexdigest(), 16) % len(quotes)
    embed.set_footer(text=f"{quotes[qi]} · travops.online")

    return embed


async def send_digest(bot: commands.Bot, guild_id: str, channel_id: str):
    """Build and post the digest embed to a channel."""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return False

    channel = guild.get_channel(int(channel_id))
    if not channel:
        try:
            channel = await guild.fetch_channel(int(channel_id))
        except Exception:
            return False

    now = datetime.utcnow()
    week_label = f"KW {now.isocalendar()[1]} · {(now - timedelta(days=7)).strftime('%d.%m')}–{now.strftime('%d.%m.%Y')}"
    embed = await build_digest_embed(guild_id, guild.name, week_label)

    await channel.send(embed=embed)
    await database.mark_digest_sent(guild_id, now.isoformat())
    print(f"[digest] Posted weekly digest for guild {guild_id} → #{channel.name}", flush=True)
    return True


# ── Cog ──────────────────────────────────────────────────────────────────────

class Digest(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._digest_task: asyncio.Task | None = None

    @commands.Cog.listener()
    async def on_ready(self):
        if self._digest_task is None or self._digest_task.done():
            self._digest_task = self.bot.loop.create_task(self._weekly_loop())
            print("[digest] Weekly digest loop started.", flush=True)

    async def _weekly_loop(self):
        """Check every 30 min: if it's Monday 07:xx UTC and not yet sent this week → post."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                now = datetime.utcnow()
                # Monday = 0, fire between 07:00 and 07:59
                if now.weekday() == 0 and now.hour == 7:
                    for guild in self.bot.guilds:
                        gid = str(guild.id)
                        ch_id = await database.get_set_digest_channel(gid)
                        if not ch_id:
                            continue
                        last = await database.get_digest_last_sent(gid)
                        # Only send once per week (check if last sent was before this Monday 00:00)
                        this_monday = now - timedelta(days=now.weekday())
                        this_monday = this_monday.replace(hour=0, minute=0, second=0, microsecond=0)
                        already_sent = False
                        if last:
                            try:
                                last_dt = datetime.fromisoformat(last)
                                already_sent = last_dt >= this_monday
                            except Exception:
                                pass
                        if not already_sent:
                            await send_digest(self.bot, gid, ch_id)
            except Exception as e:
                print(f"[digest] Weekly loop error: {e}", flush=True)
            await asyncio.sleep(1800)  # check every 30 min

    @app_commands.command(name="digest", description="Wöchentlichen Allianz-Digest jetzt posten")
    @app_commands.describe(channel="Channel für den Digest (optional, speichert als Standard)")
    async def cmd_digest(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        """Manual trigger: /digest [#channel]"""
        if not interaction.user.guild_permissions.administrator:
            perms = await database.get_member_permissions(
                str(interaction.guild_id), str(interaction.user.id)
            )
            if "ally_manage" not in perms:
                await interaction.response.send_message(
                    "⛔ Nur Admins oder Nutzer mit `ally_manage` können den Digest posten.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)

        gid = str(interaction.guild_id)

        # Use provided channel, or fall back to configured one
        target_ch = channel
        if target_ch:
            await database.get_set_digest_channel(gid, str(target_ch.id))
        else:
            ch_id = await database.get_set_digest_channel(gid)
            if not ch_id:
                await interaction.followup.send(
                    "❌ Kein Digest-Channel konfiguriert. Nutze `/digest #channel` um einen festzulegen.",
                    ephemeral=True,
                )
                return
            target_ch = interaction.guild.get_channel(int(ch_id))
            if not target_ch:
                await interaction.followup.send("❌ Konfigurierter Channel nicht gefunden.", ephemeral=True)
                return

        ok = await send_digest(self.bot, gid, str(target_ch.id))
        if ok:
            await interaction.followup.send(
                f"✅ Digest gepostet in {target_ch.mention}!", ephemeral=True
            )
        else:
            await interaction.followup.send("❌ Fehler beim Posten des Digests.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Digest(bot))
