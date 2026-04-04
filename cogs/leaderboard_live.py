"""
cogs/leaderboard_live.py  -  DME 6Mans
Classement par points : wins*3 + losses*1
MMR affiche comme indicateur de niveau uniquement.
"""

import asyncio
import datetime
import discord
from discord.ext import commands, tasks
from utils.mmr import rang_dme

LEADERBOARD_CHANNEL = "leaderboard"
REFRESH_MINUTES     = 2
TOP_N               = 15


class LeaderboardLiveCog(commands.Cog, name="LeaderboardLive"):
    def __init__(self, bot):
        self.bot        = bot
        self.message_id = None
        self.live_update.start()

    def cog_unload(self):
        self.live_update.cancel()

    async def _get_channel(self):
        for guild in self.bot.guilds:
            for ch in guild.text_channels:
                if "leaderboard" in ch.name.lower():
                    return ch
        return None

    async def _build_embed(self) -> discord.Embed:
        joueurs = await self.bot.db.get_leaderboard_by_points(TOP_N)
        season  = await self.bot.db.get_current_season()

        embed = discord.Embed(
            title=f"Classement DME 6Mans — {season['name']}",
            color=discord.Color.gold(),
        )

        if not joueurs:
            embed.description = "*Aucun joueur inscrit pour le moment.*\nUtilise `!rc` pour t'inscrire."
            embed.set_footer(text="Mis a jour en temps reel")
            return embed

        medailles = ["🥇", "🥈", "🥉"]
        lignes    = []

        for i, j in enumerate(joueurs, start=1):
            wins   = j.get("wins", 0)
            losses = j.get("losses", 0)
            total  = wins + losses
            wr     = f"{(wins / total * 100):.0f}%" if total else "—"
            pts    = wins * 3 + losses
            rang, emoji = rang_dme(j["mmr"], wins)
            prefix = medailles[i - 1] if i <= 3 else f"`{i}.`"
            statut = " `placement`" if total < 10 else ""

            lignes.append(
                f"{prefix} <@{j['discord_id']}> {emoji} **{pts} pts** "
                f"· {wins}V/{losses}D · WR {wr} · {j['mmr']} MMR{statut}"
            )

        embed.description = "\n".join(lignes)

        # Stats globales
        tous = await self.bot.db.get_leaderboard_by_points(999)
        total_joueurs = len(tous)
        total_matchs  = sum(j.get("wins", 0) + j.get("losses", 0) for j in tous) // 2
        embed.add_field(
            name="Saison 1",
            value=f"**{total_joueurs}** joueurs · **{total_matchs}** matchs joues",
            inline=False,
        )

        now = datetime.datetime.now().strftime("%H:%M:%S")
        embed.set_footer(text=f"+3 pts victoire · +1 pt defaite · MMR = peak · Mis a jour a {now}")
        return embed

    @tasks.loop(minutes=REFRESH_MINUTES)
    async def live_update(self):
        channel = await self._get_channel()
        if not channel:
            return

        embed = await self._build_embed()

        if self.message_id:
            try:
                msg = await channel.fetch_message(self.message_id)
                await msg.edit(embed=embed)
                return
            except (discord.NotFound, discord.HTTPException):
                self.message_id = None

        try:
            async for msg in channel.history(limit=10):
                if msg.author == self.bot.user:
                    await msg.delete()
        except discord.Forbidden:
            pass

        try:
            msg = await channel.send(embed=embed)
            self.message_id = msg.id
        except discord.Forbidden:
            pass

    @live_update.before_loop
    async def before_live_update(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(3)

    @commands.command(name="lboard")
    async def lboard(self, ctx):
        """Voir le classement. Usage : !lboard"""
        embed = await self._build_embed()
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(LeaderboardLiveCog(bot))