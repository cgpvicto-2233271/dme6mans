"""
cogs/leaderboard_live.py  —  DME 6Mans
Leaderboard auto-rafraîchi toutes les 2 minutes.
Message_id persisté en base pour survivre aux redémarrages.
"""

import asyncio
import datetime
import discord
from discord.ext import commands, tasks

from utils.mmr import rang_dme
from utils.logger import setup_logger

log = setup_logger("leaderboard")

LEADERBOARD_CHANNEL = "leaderboard"
REFRESH_MINUTES     = 2
TOP_N               = 15


class LeaderboardLiveCog(commands.Cog, name="LeaderboardLive"):
    def __init__(self, bot):
        self.bot = bot
        self.live_update.start()

    def cog_unload(self):
        self.live_update.cancel()

    async def _get_channel(self):
        for guild in self.bot.guilds:
            for ch in guild.text_channels:
                if LEADERBOARD_CHANNEL in ch.name.lower():
                    return guild, ch
        return None, None

    async def _build_embed(self) -> discord.Embed:
        try:
            joueurs = await self.bot.db.get_leaderboard_by_points(TOP_N)
            season  = await self.bot.db.get_current_season()
        except Exception as exc:
            log.error("Leaderboard build error: %s", exc)
            embed = discord.Embed(title="Classement DME 6Mans", color=0xFFD700)
            embed.description = "*Chargement en cours...*"
            return embed

        embed = discord.Embed(
            title=f"🏆 Classement DME 6Mans — {season['name']}",
            color=0xFFD700,
        )

        if not joueurs:
            embed.description = (
                "*Aucun joueur inscrit pour le moment.*\n"
                "Utilise `!rc <plateforme> <pseudo>` pour t'inscrire."
            )
            embed.set_footer(text="Mis à jour automatiquement")
            return embed

        medailles = ["🥇", "🥈", "🥉"]
        lignes = []

        for i, j in enumerate(joueurs, start=1):
            wins   = j.get("wins", 0)
            losses = j.get("losses", 0)
            total  = wins + losses
            wr     = f"{(wins / total * 100):.0f}%" if total else "—"
            rang, emoji = rang_dme(j["mmr"], wins)
            prefix = medailles[i - 1] if i <= 3 else f"`{i:>2}.`"
            placement = " `P`" if total < 10 else ""

            lignes.append(
                f"{prefix} <@{j['discord_id']}> {emoji} {rang} **{j['mmr']} MMR** "
                f"· {wins}V/{losses}D · {wr}{placement}"
            )

        embed.description = "\n".join(lignes)

        try:
            tous = await self.bot.db.get_leaderboard_by_points(999)
            total_joueurs = len(tous)
            total_matchs  = await self.bot.db.get_total_matches()
            embed.add_field(
                name="Stats globales",
                value=f"**{total_joueurs}** joueurs · **{total_matchs}** matchs joués",
                inline=False,
            )
        except Exception:
            pass

        now = datetime.datetime.now().strftime("%H:%M:%S")
        embed.set_footer(
            text=f"MMR dynamique (Elo) · `P` = placement · Mis à jour à {now}"
        )
        return embed

    @tasks.loop(minutes=REFRESH_MINUTES)
    async def live_update(self):
        try:
            guild, channel = await self._get_channel()
            if not channel or not guild:
                return

            embed = await self._build_embed()

            # Essayer de récupérer le message depuis la DB
            stored = await self.bot.db.get_leaderboard_message(guild.id)
            if stored and stored["channel_id"] == channel.id:
                try:
                    msg = await channel.fetch_message(stored["message_id"])
                    await msg.edit(embed=embed)
                    return
                except (discord.NotFound, discord.HTTPException):
                    pass

            # Nettoyer les anciens messages du bot
            try:
                async for msg in channel.history(limit=10):
                    if msg.author == self.bot.user:
                        await msg.delete()
            except discord.Forbidden:
                pass

            try:
                msg = await channel.send(embed=embed)
                await self.bot.db.set_leaderboard_message(guild.id, channel.id, msg.id)
                log.info("Leaderboard posté (id=%d) dans #%s", msg.id, channel.name)
            except discord.Forbidden:
                log.warning("Forbidden: impossible de poster dans #%s", channel.name)

        except Exception as exc:
            log.error("live_update error: %s", exc)

    @live_update.before_loop
    async def before_live_update(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)

    @commands.command(name="lboard", aliases=["leaderboard"])
    async def lboard(self, ctx: commands.Context):
        """Voir le classement. Usage : !lboard"""
        embed = await self._build_embed()
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(LeaderboardLiveCog(bot))
