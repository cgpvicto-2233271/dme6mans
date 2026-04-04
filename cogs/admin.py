"""
cogs/admin.py  —  DME 6Mans
Commandes admin préfixe :
  !setmmr @joueur <mmr>
  !resetplayer @joueur
  !clearqueue [queue]
  !newseason <nom>
"""

import discord
from discord.ext import commands


class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── !setmmr @joueur <mmr> ──────────────────────────────────────────────
    @commands.command(name="setmmr")
    @commands.has_permissions(administrator=True)
    async def setmmr(self, ctx: commands.Context, member: discord.Member, mmr: int):
        """[Admin] Définir le MMR d'un joueur. Usage : !setmmr @joueur <mmr>"""
        if mmr < 0 or mmr > 4000:
            await ctx.send("❌ MMR invalide (0–4000).")
            return
        await self.bot.db.get_or_create_player(member.id, member.display_name)
        await self.bot.db.set_mmr(member.id, mmr)
        await ctx.send(f"✅ MMR de **{member.display_name}** mis à **{mmr}**.")

    # ── !clearqueue [queue|all] ────────────────────────────────────────────
    @commands.command(name="clearqueue", aliases=["clearq"])
    @commands.has_permissions(administrator=True)
    async def clearqueue(self, ctx: commands.Context, queue: str = "all"):
        """[Admin] Vider une ou toutes les files. Usage : !clearqueue [open|champion|gc|ssl|all]"""
        queue = queue.lower()
        if queue not in ("open", "champion", "gc", "ssl", "all"):
            await ctx.send("❌ File invalide. Choix : `open`, `champion`, `gc`, `ssl`, `all`")
            return
        await self.bot.db.queue_clear(None if queue == "all" else queue)
        await ctx.send(f"🧹 File **{queue.upper()}** vidée.")

    # ── !newseason <nom> ───────────────────────────────────────────────────
    @commands.command(name="newseason")
    @commands.has_permissions(administrator=True)
    async def newseason(self, ctx: commands.Context, *, nom: str):
        """[Admin] Créer une nouvelle saison. Usage : !newseason <nom>"""
        season = await self.bot.db.create_new_season(nom)
        await ctx.send(f"📅 Nouvelle saison active : **{season['name']}**")

    # ── !help6mans ─────────────────────────────────────────────────────────
    @commands.command(name="help6mans", aliases=["commandes", "aide"])
    async def help6mans(self, ctx: commands.Context):
        """Liste toutes les commandes du bot DME 6Mans."""
        embed = discord.Embed(
            title="📋 COMMANDES DME 6Mans",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="🎮 Inscription",
            value=(
                "`!rc <plateforme> <pseudo>` — Rank check et inscription\n"
                "Plateformes : `epic` · `steam` · `psn` · `xbox` · `switch`\n"
                "Exemple : `!rc epic Coussinho` · `!rc steam 76561198XXX`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔁 Files",
            value=(
                "`!q` — Rejoindre ta file (selon ton MMR)\n"
                "`!dq` — Quitter la file\n"
                "`!queue` — Voir toutes les files"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚽ Match",
            value=(
                "`!pick <match_id> @joueur` — Choisir un joueur (draft)\n"
                "`!w <match_id>` — Reporter une victoire\n"
                "`!l <match_id>` — Reporter une défaite"
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Stats",
            value=(
                "`!stats [@joueur]` — Voir ton profil\n"
                "`!top [nb]` — Classement MMR\n"
                "`!history [@joueur]` — Derniers matchs\n"
                "`!whois [@joueur]` — Profil Tracker Network"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔑 Admin",
            value=(
                "`!setmmr @joueur <mmr>` — Modifier le MMR\n"
                "`!resetplayer @joueur` — Reset un joueur\n"
                "`!clearqueue [queue]` — Vider une file\n"
                "`!newseason <nom>` — Nouvelle saison\n"
                "`!forcematch <queue>` — Forcer un draft\n"
                "`!cancelmatch <id>` — Annuler un match"
            ),
            inline=False,
        )
        embed.set_footer(text="DME 6Mans · deathmarkesport.com")
        await ctx.send(embed=embed)

    # ── Gestion erreurs permissions ────────────────────────────────────────
    @setmmr.error
    @clearqueue.error
    @newseason.error
    async def admin_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Argument manquant. Utilise `!help6mans` pour voir les commandes.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))