"""
cogs/stats.py  —  DME 6Mans
!stats / !top / !history / !mmrhistory / !season
"""

import json
import discord
from discord.ext import commands

from utils.mmr import rang_dme, mmr_change_arrow
from utils.logger import setup_logger

log = setup_logger("stats")


class StatsCog(commands.Cog, name="Stats"):
    def __init__(self, bot):
        self.bot = bot

    # ── !stats ─────────────────────────────────────────────────────────────────

    @commands.command(name="stats", aliases=["profil", "profile"])
    async def stats(self, ctx: commands.Context, member: discord.Member = None):
        """Voir ton profil. Usage : !stats [@joueur]"""
        cible  = member or ctx.author
        joueur = await self.bot.db.get_or_create_player(cible.id, cible.display_name)
        lien   = await self.bot.db.get_tracker_link(cible.id)

        wins   = joueur.get("wins", 0)
        losses = joueur.get("losses", 0)
        total  = wins + losses
        wr     = f"{(wins / total * 100):.1f}%" if total else "0%"
        pts    = wins * 3 + losses
        rang, emoji = rang_dme(joueur["mmr"], wins)

        # Saison active
        season = await self.bot.db.get_current_season()
        s_wins   = joueur.get("season_wins", 0) or 0
        s_losses = joueur.get("season_losses", 0) or 0

        statut = "🟡 Placement" if total < 10 else "✅ Classé"

        embed = discord.Embed(
            title=f"👤 {cible.display_name}",
            color=0x5865F2,
        )
        embed.set_thumbnail(url=cible.display_avatar.url)

        # Ligne principale
        embed.add_field(name="MMR",      value=f"**{joueur['mmr']}**",          inline=True)
        embed.add_field(name="Rang DME", value=f"{emoji} **{rang}**",           inline=True)
        embed.add_field(name="Statut",   value=statut,                          inline=True)

        # Stats globales
        embed.add_field(name="Victoires", value=f"**{wins}**",                  inline=True)
        embed.add_field(name="Défaites",  value=f"**{losses}**",                inline=True)
        embed.add_field(name="Winrate",   value=f"**{wr}**",                    inline=True)

        # Stats saison
        embed.add_field(
            name=f"Saison — {season['name']}",
            value=f"**{s_wins}**V / **{s_losses}**D",
            inline=True,
        )
        embed.add_field(name="Points",   value=f"**{pts}**",                    inline=True)
        embed.add_field(name="Matchs",   value=f"**{total}**",                  inline=True)

        # Tracker
        if lien and lien.get("rl_rank") and lien.get("rl_mmr"):
            embed.add_field(
                name="Rang Tracker",
                value=f"**{lien['rl_rank']}** ({lien['rl_mmr']} MMR)",
                inline=True,
            )
        if lien:
            embed.add_field(
                name="Profil",
                value=f"[tracker.gg]({lien['tracker_url']})",
                inline=True,
            )

        embed.set_footer(text="!history · !mmrhistory · !top · !help6mans")
        await ctx.send(embed=embed)

    # ── !top ───────────────────────────────────────────────────────────────────

    @commands.command(name="top", aliases=["lb", "classement"])
    async def top(self, ctx: commands.Context, nb: int = 10):
        """Classement par MMR. Usage : !top [nb]"""
        nb     = min(max(nb, 3), 20)
        joueurs = await self.bot.db.get_leaderboard_by_points(nb)
        if not joueurs:
            await ctx.send("❌ Aucun joueur enregistré.")
            return

        season = await self.bot.db.get_current_season()
        embed  = discord.Embed(
            title=f"🏆 Classement DME 6Mans — {season['name']}",
            color=0xFFD700,
        )

        medailles = ["🥇", "🥈", "🥉"]
        lignes    = []
        for i, joueur in enumerate(joueurs, start=1):
            wins   = joueur.get("wins", 0)
            losses = joueur.get("losses", 0)
            total  = wins + losses
            wr     = f"{(wins / total * 100):.0f}%" if total else "—"
            rang, emoji = rang_dme(joueur["mmr"], wins)
            prefix = medailles[i - 1] if i <= 3 else f"`{i}.`"
            placement = " `placement`" if total < 10 else ""
            lignes.append(
                f"{prefix} <@{joueur['discord_id']}> {emoji} {rang} **{joueur['mmr']} MMR** "
                f"· {wins}V/{losses}D · WR {wr}{placement}"
            )

        embed.description = "\n".join(lignes)
        embed.set_footer(text="MMR dynamique (Elo) · !stats pour ton profil")
        await ctx.send(embed=embed)

    # ── !history ───────────────────────────────────────────────────────────────

    @commands.command(name="history", aliases=["historique", "hist"])
    async def history(self, ctx: commands.Context, member: discord.Member = None):
        """Voir les derniers matchs. Usage : !history [@joueur]"""
        cible  = member or ctx.author
        matchs = await self.bot.db.get_player_matches(cible.id, 7)
        if not matchs:
            await ctx.send(f"❌ Aucun match trouvé pour **{cible.display_name}**.")
            return

        embed = discord.Embed(
            title=f"📋 Historique — {cible.display_name}",
            color=0x5865F2,
        )
        for match in matchs:
            orange     = json.loads(match["team_orange"])
            est_orange = cible.id in orange
            victoire   = (
                (match["winner"] == "orange" and est_orange)
                or (match["winner"] == "blue" and not est_orange)
            )
            resultat  = "✅ Victoire" if victoire else "❌ Défaite"
            date      = (match.get("finished_at") or "")[:10]
            queue     = match.get("queue_name", "?").upper()
            embed.add_field(
                name=f"Match #{match['id']} · {queue} · {date}",
                value=resultat,
                inline=True,
            )
        embed.set_footer(text="!mmrhistory pour l'évolution MMR")
        await ctx.send(embed=embed)

    # ── !mmrhistory ────────────────────────────────────────────────────────────

    @commands.command(name="mmrhistory", aliases=["mmrhist", "mmrlog"])
    async def mmr_history(self, ctx: commands.Context, member: discord.Member = None):
        """Voir l'évolution MMR récente. Usage : !mmrhistory [@joueur]"""
        cible   = member or ctx.author
        joueur  = await self.bot.db.get_player(cible.id)
        if not joueur:
            await ctx.send(f"❌ **{cible.display_name}** n'est pas inscrit.")
            return

        historique = await self.bot.db.get_mmr_history(cible.id, 10)
        if not historique:
            await ctx.send(
                f"Aucun historique MMR pour **{cible.display_name}** pour le moment.\n"
                f"L'historique se remplit au fil des matchs."
            )
            return

        rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
        embed = discord.Embed(
            title=f"📈 Évolution MMR — {cible.display_name}",
            description=f"MMR actuel : **{joueur['mmr']}** {emoji} {rang}",
            color=0x5865F2,
        )

        lignes = []
        for entry in historique:
            change = entry["change"]
            arrow  = mmr_change_arrow(change)
            reason = entry.get("reason", "?")
            date   = (entry.get("created_at") or "")[:10]
            mid    = entry.get("match_id")
            ligne  = f"`{date}` {arrow} → **{entry['mmr_after']}** · {reason}"
            if mid:
                ligne += f" (match #{mid})"
            lignes.append(ligne)

        embed.add_field(
            name="10 derniers changements",
            value="\n".join(lignes),
            inline=False,
        )
        embed.set_footer(text="!history pour l'historique des matchs")
        await ctx.send(embed=embed)

    # ── !season ────────────────────────────────────────────────────────────────

    @commands.command(name="season", aliases=["saison"])
    async def season(self, ctx: commands.Context):
        """Voir la saison active. Usage : !season"""
        season  = await self.bot.db.get_current_season()
        nb_joueurs = len(await self.bot.db.get_leaderboard_by_points(999))
        nb_matchs  = await self.bot.db.get_total_matches()
        embed = discord.Embed(
            title=f"📅 Saison active : {season['name']}",
            color=0xFFD700,
        )
        embed.add_field(name="Joueurs inscrits", value=f"**{nb_joueurs}**", inline=True)
        embed.add_field(name="Matchs joués",     value=f"**{nb_matchs}**",  inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))
