import asyncio
import datetime
import discord
from discord.ext import commands

import database


class ArtifactRotation(commands.Cog):
    """Clock-driven artifact rotation. DMs each player on turn-start and before
    they must pass the artifact on. The current holder advances automatically."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None

    @commands.Cog.listener()
    async def on_ready(self):
        if self._task is None or self._task.done():
            self._task = self.bot.loop.create_task(self._loop())
            print("[rotation] Auto-rotation loop started.", flush=True)

    async def _loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._tick()
            except Exception as e:
                print(f"[rotation] loop error: {e}", flush=True)
            await asyncio.sleep(600)  # every 10 min

    async def _dm(self, discord_id: str, embed: discord.Embed) -> bool:
        try:
            user = self.bot.get_user(int(discord_id)) or await self.bot.fetch_user(int(discord_id))
            if user:
                await user.send(embed=embed)
                return True
        except Exception as e:
            print(f"[rotation] DM to {discord_id} failed: {e}", flush=True)
        return False

    async def _channel_post(self, guild, channel_id: str, content: str):
        if not channel_id:
            return
        try:
            ch = guild.get_channel(int(channel_id))
            if ch:
                await ch.send(content)
        except Exception as e:
            print(f"[rotation] channel post failed: {e}", flush=True)

    async def _tick(self):
        rotations = await database.get_active_rotations()
        for art in rotations:
            aid = art["id"]
            gid = art["guild_id"]
            started = art.get("rotation_started_at") or ""
            if not started:
                continue
            players = await database.get_rotation_players(aid, gid)
            if not players:
                continue
            state = database.compute_rotation_state(players, started)
            if not state:
                continue

            guild = self.bot.get_guild(int(gid))
            if not guild:
                continue

            win = state["window_number"]
            ch_id = art.get("rotation_channel_id") or ""
            name = art.get("name", "Artifact")
            last_start = art.get("rot_last_start_window", -1)
            last_notify = art.get("rot_last_notify_window", -1)

            cur_name = state["current_player"]
            next_name = state["next_player"]
            end_label = state["window_end"][:16].replace("T", " ")

            # ── Turn start: a new window became current ──────────────────────
            if win > (last_start if last_start is not None else -1):
                cur_did = await database.find_member_discord_id(gid, cur_name)
                if cur_did:
                    emb = discord.Embed(
                        title=f"🏺 Your turn — {name}",
                        description=(
                            f"It's now **your turn** to hold **{name}**.\n\n"
                            f"⏱ Hold until **{end_label} UTC**, then pass it to **{next_name}**.\n"
                            f"The bot will remind you {state['notify_hours']}h before."
                        ),
                        color=0x22c55e,
                    )
                    await self._dm(cur_did, emb)
                await self._channel_post(
                    guild, ch_id,
                    f"🟢 **{cur_name}** is now holding **{name}** — until **{end_label} UTC**, then → **{next_name}**.",
                )
                await database.update_rotation_tracking(
                    aid, gid, rot_last_start_window=win, current_holder=cur_name,
                )

            # ── Pre-handoff: within notify_hours of window end ───────────────
            notify_secs = state["notify_hours"] * 3600
            if (state["seconds_until_handoff"] <= notify_secs
                    and win > (last_notify if last_notify is not None else -1)):
                hrs = max(1, round(state["seconds_until_handoff"] / 3600))
                cur_did = await database.find_member_discord_id(gid, cur_name)
                next_did = await database.find_member_discord_id(gid, next_name)
                if cur_did:
                    emb = discord.Embed(
                        title=f"⏰ Pass on {name} soon",
                        description=(
                            f"You need to hand **{name}** to **{next_name}** in about **{hrs}h** "
                            f"(by **{end_label} UTC**).\n\nRelease the artifact so they can pick it up on time."
                        ),
                        color=0xf59e0b,
                    )
                    await self._dm(cur_did, emb)
                if next_did:
                    emb = discord.Embed(
                        title=f"🔜 You're up next — {name}",
                        description=(
                            f"**{cur_name}** will pass **{name}** to you in about **{hrs}h** "
                            f"(around **{end_label} UTC**).\n\nGet your hero ready to pick it up."
                        ),
                        color=0x6366f1,
                    )
                    await self._dm(next_did, emb)
                await self._channel_post(
                    guild, ch_id,
                    f"⏰ Handoff **{cur_name} → {next_name}** for **{name}** in ~{hrs}h (by {end_label} UTC).",
                )
                await database.update_rotation_tracking(
                    aid, gid, rot_last_notify_window=win,
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(ArtifactRotation(bot))
