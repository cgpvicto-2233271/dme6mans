"""
cogs/admin.py  —  DME 6Mans
Panel admin complet : MMR, bans queue, saisons, logs, commandes de gestion.
"""

import discord
from discord.ext import commands

from utils.mmr import rang_dme, mmr_change_arrow
from utils.logger import setup_logger

log = setup_logger("admin")


class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── !setmmr ───────────────────────────────────────────────────────────────

    @commands.command(name="setmmr")
    @commands.has_permissions(administrator=True)
    async def setmmr(self, ctx: commands.Context, member: discord.Member, mmr: int):
        """[Admin] Définir le MMR d'un joueur. Usage : !setmmr @joueur <mmr>"""
        if mmr < 0 or mmr > 4000:
            await ctx.send("❌ MMR invalide (0–4000).")
            return
        joueur = await self.bot.db.get_player(member.id)
        if not joueur:
            await ctx.send(f"❌ **{member.display_name}** n'est pas inscrit.")
            return
        old_mmr = joueur["mmr"]
        await self.bot.db.set_mmr(member.id, mmr, reason="admin", admin_id=ctx.author.id)
        rang, emoji = rang_dme(mmr, joueur.get("wins", 0))
        arrow = mmr_change_arrow(mmr - old_mmr)
        await ctx.send(
            f"✅ MMR de **{member.display_name}** : {old_mmr} → **{mmr}** {arrow} "
            f"({emoji} {rang})"
        )
        log.info("setmmr: %s → %d par %s", member.display_name, mmr, ctx.author)

    # ── !banqueue ─────────────────────────────────────────────────────────────

    @commands.command(name="banqueue", aliases=["bq", "qban"])
    @commands.has_permissions(administrator=True)
    async def ban_queue(
        self,
        ctx: commands.Context,
        member: discord.Member,
        heures: int = 0,
        *,
        raison: str = "Violation des règles",
    ):
        """[Admin] Bannir un joueur de la queue. Usage : !banqueue @joueur [heures] [raison]"""
        hours = heures if heures > 0 else None
        await self.bot.db.queue_leave(member.id)
        await self.bot.db.ban_player(member.id, raison, ctx.author.id, hours)

        duree = f"**{heures}h**" if hours else "**permanent**"
        embed = discord.Embed(
            title="🔨 Ban Queue",
            color=0xED4245,
        )
        embed.add_field(name="Joueur",  value=f"<@{member.id}>",                inline=True)
        embed.add_field(name="Durée",   value=duree,                            inline=True)
        embed.add_field(name="Raison",  value=raison,                           inline=False)
        embed.add_field(name="Par",     value=f"<@{ctx.author.id}>",            inline=True)
        await ctx.send(embed=embed)

        try:
            await member.send(
                f"🚫 Tu as été banni de la queue DME 6Mans.\n"
                f"Durée : {duree} · Raison : {raison}"
            )
        except discord.Forbidden:
            pass
        log.info(
            "banqueue: %s (%d) | %sh | %s | par %s",
            member.display_name, member.id, heures or "∞", raison, ctx.author,
        )

    # ── !unbanqueue ───────────────────────────────────────────────────────────

    @commands.command(name="unbanqueue", aliases=["unbq", "qunban"])
    @commands.has_permissions(administrator=True)
    async def unban_queue(self, ctx: commands.Context, member: discord.Member):
        """[Admin] Lever le ban queue d'un joueur. Usage : !unbanqueue @joueur"""
        removed = await self.bot.db.unban_player(member.id)
        if removed:
            await ctx.send(f"✅ Ban queue levé pour **{member.display_name}**.")
            try:
                await member.send("✅ Ton ban queue DME 6Mans a été levé. Tu peux rejoindre une file.")
            except discord.Forbidden:
                pass
            log.info("unbanqueue: %s (%d) par %s", member.display_name, member.id, ctx.author)
        else:
            await ctx.send(f"❌ **{member.display_name}** n'est pas banni.")

    # ── !bans ─────────────────────────────────────────────────────────────────

    @commands.command(name="bans", aliases=["banlist"])
    @commands.has_permissions(administrator=True)
    async def bans(self, ctx: commands.Context):
        """[Admin] Voir les bans queue actifs. Usage : !bans"""
        bans = await self.bot.db.get_all_bans()
        if not bans:
            await ctx.send("✅ Aucun ban queue actif.")
            return
        embed = discord.Embed(
            title=f"🔨 Bans Queue Actifs ({len(bans)})",
            color=0xED4245,
        )
        for ban in bans:
            until = ban.get("banned_until")
            duree = f"`{until[:16]}`" if until else "Permanent"
            embed.add_field(
                name=f"<@{ban['discord_id']}>",
                value=f"Raison : {ban['reason']}\nExpire : {duree}",
                inline=True,
            )
        await ctx.send(embed=embed)

    # ── !clearqueue ───────────────────────────────────────────────────────────

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
        log.info("clearqueue: %s par %s", queue, ctx.author)

    # ── !newseason ────────────────────────────────────────────────────────────

    @commands.command(name="newseason")
    @commands.has_permissions(administrator=True)
    async def newseason(self, ctx: commands.Context, *, nom: str):
        """[Admin] Créer une nouvelle saison (reset stats saison). Usage : !newseason <nom>"""
        season = await self.bot.db.create_new_season(nom)
        embed = discord.Embed(
            title=f"📅 Nouvelle saison : {season['name']}",
            description=(
                "Les stats de saison ont été réinitialisées.\n"
                "Le MMR et les stats globales sont conservés."
            ),
            color=0x2ECC71,
        )
        await ctx.send(embed=embed)
        log.info("newseason: '%s' créée par %s", nom, ctx.author)

    # ── !mmrlogs ──────────────────────────────────────────────────────────────

    @commands.command(name="mmrlogs", aliases=["mmrlog", "mlog"])
    @commands.has_permissions(administrator=True)
    async def mmrlogs(self, ctx: commands.Context, member: discord.Member):
        """[Admin] Voir l'historique MMR d'un joueur. Usage : !mmrlogs @joueur"""
        joueur = await self.bot.db.get_player(member.id)
        if not joueur:
            await ctx.send(f"❌ **{member.display_name}** n'est pas inscrit.")
            return

        historique = await self.bot.db.get_mmr_history(member.id, 15)
        if not historique:
            await ctx.send(f"Aucun historique MMR pour **{member.display_name}**.")
            return

        rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
        embed = discord.Embed(
            title=f"📊 Logs MMR — {member.display_name}",
            description=f"MMR actuel : **{joueur['mmr']}** {emoji} {rang}",
            color=0x5865F2,
        )
        lignes = []
        for entry in historique:
            change = entry["change"]
            arrow  = mmr_change_arrow(change)
            reason = entry.get("reason", "?")
            date   = (entry.get("created_at") or "")[:16]
            mid    = entry.get("match_id")
            ligne  = f"`{date}` {arrow} → **{entry['mmr_after']}** · `{reason}`"
            if mid:
                ligne += f" (#{mid})"
            lignes.append(ligne)

        embed.add_field(
            name="15 derniers changements",
            value="\n".join(lignes),
            inline=False,
        )
        await ctx.send(embed=embed)

    # ── !forceresult ──────────────────────────────────────────────────────────

    @commands.command(name="forceresult", aliases=["forcew"])
    @commands.has_permissions(administrator=True)
    async def forceresult(self, ctx: commands.Context, match_id: int, winner: str):
        """[Admin] Forcer le résultat d'un match. Usage : !forceresult <match_id> <orange|blue>"""
        winner = winner.lower()
        if winner not in ("orange", "blue"):
            await ctx.send("❌ Winner invalide : `orange` ou `blue`.")
            return
        match = await self.bot.db.get_match(match_id)
        if not match:
            await ctx.send(f"❌ Match #{match_id} introuvable.")
            return
        if match["status"] not in ("draft", "active"):
            await ctx.send(f"❌ Match #{match_id} déjà terminé/annulé.")
            return

        matchmaking = self.bot.get_cog("Matchmaking")
        if matchmaking:
            await matchmaking._reporter(ctx, match_id, winner)
        else:
            await ctx.send("❌ Cog Matchmaking introuvable.")

    # ── !help6mans ────────────────────────────────────────────────────────────

    @commands.command(name="help6mans", aliases=["commandes", "aide"])
    async def help6mans(self, ctx: commands.Context):
        """Liste toutes les commandes du bot DME 6Mans."""
        embed = discord.Embed(
            title="📋 COMMANDES DME 6Mans",
            color=0xE67E22,
        )
        embed.add_field(
            name="🎮 Inscription",
            value=(
                "`!rc <plateforme> <pseudo>` — Inscription via tracker.gg\n"
                "Plateformes : `epic` · `steam` · `psn` · `xbox` · `switch`\n"
                "`!resetme` — Supprimer ton profil pour te réinscrire"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔁 Files",
            value=(
                "`!q` — Rejoindre ta file (channel de file requis)\n"
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
                "`!stats [@joueur]` — Profil complet\n"
                "`!top [nb]` — Classement MMR\n"
                "`!history [@joueur]` — Derniers matchs\n"
                "`!mmrhistory [@joueur]` — Évolution MMR\n"
                "`!whois [@joueur]` — Infos tracker\n"
                "`!season` — Saison active"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔑 Admin",
            value=(
                "`!setmmr @joueur <mmr>` — Modifier le MMR\n"
                "`!banqueue @joueur [heures] [raison]` — Bannir de la queue\n"
                "`!unbanqueue @joueur` — Lever un ban\n"
                "`!bans` — Voir les bans actifs\n"
                "`!mmrlogs @joueur` — Logs MMR (15 derniers)\n"
                "`!resetplayer @joueur` — Reset complet\n"
                "`!clearqueue [queue]` — Vider une file\n"
                "`!newseason <nom>` — Nouvelle saison\n"
                "`!forcematch <queue>` — Forcer un draft\n"
                "`!autofill <queue>` — Auto-balance\n"
                "`!cancelmatch <id>` — Annuler un match\n"
                "`!forceresult <id> <orange|blue>` — Forcer résultat\n"
                "`!approve @joueur <mmr>` — Valider inscription\n"
                "`!deny @joueur` — Refuser inscription\n"
                "`!pending` — Demandes en attente\n"
                "`!updateroles @joueur` — MAJ des rôles"
            ),
            inline=False,
        )
        embed.set_footer(text="DME 6Mans · Rocket League Compétitif")
        await ctx.send(embed=embed)

    # ── Gestion erreurs ───────────────────────────────────────────────────────

    @setmmr.error
    @ban_queue.error
    @unban_queue.error
    @bans.error
    @clearqueue.error
    @newseason.error
    @mmrlogs.error
    @forceresult.error
    async def admin_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Argument manquant. Utilise `!help6mans` pour voir les commandes.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Argument invalide.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
