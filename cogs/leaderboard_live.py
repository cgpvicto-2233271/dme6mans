"""
cogs/leaderboard_live.py  -  DME 6Mans
Classement base sur un score combine :
  Score = (winrate * 0.5) + (mmr / 2000 * 0.35) + (matchs_joues / 100 * 0.15)
Quelqu'un avec beaucoup de victoires ET un bon winrate sera toujours devant.
"""

import asyncio
import datetime
import discord
from discord.ext import commands, tasks
from utils.mmr import rang_dme

LEADERBOARD_CHANNEL = "leaderboard"
REFRESH_MINUTES     = 2
TOP_N               = 15


def score_classement(mmr: int, wins: int, losses: int) -> float:
    """Score combine pour le classement."""
    total   = wins + losses
    winrate = (wins / total) if total > 0 else 0
    # Penalise les joueurs avec trop peu de matchs
    poids_matchs = min(total / 20, 1.0)  # plein poids apres 20 matchs
    return (winrate * 0.5 + (mmr / 2000) * 0.35 + (total / 100) * 0.15) * poids_matchs


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
        joueurs = await self.bot.db.get_leaderboard(50)
        season  = await self.bot.db.get_current_season()

        # Trier par score combine
        def sort_key(j):
            return score_classement(j["mmr"], j.get("wins", 0), j.get("losses", 0))

        joueurs = sorted(joueurs, key=sort_key, reverse=True)[:TOP_N]

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
            rang, emoji = rang_dme(j["mmr"], wins)
            prefix = medailles[i - 1] if i <= 3 else f"`{i}.`"

            # Indicateur de placement si moins de 10 matchs
            statut = " `placement`" if total < 10 else ""

            lignes.append(
                f"{prefix} <@{j['discord_id']}> {emoji} **{j['mmr']}** "
                f"· {wins}V/{losses}D · WR {wr}{statut}"
            )

        embed.description = "\n".join(lignes)

        # Stats globales
        total_joueurs = await self.bot.db.get_leaderboard(999)
        total_matchs  = sum(j.get("wins", 0) + j.get("losses", 0) for j in total_joueurs) // 2
        embed.add_field(
            name="Saison 1",
            value=f"**{len(total_joueurs)}** joueurs · **{total_matchs}** matchs joues",
            inline=False,
        )

        now = datetime.datetime.now().strftime("%H:%M:%S")
        embed.set_footer(text=f"Classement par winrate + MMR + activite · Mis a jour a {now}")
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