"""
cogs/stats.py  —  DME 6Mans
Commandes préfixe style RLQC :
  !stats [@joueur]   → profil compétitif
  !top               → leaderboard top 10
  !history [@joueur] → derniers matchs
  !season            → saison active
"""

import json

import discord
from discord.ext import commands

from utils.mmr import rang_dme


class StatsCog(commands.Cog, name="Stats"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── !stats [@joueur] ───────────────────────────────────────────────────
    @commands.command(name="stats", aliases=["profil", "profile"])
    async def stats(self, ctx: commands.Context, member: discord.Member | None = None):
        """Voir ton profil compétitif. Usage : !stats [@joueur]"""
        cible  = member or ctx.author
        joueur = await self.bot.db.get_or_create_player(cible.id, cible.display_name)
        wins   = joueur.get("wins", 0)
        losses = joueur.get("losses", 0)
        total  = wins + losses
        wr     = f"{(wins / total * 100):.1f}%" if total else "0%"
        rang, emoji = rang_dme(joueur["mmr"], wins)

        embed = discord.Embed(
            title=f"📊 Profil — {cible.display_name}",
            color=discord.Color.orange(),
        )
        embed.set_thumbnail(url=cible.display_avatar.url)
        embed.add_field(name="MMR",      value=f"**{joueur['mmr']}**",        inline=True)
        embed.add_field(name="Rang",     value=f"{emoji} **{rang}**",          inline=True)
        embed.add_field(name="Statut",   value="Placements" if total < 10 else "Classé", inline=True)
        embed.add_field(name="Victoires",value=f"**{wins}**",                  inline=True)
        embed.add_field(name="Défaites", value=f"**{losses}**",                inline=True)
        embed.add_field(name="Winrate",  value=f"**{wr}**",                    inline=True)
        embed.set_footer(text="!top pour le classement · !history pour l'historique")
        await ctx.send(embed=embed)

    # ── !top ───────────────────────────────────────────────────────────────
    @commands.command(name="top", aliases=["lb", "classement"])
    async def top(self, ctx: commands.Context, nb: int = 10):
        """Voir le classement MMR. Usage : !top [nb]"""
        nb      = min(max(nb, 3), 20)
        joueurs = await self.bot.db.get_leaderboard(nb)
        if not joueurs:
            await ctx.send("📭 Aucun joueur enregistré.")
            return

        season = await self.bot.db.get_current_season()
        embed  = discord.Embed(
            title=f"🏆 Classement DME 6Mans — {season['name']}",
            color=discord.Color.gold(),
        )
        medailles = ["🥇", "🥈", "🥉"]
        lignes    = []
        for i, joueur in enumerate(joueurs, start=1):
            wins   = joueur.get("wins", 0)
            losses = joueur.get("losses", 0)
            total  = wins + losses
            wr     = f"{(wins / total * 100):.0f}%" if total else "N/A"
            rang, emoji = rang_dme(joueur["mmr"], wins)
            prefix = medailles[i - 1] if i <= 3 else f"`{i}.`"
            lignes.append(
                f"{prefix} <@{joueur['discord_id']}> — **{joueur['mmr']}** {emoji} {rang} · {wins}W/{losses}L · {wr}"
            )

        embed.description = "\n".join(lignes)
        embed.set_footer(text="!stats @joueur pour voir un profil · !history pour l'historique")
        await ctx.send(embed=embed)

    # ── !history [@joueur] ─────────────────────────────────────────────────
    @commands.command(name="history", aliases=["historique", "hist"])
    async def history(self, ctx: commands.Context, member: discord.Member | None = None):
        """Voir les derniers matchs. Usage : !history [@joueur]"""
        cible  = member or ctx.author
        matchs = await self.bot.db.get_player_matches(cible.id, 7)
        if not matchs:
            await ctx.send(f"📭 Aucun match trouvé pour **{cible.display_name}**.")
            return

        embed = discord.Embed(
            title=f"📋 Historique — {cible.display_name}",
            color=discord.Color.orange(),
        )
        for match in matchs:
            orange   = json.loads(match["team_orange"])
            est_orange = cible.id in orange
            victoire = (
                (match["winner"] == "orange" and est_orange)
                or (match["winner"] == "blue" and not est_orange)
            )
            resultat = "✅ W" if victoire else "❌ L"
            date     = (match.get("finished_at") or "")[:10]
            queue    = match.get("queue_name", "?").upper()
            embed.add_field(
                name=f"Match #{match['id']} · {queue} · {date}",
                value=f"{resultat}",
                inline=True,
            )
        await ctx.send(embed=embed)

    # ── !season ────────────────────────────────────────────────────────────
    @commands.command(name="season", aliases=["saison"])
    async def season(self, ctx: commands.Context):
        """Voir la saison active. Usage : !season"""
        season = await self.bot.db.get_current_season()
        await ctx.send(f"📅 Saison active : **{season['name']}**")


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))