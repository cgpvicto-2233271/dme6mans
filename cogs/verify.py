"""
cogs/verify.py  —  DME 6Mans
Inscription via tracker.gg (API) avec fallback RLStats.
Détection anti-smurf intégrée.
!rc / !resetme / !resetplayer / !approve / !deny / !pending / !updateroles / !whois
"""

import re
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands

from utils.mmr import rang_dme, tracker_mmr_to_dme, mmr_depuis_rang_rl
from utils.logger import setup_logger
import utils.tracker as tracker_svc

log = setup_logger("verify")

# ── Mapping plateformes ───────────────────────────────────────────────────────

PLATEFORMES = {
    "epic":   "epic",
    "steam":  "steam",
    "psn":    "psn",
    "ps":     "psn",
    "ps4":    "psn",
    "ps5":    "psn",
    "xbox":   "xbl",
    "xbl":    "xbl",
    "switch": "nintendo-switch",
    "sw":     "nintendo-switch",
}

PLATEFORMES_LABEL = {
    "epic":           "Epic",
    "steam":          "Steam",
    "psn":            "PSN",
    "xbl":            "Xbox",
    "nintendo-switch": "Switch",
}

SCRAPE_PLATEFORMES = {
    "epic":           "Epic",
    "steam":          "Steam",
    "psn":            "PS4",
    "xbl":            "XboxOne",
    "nintendo-switch": "Switch",
}

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Rôles ─────────────────────────────────────────────────────────────────────

ROLES_FILE      = [(1900, "6Mans SSL"), (1500, "6Mans GC+"), (1200, "6Mans Champion+"), (0, "6Mans Open")]
TOUS_ROLES_FILE = ["6Mans Open", "6Mans Champion+", "6Mans GC+", "6Mans SSL"]
ROLES_RANG      = [
    (2100, 200, "Rang SS"), (1900, 150, "Rang S"), (1700, 100, "Rang A"),
    (1500,  75, "Rang B"),  (1350,  50, "Rang C"), (1200,  30, "Rang D"),
    (1050,  15, "Rang E"),  (900,    5, "Rang F"),  (0,     0, "Rang F"),
]
TOUS_ROLES_RANG = ["Rang F", "Rang E", "Rang D", "Rang C", "Rang B", "Rang A", "Rang S", "Rang SS"]
ROLE_JOUEUR     = "6Mans Joueur"

SMURF_THRESHOLD = 0.6  # Score ≥ 0.6 → alerte smurf


def _get_role_file(mmr: int) -> str:
    for seuil, nom in ROLES_FILE:
        if mmr >= seuil:
            return nom
    return "6Mans Open"


def _get_role_rang(mmr: int, wins: int) -> str:
    for mmr_min, wins_min, nom in ROLES_RANG:
        if mmr >= mmr_min and wins >= wins_min:
            return nom
    return "Rang F"


async def _assigner_roles(member: discord.Member, mmr: int, wins: int):
    guild = member.guild
    a_ajouter, a_retirer = [], []

    role_joueur = discord.utils.get(guild.roles, name=ROLE_JOUEUR)
    if role_joueur and role_joueur not in member.roles:
        a_ajouter.append(role_joueur)

    nom_file = _get_role_file(mmr)
    for nom in TOUS_ROLES_FILE:
        role = discord.utils.get(guild.roles, name=nom)
        if not role:
            continue
        if nom == nom_file:
            if role not in member.roles:
                a_ajouter.append(role)
        else:
            if role in member.roles:
                a_retirer.append(role)

    nom_rang = _get_role_rang(mmr, wins)
    for nom in TOUS_ROLES_RANG:
        role = discord.utils.get(guild.roles, name=nom)
        if not role:
            continue
        if nom == nom_rang:
            if role not in member.roles:
                a_ajouter.append(role)
        else:
            if role in member.roles:
                a_retirer.append(role)

    if a_retirer:
        await member.remove_roles(*a_retirer, reason="DME 6Mans - MAJ rang")
    if a_ajouter:
        await member.add_roles(*a_ajouter, reason="DME 6Mans - MAJ rang")
    return nom_file, nom_rang


async def _retirer_tous_roles(member: discord.Member):
    guild = member.guild
    a_retirer = []
    tous = TOUS_ROLES_FILE + TOUS_ROLES_RANG + [ROLE_JOUEUR]
    for nom in tous:
        role = discord.utils.get(guild.roles, name=nom)
        if role and role in member.roles:
            a_retirer.append(role)
    if a_retirer:
        await member.remove_roles(*a_retirer, reason="DME 6Mans - reset joueur")


# ── Fallback RLStats ──────────────────────────────────────────────────────────

def _rlstats_url(slug: str, pseudo: str) -> str:
    return f"https://rlstats.net/profile/{slug}/{quote(pseudo)}"


def _extraire_peak_rlstats(html: str):
    tables = re.findall(
        r"<tr><th>1v1 Duel</th><th>2v2 Doubles</th><th>3v3 Standard</th>.*?</table>",
        html, re.DOTALL,
    )
    all_ratings = {}
    for idx, table in enumerate(tables[:3]):
        ratings_row = re.findall(
            r"<mmr[^>]*>~\d+</mmr>\s*(\d{3,4})\s*<mmr", table
        )
        if not ratings_row:
            ratings_row = re.findall(r"<td>(\d{3,4})</td>", table)
        if len(ratings_row) >= 3:
            doubles  = int(ratings_row[1])
            standard = int(ratings_row[2])
            ratings = {}
            if 900 <= doubles  <= 2500:
                ratings["doubles"]  = doubles
            if 900 <= standard <= 2500:
                ratings["standard"] = standard
            if ratings:
                all_ratings[idx] = ratings

    peak_mmr, peak_info = 0, {}
    for saison_idx, ratings in all_ratings.items():
        for mode, mmr in ratings.items():
            if mmr > peak_mmr:
                peak_mmr = mmr
                peak_info = {"saison": saison_idx, "mode": mode, "mmr": mmr}

    return all_ratings, peak_info


# ── Cog principal ─────────────────────────────────────────────────────────────

class VerifyCog(commands.Cog, name="Verify"):
    def __init__(self, bot):
        self.bot = bot
        self.pending: dict = {}

    def _get_admin_channel(self, guild: discord.Guild):
        for ch in guild.text_channels:
            if ch.name.lower() == "6mans-admin":
                return ch
        for ch in guild.text_channels:
            if "6mans" in ch.name.lower() and "admin" in ch.name.lower():
                return ch
        for ch in guild.text_channels:
            if any(k in ch.name.lower() for k in ["admin", "staff", "mod"]):
                return ch
        return None

    # ── !rc ───────────────────────────────────────────────────────────────────

    @commands.command(name="rc", aliases=["rankcheck", "verify", "inscription"])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def rank_check(self, ctx: commands.Context, plateforme: str = "", *, pseudo: str = ""):
        """Inscription via tracker.gg. Usage : !rc <plateforme> <pseudo>"""
        if not plateforme or not pseudo:
            embed = discord.Embed(
                title="📋 Inscription DME 6Mans",
                color=0xE67E22,
            )
            embed.add_field(
                name="Commande",
                value=(
                    "`!rc <plateforme> <pseudo>`\n\n"
                    "**Plateformes :** `epic` · `steam` · `psn` · `xbox` · `switch`\n\n"
                    "**Exemples :**\n"
                    "`!rc epic Coussinho`\n"
                    "`!rc steam MonPseudo`\n"
                    "`!rc psn MonPseudo`"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        slug = PLATEFORMES.get(plateforme.lower())
        if not slug:
            await ctx.send(
                "❌ Plateforme inconnue. Choix : `epic`, `steam`, `psn`, `xbox`, `switch`"
            )
            return

        # Déjà inscrit ?
        joueur = await self.bot.db.get_player(ctx.author.id)
        if joueur:
            rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
            await ctx.send(
                f"✅ Tu es déjà inscrit ! {emoji} **{rang}** — **{joueur['mmr']} MMR**\n"
                f"Utilise `!resetme` pour te réinscrire ou contacte un admin."
            )
            return

        if ctx.author.id in self.pending:
            await ctx.send("⏳ Ta demande est déjà en attente de validation.")
            return

        msg = await ctx.send(f"🔍 Recherche du profil **{pseudo}** sur tracker.gg...")

        # ── Tentative tracker.gg ──────────────────────────────────────────
        profile = await tracker_svc.fetch_profile(slug, pseudo)

        if profile and profile.best_mmr > 0:
            mmr_depart = tracker_mmr_to_dme(profile.best_mmr)
            tracker_url = profile.profile_url()

            log.info(
                "RC via tracker.gg: %s (%d) → tracker=%d → dme=%d",
                pseudo, ctx.author.id, profile.best_mmr, mmr_depart,
            )

            # Anti-smurf
            smurf = profile.smurf_score(mmr_depart)
            if smurf >= SMURF_THRESHOLD:
                await self._alerte_smurf(ctx, pseudo, plateforme, slug, profile, mmr_depart)

            await self._inscrire(
                ctx, msg, ctx.author, pseudo, plateforme, slug,
                tracker_url, mmr_depart,
                tracker_rank=profile.best_rank,
                tracker_mmr=profile.best_mmr,
                source="tracker.gg",
            )
            return

        # ── Fallback RLStats ──────────────────────────────────────────────
        await msg.edit(content=f"🔍 tracker.gg indisponible, tentative via RLStats...")
        rlstats_slug = SCRAPE_PLATEFORMES.get(slug, slug)
        url_rlstats = _rlstats_url(rlstats_slug, pseudo)

        try:
            async with aiohttp.ClientSession(headers=SCRAPE_HEADERS) as session:
                async with session.get(
                    url_rlstats, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 404:
                        await self._envoyer_en_pending(ctx, msg, pseudo, plateforme, slug, url_rlstats)
                        return
                    if resp.status != 200:
                        await msg.edit(
                            content=f"❌ Erreur {resp.status} sur RLStats. Réessaie."
                        )
                        return
                    html = await resp.text()
        except Exception as exc:
            log.error("RLStats scrape error: %s", exc)
            await self._envoyer_en_pending(ctx, msg, pseudo, plateforme, slug, url_rlstats)
            return

        all_ratings, peak_info = _extraire_peak_rlstats(html)
        if not peak_info:
            await self._envoyer_en_pending(ctx, msg, pseudo, plateforme, slug, url_rlstats)
            return

        mmr_depart = peak_info["mmr"]
        saison_label = ["saison actuelle", "saison -1", "saison -2"][peak_info["saison"]]
        mode_label = "3v3 Standard" if peak_info["mode"] == "standard" else "2v2 Doubles"

        log.info(
            "RC via RLStats: %s (%d) → peak=%d (%s %s)",
            pseudo, ctx.author.id, mmr_depart, mode_label, saison_label,
        )

        await self._inscrire(
            ctx, msg, ctx.author, pseudo, plateforme, slug,
            url_rlstats, mmr_depart,
            saison_label=saison_label,
            mode_label=mode_label,
            source="RLStats",
        )

    async def _envoyer_en_pending(self, ctx, msg, pseudo, plateforme, slug, url):
        self.pending[ctx.author.id] = {
            "pseudo": pseudo,
            "plateforme": plateforme.lower(),
            "slug": slug,
            "url": url,
            "member": ctx.author,
        }
        admin_ch = self._get_admin_channel(ctx.guild)
        if admin_ch:
            embed = discord.Embed(
                title="⚠️ Demande d'inscription — rang non détecté",
                color=0xE67E22,
            )
            embed.add_field(name="Joueur",     value=f"<@{ctx.author.id}>", inline=True)
            embed.add_field(name="Plateforme", value=plateforme.upper(),    inline=True)
            embed.add_field(name="Pseudo RL",  value=f"`{pseudo}`",         inline=True)
            embed.add_field(name="Profil",     value=f"[Voir]({url})",      inline=False)
            embed.add_field(
                name="Action",
                value=(
                    f"`!approve <@{ctx.author.id}> <mmr>` — Valider\n"
                    f"`!deny <@{ctx.author.id}>` — Refuser"
                ),
                inline=False,
            )
            embed.set_thumbnail(url=ctx.author.display_avatar.url)
            await admin_ch.send(embed=embed)

        await msg.edit(
            content=(
                "⚠️ Rang non détecté automatiquement.\n"
                "Un admin va vérifier ton profil et te valider manuellement."
            )
        )

    async def _alerte_smurf(self, ctx, pseudo, plateforme, slug, profile, mmr_dme):
        admin_ch = self._get_admin_channel(ctx.guild)
        if not admin_ch:
            return
        smurf_pct = round(profile.smurf_score(mmr_dme) * 100)
        embed = discord.Embed(
            title="🚨 Alerte Smurf Potentiel",
            color=0xED4245,
        )
        embed.add_field(name="Joueur",       value=f"<@{ctx.author.id}>",       inline=True)
        embed.add_field(name="Pseudo RL",    value=f"`{pseudo}` ({plateforme})", inline=True)
        embed.add_field(name="Suspicion",    value=f"**{smurf_pct}%**",          inline=True)
        embed.add_field(
            name="Tracker",
            value=f"3v3: **{profile.standard_mmr or 'N/A'}** · 2v2: **{profile.doubles_mmr or 'N/A'}**",
            inline=True,
        )
        embed.add_field(name="MMR DME attribué", value=f"**{mmr_dme}**",         inline=True)
        embed.add_field(
            name="Action suggérée",
            value=(
                f"`!setmmr <@{ctx.author.id}> <mmr>` pour ajuster\n"
                f"`!banqueue <@{ctx.author.id}> [raison]` si nécessaire"
            ),
            inline=False,
        )
        embed.set_footer(text="Le joueur a été inscrit normalement — vérification recommandée")
        await admin_ch.send(embed=embed)
        log.warning(
            "Smurf alert: %s (%d) | tracker=%d | dme=%d",
            pseudo, ctx.author.id, profile.best_mmr, mmr_dme,
        )

    async def _inscrire(
        self, ctx, msg, member: discord.Member, pseudo, plateforme, slug, url,
        mmr, saison_label="", mode_label="",
        tracker_rank=None, tracker_mmr=None, source="",
    ):
        await self.bot.db.get_or_create_player(member.id, member.display_name)
        await self.bot.db.set_mmr(member.id, mmr, reason="inscription")
        await self.bot.db.save_tracker_link(
            member.id, url, plateforme,
            platform=slug,
            rl_username=pseudo,
            rl_mmr=tracker_mmr,
        )
        if tracker_rank and tracker_mmr:
            await self.bot.db.update_tracker_data(member.id, tracker_rank, tracker_mmr)

        self.pending.pop(member.id, None)

        try:
            nom_file, nom_rang = await _assigner_roles(member, mmr, 0)
            roles_ok = True
        except discord.Forbidden:
            nom_file = _get_role_file(mmr)
            nom_rang = _get_role_rang(mmr, 0)
            roles_ok = False

        rang, emoji = rang_dme(mmr, 0)

        embed = discord.Embed(
            title="✅ Nouveau joueur inscrit !",
            description=f"<@{member.id}> a rejoint le DME 6Mans.",
            color=0x2ECC71,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Plateforme", value=PLATEFORMES_LABEL.get(slug, plateforme.upper()), inline=True)
        embed.add_field(name="Pseudo RL",  value=f"`{pseudo}`",                                   inline=True)
        embed.add_field(name="MMR DME",    value=f"**{mmr}**",                                    inline=True)
        embed.add_field(name="Rang DME",   value=f"{emoji} **{rang}**",                           inline=True)
        embed.add_field(name="File",       value=f"`{nom_file}`",                                 inline=True)
        if source:
            embed.add_field(name="Source", value=source, inline=True)
        if tracker_rank:
            embed.add_field(name="Rang Tracker", value=f"**{tracker_rank}** ({tracker_mmr} MMR)", inline=False)
        elif saison_label and mode_label:
            embed.add_field(
                name="Peak utilisé",
                value=f"**{mmr}** ({mode_label} — {saison_label})",
                inline=False,
            )
        if not roles_ok:
            embed.add_field(
                name="⚠️ Attention",
                value="Rôles non assignés (permissions insuffisantes).",
                inline=False,
            )
        embed.set_footer(text="!q pour rejoindre une file · !stats pour ton profil")

        if msg:
            await msg.edit(content=None, embed=embed)
        else:
            await ctx.send(embed=embed)

        try:
            dm = discord.Embed(title="🎮 Inscription validée !", color=0x2ECC71)
            dm.add_field(name="MMR de départ", value=f"**{mmr}**",          inline=True)
            dm.add_field(name="Rang DME",      value=f"{emoji} **{rang}**", inline=True)
            dm.add_field(name="File assignée", value=f"`{nom_file}`",       inline=True)
            dm.set_footer(text="!q pour jouer · !stats · !history · !help6mans")
            await member.send(embed=dm)
        except discord.Forbidden:
            pass

        log.info("Inscrit: %s (%d) | %s | %d MMR | source=%s", pseudo, member.id, slug, mmr, source)

    @rank_check.error
    async def rc_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Attends encore **{error.retry_after:.0f}s** avant de réessayer.")

    # ── !resetme ──────────────────────────────────────────────────────────────

    @commands.command(name="resetme")
    @commands.cooldown(1, 300, commands.BucketType.user)
    async def resetme(self, ctx: commands.Context):
        """Supprimer ton profil pour te réinscrire. Usage : !resetme"""
        joueur = await self.bot.db.get_player(ctx.author.id)
        if not joueur:
            await ctx.send("❌ Tu n'as pas de profil enregistré.")
            return

        await ctx.send(
            "⚠️ Es-tu sûr de vouloir supprimer ton profil DME 6Mans ?\n"
            "Réponds `oui` dans les 30 secondes pour confirmer."
        )

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.lower() == "oui"
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=30)
        except Exception:
            await ctx.send("Annulé.")
            return

        await self._reset_joueur(ctx.author)
        await ctx.send("✅ Profil supprimé. Utilise `!rc <plateforme> <pseudo>` pour te réinscrire.")
        log.info("Reset: %s (%d) via !resetme", ctx.author.display_name, ctx.author.id)

    # ── !resetplayer ──────────────────────────────────────────────────────────

    @commands.command(name="resetplayer", aliases=["reset"])
    @commands.has_permissions(administrator=True)
    async def resetplayer(self, ctx: commands.Context, member: discord.Member):
        """[Admin] Reset complet d'un joueur. Usage : !resetplayer @joueur"""
        await self._reset_joueur(member)
        await ctx.send(f"✅ Profil de **{member.display_name}** réinitialisé.")
        log.info("Reset admin: %s (%d) par %s", member.display_name, member.id, ctx.author)

    async def _reset_joueur(self, member: discord.Member):
        await self.bot.db.queue_leave(member.id)
        await self.bot.db.delete_player(member.id)
        await self.bot.db.delete_tracker_link(member.id)
        try:
            await _retirer_tous_roles(member)
        except discord.Forbidden:
            pass
        self.pending.pop(member.id, None)

    # ── !approve ──────────────────────────────────────────────────────────────

    @commands.command(name="approve", aliases=["valider"])
    @commands.has_permissions(administrator=True)
    async def approve(self, ctx: commands.Context, member: discord.Member, mmr: int):
        """[Admin] Valider manuellement un joueur. Usage : !approve @joueur <mmr>"""
        if mmr < 100 or mmr > 3000:
            await ctx.send("❌ MMR invalide. Range : 100–3000")
            return
        demande    = self.pending.get(member.id)
        pseudo     = demande["pseudo"]              if demande else member.display_name
        plateforme = demande["plateforme"]          if demande else "manuel"
        slug       = demande.get("slug", "epic")   if demande else "epic"
        url        = demande["url"]                 if demande else ""
        await self._inscrire(ctx, None, member, pseudo, plateforme, slug, url, mmr)

    # ── !deny ─────────────────────────────────────────────────────────────────

    @commands.command(name="deny", aliases=["refuser"])
    @commands.has_permissions(administrator=True)
    async def deny(self, ctx: commands.Context, member: discord.Member, *, raison: str = "Aucune raison fournie."):
        """[Admin] Refuser une demande. Usage : !deny @joueur [raison]"""
        self.pending.pop(member.id, None)
        try:
            await member.send(
                f"❌ Ta demande DME 6Mans a été refusée.\nRaison : {raison}"
            )
        except discord.Forbidden:
            pass
        await ctx.send(f"✅ Demande de **{member.display_name}** refusée.")

    # ── !pending ──────────────────────────────────────────────────────────────

    @commands.command(name="pending", aliases=["enattente"])
    @commands.has_permissions(administrator=True)
    async def pending_list(self, ctx: commands.Context):
        """[Admin] Demandes en attente. Usage : !pending"""
        if not self.pending:
            await ctx.send("✅ Aucune demande en attente.")
            return
        embed = discord.Embed(
            title=f"⏳ Demandes en attente ({len(self.pending)})",
            color=0xE67E22,
        )
        for did, data in self.pending.items():
            embed.add_field(
                name=data["member"].display_name,
                value=(
                    f"<@{did}> — `{data['plateforme'].upper()}` — `{data['pseudo']}`\n"
                    f"[Profil]({data['url']})\n"
                    f"`!approve <@{did}> <mmr>` · `!deny <@{did}>`"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    # ── !updateroles ──────────────────────────────────────────────────────────

    @commands.command(name="updateroles", aliases=["majroles"])
    @commands.has_permissions(administrator=True)
    async def update_roles(self, ctx: commands.Context, member: discord.Member):
        """[Admin] Forcer MAJ des rôles. Usage : !updateroles @joueur"""
        joueur = await self.bot.db.get_player(member.id)
        if not joueur:
            await ctx.send(f"❌ **{member.display_name}** n'est pas inscrit.")
            return
        try:
            nom_file, nom_rang = await _assigner_roles(member, joueur["mmr"], joueur.get("wins", 0))
            rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
            await ctx.send(
                f"✅ Rôles mis à jour : `{nom_file}` · `{nom_rang}` "
                f"({emoji} {rang} — **{joueur['mmr']}** MMR)"
            )
        except discord.Forbidden:
            await ctx.send("❌ Permissions insuffisantes pour modifier les rôles.")

    # ── !whois ────────────────────────────────────────────────────────────────

    @commands.command(name="whois")
    async def whois(self, ctx: commands.Context, member: discord.Member = None):
        """Voir le profil complet d'un joueur. Usage : !whois [@joueur]"""
        cible  = member or ctx.author
        joueur = await self.bot.db.get_player(cible.id)
        if not joueur:
            await ctx.send(f"❌ **{cible.display_name}** n'est pas inscrit. Utilise `!rc`.")
            return

        lien = await self.bot.db.get_tracker_link(cible.id)
        rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
        wins   = joueur.get("wins", 0)
        losses = joueur.get("losses", 0)
        total  = wins + losses
        wr     = f"{(wins / total * 100):.1f}%" if total else "N/A"

        embed = discord.Embed(
            title=f"👤 {cible.display_name}",
            color=0x5865F2,
        )
        embed.set_thumbnail(url=cible.display_avatar.url)
        embed.add_field(name="MMR",        value=f"**{joueur['mmr']}**",       inline=True)
        embed.add_field(name="Rang DME",   value=f"{emoji} **{rang}**",        inline=True)
        embed.add_field(name="Record",     value=f"**{wins}**W / **{losses}**L", inline=True)
        embed.add_field(name="Winrate",    value=f"**{wr}**",                  inline=True)
        embed.add_field(name="Matchs",     value=f"**{total}**",               inline=True)

        if lien:
            platform_label = PLATEFORMES_LABEL.get(lien.get("platform") or lien.get("rl_rank", ""), "?")
            pseudo = lien.get("rl_username") or "?"
            embed.add_field(
                name="Compte RL",
                value=f"`{platform_label}` — `{pseudo}`",
                inline=True,
            )
            if lien.get("rl_rank") and lien.get("rl_mmr"):
                embed.add_field(
                    name="Rang Tracker",
                    value=f"**{lien['rl_rank']}** ({lien['rl_mmr']} MMR)",
                    inline=True,
                )
            embed.add_field(
                name="Profil tracker.gg",
                value=f"[Voir le profil]({lien['tracker_url']})",
                inline=False,
            )
        await ctx.send(embed=embed)

    @approve.error
    @deny.error
    @pending_list.error
    @update_roles.error
    @resetplayer.error
    async def admin_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Tu n'as pas la permission.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Argument manquant. Utilise `!help6mans`.")


async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))
