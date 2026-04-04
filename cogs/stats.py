"""
cogs/stats.py  -  DME 6Mans
Systeme de points : +3 victoire, +1 defaite. MMR fixe.
"""

import json
import discord
from discord.ext import commands
from utils.mmr import rang_dme


class StatsCog(commands.Cog, name="Stats"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="stats", aliases=["profil", "profile"])
    async def stats(self, ctx, member: discord.Member = None):
        """Voir ton profil. Usage : !stats [@joueur]"""
        cible  = member or ctx.author
        joueur = await self.bot.db.get_or_create_player(cible.id, cible.display_name)
        wins   = joueur.get("wins", 0)
        losses = joueur.get("losses", 0)
        total  = wins + losses
        wr     = f"{(wins / total * 100):.1f}%" if total else "0%"
        pts    = wins * 3 + losses
        rang, emoji = rang_dme(joueur["mmr"], wins)

        embed = discord.Embed(
            title=f"Profil — {cible.display_name}",
            color=discord.Color.orange(),
        )
        embed.set_thumbnail(url=cible.display_avatar.url)
        embed.add_field(name="MMR (peak)",   value=f"**{joueur['mmr']}**",  inline=True)
        embed.add_field(name="Rang DME",     value=f"{emoji} **{rang}**",    inline=True)
        embed.add_field(name="Points",       value=f"**{pts}**",             inline=True)
        embed.add_field(name="Victoires",    value=f"**{wins}** (+3 pts)",   inline=True)
        embed.add_field(name="Defaites",     value=f"**{losses}** (+1 pt)",  inline=True)
        embed.add_field(name="Winrate",      value=f"**{wr}**",              inline=True)
        embed.add_field(name="Statut",       value="Placements" if total < 10 else "Classe", inline=True)
        embed.set_footer(text="!top pour le classement · !history pour l'historique")
        await ctx.send(embed=embed)

    @commands.command(name="top", aliases=["lb", "classement"])
    async def top(self, ctx, nb: int = 10):
        """Voir le classement par points. Usage : !top [nb]"""
        nb      = min(max(nb, 3), 20)
        joueurs = await self.bot.db.get_leaderboard_by_points(nb)
        if not joueurs:
            await ctx.send("Aucun joueur enregistre.")
            return

        season = await self.bot.db.get_current_season()
        embed  = discord.Embed(
            title=f"Classement DME 6Mans — {season['name']}",
            color=discord.Color.gold(),
        )
        medailles = ["🥇", "🥈", "🥉"]
        lignes    = []
        for i, joueur in enumerate(joueurs, start=1):
            wins   = joueur.get("wins", 0)
            losses = joueur.get("losses", 0)
            total  = wins + losses
            wr     = f"{(wins / total * 100):.0f}%" if total else "N/A"
            pts    = wins * 3 + losses
            rang, emoji = rang_dme(joueur["mmr"], wins)
            prefix = medailles[i - 1] if i <= 3 else f"`{i}.`"
            lignes.append(
                f"{prefix} <@{joueur['discord_id']}> {emoji} **{pts} pts** · {wins}V/{losses}D · WR {wr} · {joueur['mmr']} MMR"
            )

        embed.description = "\n".join(lignes)
        embed.set_footer(text="+3 pts victoire · +1 pt defaite · MMR = peak inscription")
        await ctx.send(embed=embed)

    @commands.command(name="history", aliases=["historique", "hist"])
    async def history(self, ctx, member: discord.Member = None):
        """Voir les derniers matchs. Usage : !history [@joueur]"""
        cible  = member or ctx.author
        matchs = await self.bot.db.get_player_matches(cible.id, 7)
        if not matchs:
            await ctx.send(f"Aucun match trouve pour **{cible.display_name}**.")
            return

        embed = discord.Embed(
            title=f"Historique — {cible.display_name}",
            color=discord.Color.orange(),
        )
        for match in matchs:
            orange     = json.loads(match["team_orange"])
            est_orange = cible.id in orange
            victoire   = (match["winner"] == "orange" and est_orange) or (match["winner"] == "blue" and not est_orange)
            resultat   = "W +3 pts" if victoire else "L +1 pt"
            date       = (match.get("finished_at") or "")[:10]
            queue      = match.get("queue_name", "?").upper()
            embed.add_field(
                name=f"Match #{match['id']} · {queue} · {date}",
                value=f"{'✅' if victoire else '❌'} {resultat}",
                inline=True,
            )
        await ctx.send(embed=embed)

    @commands.command(name="season", aliases=["saison"])
    async def season(self, ctx):
        season = await self.bot.db.get_current_season()
        await ctx.send(f"Saison active : **{season['name']}**")


async def setup(bot):
    await bot.add_cog(StatsCog(bot))