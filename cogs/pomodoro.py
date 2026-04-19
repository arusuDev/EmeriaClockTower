from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CHIME_PATH = Path(__file__).resolve().parent.parent / "assets" / "chime.mp3"

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("emeria.pomodoro")


@dataclass
class PomodoroSession:
    guild_id: int
    voice_channel_id: int
    text_channel_id: int
    owner_id: int
    work_minutes: int
    break_minutes: int
    total_cycles: int
    phase: str = "work"  # "work" | "break" | "done"
    current_cycle: int = 1
    phase_ends_at: float = 0.0
    muted_member_ids: set[int] = field(default_factory=set)
    task: Optional[asyncio.Task] = None


class Pomodoro(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sessions: dict[int, PomodoroSession] = {}

    pomodoro = app_commands.Group(
        name="pomodoro",
        description="ポモドーロタイマーを操作します",
    )

    @pomodoro.command(name="start", description="ポモドーロタイマーを開始します")
    @app_commands.describe(
        work="作業時間（分）。デフォルト: 25",
        rest="休憩時間（分）。デフォルト: 5",
        cycles="繰り返し回数。デフォルト: 4",
    )
    async def start(
        self,
        interaction: discord.Interaction,
        work: app_commands.Range[int, 1, 180] = 25,
        rest: app_commands.Range[int, 1, 60] = 5,
        cycles: app_commands.Range[int, 1, 20] = 4,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "このコマンドはサーバー内で実行してください。", ephemeral=True
            )
            return

        member = interaction.user
        voice_state = member.voice
        if voice_state is None or voice_state.channel is None:
            await interaction.response.send_message(
                "先にボイスチャンネルに参加してから実行してください。", ephemeral=True
            )
            return

        vc = voice_state.channel
        guild_id = interaction.guild.id
        if guild_id in self.sessions:
            await interaction.response.send_message(
                "このサーバーでは既にポモドーロが実行中です。`/pomodoro stop` で停止できます。",
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message(
                "bot のメンバー情報が取得できませんでした。", ephemeral=True
            )
            return
        perms = vc.permissions_for(me)
        if not perms.mute_members:
            await interaction.response.send_message(
                f"{vc.mention} でメンバーをミュートする権限（メンバーをミュート）が"
                "bot に付与されていません。",
                ephemeral=True,
            )
            return

        session = PomodoroSession(
            guild_id=guild_id,
            voice_channel_id=vc.id,
            text_channel_id=interaction.channel_id or 0,
            owner_id=member.id,
            work_minutes=int(work),
            break_minutes=int(rest),
            total_cycles=int(cycles),
        )
        self.sessions[guild_id] = session
        session.task = asyncio.create_task(self._run(session))

        await interaction.response.send_message(
            "**ポモドーロを開始するよ！**\n"
            f"・対象VC: {vc.mention}\n"
            f"・作業: **{work}分** / 休憩: **{rest}分** / サイクル: **{cycles}回**\n"
            "作業中はVCのメンバーをサーバーミュートし、休憩に入ると解除します。"
        )

    @pomodoro.command(name="stop", description="実行中のポモドーロタイマーを停止します")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "このコマンドはサーバー内で実行してください。", ephemeral=True
            )
            return
        session = self.sessions.get(interaction.guild.id)
        if session is None:
            await interaction.response.send_message(
                "現在、このサーバーでは実行中のポモドーロはありません。", ephemeral=True
            )
            return

        await interaction.response.defer()
        await self._end_session(session)
        await interaction.followup.send("ポモドーロを停止しました。ミュートを解除しました。")

    @pomodoro.command(name="status", description="ポモドーロの状態を確認します")
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "このコマンドはサーバー内で実行してください。", ephemeral=True
            )
            return
        session = self.sessions.get(interaction.guild.id)
        if session is None:
            await interaction.response.send_message(
                "実行中のポモドーロはありません。", ephemeral=True
            )
            return

        remaining = max(0, int(session.phase_ends_at - time.time()))
        mm, ss = divmod(remaining, 60)
        phase_name = {"work": "作業中", "break": "休憩中", "done": "終了処理中"}.get(
            session.phase, session.phase
        )
        vc = interaction.guild.get_channel(session.voice_channel_id)
        vc_mention = vc.mention if vc is not None else f"<#{session.voice_channel_id}>"
        await interaction.response.send_message(
            f"**{phase_name}** ({session.current_cycle}/{session.total_cycles})\n"
            f"・対象VC: {vc_mention}\n"
            f"・残り時間: **{mm:02d}:{ss:02d}**",
            ephemeral=True,
        )

    async def _run(self, session: PomodoroSession) -> None:
        channel = self.bot.get_channel(session.text_channel_id)
        try:
            for cycle in range(1, session.total_cycles + 1):
                session.current_cycle = cycle

                # ---- Work phase ----
                session.phase = "work"
                duration = session.work_minutes * 60
                session.phase_ends_at = time.time() + duration
                await self._apply_mute(session, mute=True)
                await self._announce(
                    channel,
                    f"**【{cycle}/{session.total_cycles}】作業開始**  "
                    f"{session.work_minutes}分 集中しましょう！VCメンバーをミュートしました。",
                )
                await self._play_chime(session)
                await asyncio.sleep(duration)

                # Skip break after the last work period
                if cycle >= session.total_cycles:
                    break

                # ---- Break phase ----
                session.phase = "break"
                duration = session.break_minutes * 60
                session.phase_ends_at = time.time() + duration
                await self._apply_mute(session, mute=False)
                await self._announce(
                    channel,
                    f"**【{cycle}/{session.total_cycles}】休憩**  "
                    f"{session.break_minutes}分 休みましょう！ミュートを解除しました。",
                )
                await self._play_chime(session)
                await asyncio.sleep(duration)

            session.phase = "done"
            await self._apply_mute(session, mute=False)
            await self._announce(channel, "ポモドーロ完了！おつかれさまでした。")
            await self._play_chime(session)
        except asyncio.CancelledError:
            log.info("Pomodoro task cancelled for guild %s", session.guild_id)
            raise
        except Exception:
            log.exception("Pomodoro task failed for guild %s", session.guild_id)
            await self._announce(channel, "エラーが発生したためポモドーロを終了します。")
        finally:
            # Make sure mutes are cleared and session is removed
            await self._apply_mute(session, mute=False)
            self.sessions.pop(session.guild_id, None)

    async def _end_session(self, session: PomodoroSession) -> None:
        task = session.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        # _run's finally clause handles mute cleanup and session removal,
        # but in case the task wasn't running yet, do it here too.
        await self._apply_mute(session, mute=False)
        self.sessions.pop(session.guild_id, None)

    async def _apply_mute(self, session: PomodoroSession, mute: bool) -> None:
        guild = self.bot.get_guild(session.guild_id)
        if guild is None:
            return
        vc = guild.get_channel(session.voice_channel_id)
        if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
            return

        if mute:
            for m in list(vc.members):
                if m.bot or m.voice is None:
                    continue
                if m.voice.mute:
                    # Already server-muted (possibly by a moderator); leave as-is.
                    continue
                try:
                    await m.edit(mute=True, reason="Pomodoro: 作業時間")
                    session.muted_member_ids.add(m.id)
                except discord.HTTPException as e:
                    log.warning("Failed to mute %s: %s", m, e)
        else:
            for member_id in list(session.muted_member_ids):
                m = guild.get_member(member_id)
                session.muted_member_ids.discard(member_id)
                if m is None or m.voice is None or m.voice.channel is None:
                    continue
                try:
                    await m.edit(mute=False, reason="Pomodoro: 休憩時間/終了")
                except discord.HTTPException as e:
                    log.warning("Failed to unmute %s: %s", m, e)

    async def _play_chime(self, session: PomodoroSession) -> None:
        guild = self.bot.get_guild(session.guild_id)
        if guild is None:
            return
        vc = guild.get_channel(session.voice_channel_id)
        if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
            return
        if not any(not m.bot for m in vc.members):
            return
        if not CHIME_PATH.is_file():
            log.warning("Chime file not found: %s", CHIME_PATH)
            return

        voice_client: Optional[discord.VoiceClient] = None
        try:
            voice_client = await vc.connect(timeout=10, reconnect=False)
            source = discord.FFmpegPCMAudio(str(CHIME_PATH))
            done = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _after(error: Optional[Exception]) -> None:
                if error is not None:
                    log.warning("Chime playback error: %s", error)
                loop.call_soon_threadsafe(done.set)

            voice_client.play(source, after=_after)
            try:
                await asyncio.wait_for(done.wait(), timeout=30)
            except asyncio.TimeoutError:
                log.warning("Chime playback timed out")
                if voice_client.is_playing():
                    voice_client.stop()
        except Exception:
            log.exception("Failed to play chime")
        finally:
            if voice_client is not None and voice_client.is_connected():
                try:
                    await voice_client.disconnect(force=False)
                except Exception:
                    log.exception("Failed to disconnect voice client after chime")

    async def _announce(self, channel: Optional[discord.abc.Messageable], content: str) -> None:
        if channel is None:
            return
        try:
            await channel.send(content)
        except discord.HTTPException as e:
            log.warning("Failed to send announcement: %s", e)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        session = self.sessions.get(member.guild.id)
        if session is None:
            return

        was_in = before.channel is not None and before.channel.id == session.voice_channel_id
        now_in = after.channel is not None and after.channel.id == session.voice_channel_id

        if not was_in and now_in and session.phase == "work":
            # Joined the target VC during work phase -> mute
            if after.mute:
                return
            try:
                await member.edit(mute=True, reason="Pomodoro: 作業中のVC参加")
                session.muted_member_ids.add(member.id)
            except discord.HTTPException as e:
                log.warning("Failed to mute joiner %s: %s", member, e)
        elif was_in and not now_in:
            # Left the VC: forget tracking; no need to unmute (not in a channel)
            session.muted_member_ids.discard(member.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Pomodoro(bot))
