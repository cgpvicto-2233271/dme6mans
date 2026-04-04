"""
cogs/verify.py  -  DME 6Mans
Scraping RLStats - peak 2v2 + 3v3 sur les 3 dernieres saisons.
Fix: extraction precise des colonnes Doubles et Standard uniquement.
"""

import re
from urllib.parse import quote
import aiohttp
import discord
from discord.ext import commands
from utils.mmr import rang_dme

PLATEFORMES = {
    "epic": "Epic", "steam": "Steam",
    "psn": "PS4", "ps": "PS4", "ps4": "PS4", "ps5": "PS4",
    "xbox": "XboxOne", "xbl": "XboxOne",
    "switch": "Switch", "sw": "Switch",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

ROLES_FILE      = [(1900,"6Mans SSL"),(1500,"6Mans GC+"),(1200,"6Mans Champion+"),(0,"6Mans Open")]
TOUS_ROLES_FILE = ["6Mans Open","6Mans Champion+","6Mans GC+","6Mans SSL"]
ROLES_RANG      = [
    (2100,200,"Rang SS"),(1900,150,"Rang S"),(1700,100,"Rang A"),
    (1500,75,"Rang B"),(1350,50,"Rang C"),(1200,30,"Rang D"),
    (1050,15,"Rang E"),(900,5,"Rang F"),(0,0,"Rang F"),
]
TOUS_ROLES_RANG = ["Rang F","Rang E","Rang D","Rang C","Rang B","Rang A","Rang S","Rang SS"]
ROLE_JOUEUR     = "6Mans Joueur"


def _get_role_file(mmr):
    for seuil, nom in ROLES_FILE:
        if mmr >= seuil: return nom
    return "6Mans Open"

def _get_role_rang(mmr, wins):
    for mmr_min, wins_min, nom in ROLES_RANG:
        if mmr >= mmr_min and wins >= wins_min: return nom
    return "Rang F"

def _rlstats_url(slug, pseudo):
    return f"https://rlstats.net/profile/{slug}/{quote(pseudo)}"


def _extraire_peak_3saisons(html):
    """
    Scanne les 3 premiers tableaux Duel/Doubles/Standard de RLStats.
    Format A : ratings dans balises <mmr>
    Format B : ratings directs dans <td>
    Ordre : index 0=Duel, index 1=Doubles, index 2=Standard
    """
    tables = re.findall(
        r'<tr><th>1v1 Duel</th><th>2v2 Doubles</th><th>3v3 Standard</th>.*?</table>',
        html, re.DOTALL
    )

    all_ratings = {}
    for idx, table in enumerate(tables[:3]):
        # Format A : <mmr ...>~N</mmr> RATING <mmr ...>~N</mmr>
        ratings_row = re.findall(r'<mmr[^>]*>~\d+</mmr>\s*(\d{3,4})\s*<mmr', table)
        # Format B : <td>RATING</td>
        if not ratings_row:
            ratings_row = re.findall(r'<td>(\d{3,4})</td>', table)

        if len(ratings_row) >= 3:
            doubles  = int(ratings_row[1])
            standard = int(ratings_row[2])
            ratings = {}
            if 900 <= doubles  <= 2500: ratings["doubles"]  = doubles
            if 900 <= standard <= 2500: ratings["standard"] = standard
            if ratings:
                all_ratings[idx] = ratings

    peak_mmr, peak_info = 0, {}
    for saison_idx, ratings in all_ratings.items():
        for mode, mmr in ratings.items():
            if mmr > peak_mmr:
                peak_mmr  = mmr
                peak_info = {"saison": saison_idx, "mode": mode, "mmr": mmr}

    return all_ratings, peak_info


async def _assigner_roles(member, mmr, wins):
    guild = member.guild
    a_ajouter, a_retirer = [], []

    role_joueur = discord.utils.get(guild.roles, name=ROLE_JOUEUR)
    if role_joueur and role_joueur not in member.roles:
        a_ajouter.append(role_joueur)

    nom_file = _get_role_file(mmr)
    for nom in TOUS_ROLES_FILE:
        role = discord.utils.get(guild.roles, name=nom)
        if not role: continue
        if nom == nom_file:
            if role not in member.roles: a_ajouter.append(role)
        else:
            if role in member.roles: a_retirer.append(role)

    nom_rang = _get_role_rang(mmr, wins)
    for nom in TOUS_ROLES_RANG:
        role = discord.utils.get(guild.roles, name=nom)
        if not role: continue
        if nom == nom_rang:
            if role not in member.roles: a_ajouter.append(role)
        else:
            if role in member.roles: a_retirer.append(role)

    if a_retirer: await member.remove_roles(*a_retirer, reason="DME 6Mans - MAJ rang")
    if a_ajouter: await member.add_roles(*a_ajouter, reason="DME 6Mans - MAJ rang")
    return nom_file, nom_rang


async def _retirer_tous_roles(member):
    """Retire tous les roles 6Mans d'un membre."""
    guild = member.guild
    a_retirer = []
    tous = TOUS_ROLES_FILE + TOUS_ROLES_RANG + [ROLE_JOUEUR]
    for nom in tous:
        role = discord.utils.get(guild.roles, name=nom)
        if role and role in member.roles:
            a_retirer.append(role)
    if a_retirer:
        await member.remove_roles(*a_retirer, reason="DME 6Mans - reset joueur")


class VerifyCog(commands.Cog, name="Verify"):
    def __init__(self, bot):
        self.bot = bot
        self.pending = {}

    def _get_admin_channel(self, guild):
    # Chercher d'abord exactement "6mans-admin"
        for ch in guild.text_channels:
            if ch.name.lower() == "6mans-admin":
                return ch
    # Fallback : contient "6mans" et "admin"
        for ch in guild.text_channels:
            if "6mans" in ch.name.lower() and "admin" in ch.name.lower():
                return ch
    # Dernier fallback : n'importe quel channel admin
        keywords = ["admin", "staff", "mod", "verify"]
        for ch in guild.text_channels:
            if any(k in ch.name.lower() for k in keywords):
                return ch
        return None

    # ── !rc ────────────────────────────────────────────────────────────────
    @commands.command(name="rc", aliases=["rankcheck","verify","inscription"])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def rank_check(self, ctx, plateforme="", *, pseudo=""):
        """Inscription via RLStats (peak 3 dernieres saisons). Usage : !rc <plateforme> <pseudo>"""
        if not plateforme or not pseudo:
            embed = discord.Embed(title="Inscription DME 6Mans", color=discord.Color.orange())
            embed.add_field(
                name="Commande",
                value=(
                    "`!rc <plateforme> <pseudo>`\n\n"
                    "**Plateformes :** `epic` - `steam` - `psn` - `xbox` - `switch`\n\n"
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
            await ctx.send("Plateforme inconnue. Choix : `epic`, `steam`, `psn`, `xbox`, `switch`")
            return

        joueur = await self.bot.db.get_player(ctx.author.id)
        if joueur:
            rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
            await ctx.send(
                f"Tu es deja inscrit ! {emoji} **{rang}** - **{joueur['mmr']} MMR**\n"
                f"Utilise `!resetme` pour te reininscrire ou contacte un admin."
            )
            return

        if ctx.author.id in self.pending:
            await ctx.send("Ta demande est deja en attente de validation.")
            return

        url = _rlstats_url(slug, pseudo)
        msg = await ctx.send(f"Recherche du profil **{pseudo}** sur RLStats...")

        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 404:
                        await msg.edit(content=f"Profil `{pseudo}` introuvable sur `{plateforme.upper()}`.")
                        return
                    if resp.status != 200:
                        await msg.edit(content=f"Erreur {resp.status} sur RLStats. Reessaie.")
                        return
                    html = await resp.text()
        except Exception as e:
            await msg.edit(content=f"Erreur de connexion : `{e}`")
            return

        with open("debug_rlstats.html", "w", encoding="utf-8") as f:
            f.write(html)

        all_ratings, peak_info = _extraire_peak_3saisons(html)

        if not peak_info:
            self.pending[ctx.author.id] = {
                "pseudo": pseudo, "plateforme": plateforme.lower(),
                "slug": slug, "url": url, "member": ctx.author,
            }
            admin_ch = self._get_admin_channel(ctx.guild)
            if admin_ch:
                embed = discord.Embed(title="Demande d'inscription (rang non detecte)", color=discord.Color.orange())
                embed.add_field(name="Joueur",     value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Plateforme", value=plateforme.upper(),    inline=True)
                embed.add_field(name="Pseudo RL",  value=f"`{pseudo}`",         inline=True)
                embed.add_field(name="RLStats",    value=f"[Profil]({url})",    inline=False)
                embed.add_field(name="Action",
                    value=f"`!approve <@{ctx.author.id}> <mmr>` - `!deny <@{ctx.author.id}>`",
                    inline=False)
                embed.set_thumbnail(url=ctx.author.display_avatar.url)
                await admin_ch.send(embed=embed)
            await msg.edit(content="Rang non detecte automatiquement. Un admin va verifier ton profil.")
            return

        mmr_depart   = peak_info["mmr"]
        saison_label = ["saison actuelle","saison -1","saison -2"][peak_info["saison"]]
        mode_label   = "3v3 Standard" if peak_info["mode"] == "standard" else "2v2 Doubles"

        await self._inscrire(ctx, msg, ctx.author, pseudo, plateforme, slug, url,
                             mmr_depart, all_ratings, saison_label, mode_label)

    async def _inscrire(self, ctx, msg, member, pseudo, plateforme, slug, url,
                        mmr, all_ratings, saison_label="", mode_label=""):
        await self.bot.db.get_or_create_player(member.id, member.display_name)
        await self.bot.db.set_mmr(member.id, mmr)
        await self.bot.db.save_tracker_link(member.id, url, plateforme)
        self.pending.pop(member.id, None)

        try:
            nom_file, nom_rang = await _assigner_roles(member, mmr, 0)
            roles_ok = True
        except discord.Forbidden:
            nom_file = _get_role_file(mmr)
            nom_rang = _get_role_rang(mmr, 0)
            roles_ok = False

        rang, emoji = rang_dme(mmr, 0)

        saison_noms = ["Saison actuelle","Saison -1","Saison -2"]
        ratings_lines = []
        for idx, ratings in all_ratings.items():
            line = f"**{saison_noms[idx]}** :"
            if "doubles"  in ratings: line += f" 2v2={ratings['doubles']}"
            if "standard" in ratings: line += f" 3v3={ratings['standard']}"
            ratings_lines.append(line)

        embed = discord.Embed(
            title="Nouveau joueur inscrit !",
            description=f"<@{member.id}> a rejoint le DME 6Mans.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Plateforme", value=plateforme.upper(), inline=True)
        embed.add_field(name="Pseudo RL",  value=f"`{pseudo}`",      inline=True)
        embed.add_field(name="MMR DME",    value=f"**{mmr}**",       inline=True)
        embed.add_field(name="Rang DME",   value=f"{emoji} {rang}",  inline=True)
        embed.add_field(name="File",       value=f"`{nom_file}`",    inline=True)
        if saison_label and mode_label:
            embed.add_field(name="Peak utilise",
                value=f"**{mmr}** ({mode_label} - {saison_label})", inline=False)
        if ratings_lines:
            embed.add_field(name="Ratings detectes",
                value="\n".join(ratings_lines), inline=False)
        if not roles_ok:
            embed.add_field(name="Attention",
                value="Roles non assignes (permissions insuffisantes).", inline=False)
        embed.set_footer(text="!q pour rejoindre une file")

        if msg:
            await msg.edit(content=None, embed=embed)
        else:
            await ctx.send(embed=embed)

        try:
            dm = discord.Embed(title="Inscription validee !", color=discord.Color.green())
            dm.add_field(name="MMR de depart", value=f"**{mmr}**",          inline=True)
            dm.add_field(name="Rang DME",      value=f"{emoji} **{rang}**", inline=True)
            dm.add_field(name="File assignee", value=f"`{nom_file}`",       inline=True)
            if saison_label and mode_label:
                dm.add_field(name="Peak", value=f"{mode_label} - {saison_label}", inline=False)
            dm.set_footer(text="!q pour jouer - !stats - !help6mans")
            await member.send(embed=dm)
        except discord.Forbidden:
            pass

    @rank_check.error
    async def rc_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Attends encore {error.retry_after:.0f}s.")

    # ── !resetme ───────────────────────────────────────────────────────────
    @commands.command(name="resetme")
    @commands.cooldown(1, 300, commands.BucketType.user)
    async def resetme(self, ctx):
        """Supprimer ton profil pour te reinscrire. Usage : !resetme"""
        joueur = await self.bot.db.get_player(ctx.author.id)
        if not joueur:
            await ctx.send("Tu n'as pas de profil enregistre.")
            return
        # Confirmation
        await ctx.send(
            "Es-tu sur de vouloir supprimer ton profil DME 6Mans ? "
            "Reponds `oui` dans les 30 secondes pour confirmer."
        )
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "oui"
        try:
            await self.bot.wait_for("message", check=check, timeout=30)
        except Exception:
            await ctx.send("Annule.")
            return

        await self._reset_joueur(ctx.author)
        await ctx.send(f"Profil supprime. Tu peux te reinscrire avec `!rc <plateforme> <pseudo>`.")

    # ── !resetplayer @joueur ───────────────────────────────────────────────
    @commands.command(name="resetplayer", aliases=["reset"])
    @commands.has_permissions(administrator=True)
    async def resetplayer(self, ctx, member: discord.Member):
        """[Admin] Reset complet d'un joueur. Usage : !resetplayer @joueur"""
        await self._reset_joueur(member)
        await ctx.send(f"Profil de **{member.display_name}** reinitialise completement.")

    async def _reset_joueur(self, member):
        """Reset complet : DB + tracker link + roles Discord."""
        # Retirer de la file si present
        await self.bot.db.queue_leave(member.id)
        # Reset les stats en base
        await self.bot.db.delete_player(member.id)
        # Supprimer le lien tracker
        await self.bot.db.delete_tracker_link(member.id)
        # Retirer tous les roles 6Mans
        try:
            await _retirer_tous_roles(member)
        except discord.Forbidden:
            pass
        # Retirer de la liste d'attente si present
        self.pending.pop(member.id, None)

    # ── !approve ───────────────────────────────────────────────────────────
    @commands.command(name="approve", aliases=["valider"])
    @commands.has_permissions(administrator=True)
    async def approve(self, ctx, member: discord.Member, mmr: int):
        """[Admin] Override manuel du MMR. Usage : !approve @joueur <mmr>"""
        if mmr < 100 or mmr > 3000:
            await ctx.send("MMR invalide. Range : 100 - 3000")
            return
        demande    = self.pending.get(member.id)
        pseudo     = demande["pseudo"]             if demande else member.display_name
        plateforme = demande["plateforme"].upper() if demande else "Manuel"
        url        = demande["url"]                if demande else ""
        slug       = demande.get("slug","epic")    if demande else "epic"
        await self._inscrire(ctx, None, member, pseudo, plateforme, slug, url, mmr, {})

    # ── !deny ──────────────────────────────────────────────────────────────
    @commands.command(name="deny", aliases=["refuser"])
    @commands.has_permissions(administrator=True)
    async def deny(self, ctx, member: discord.Member, *, raison="Aucune raison fournie."):
        """[Admin] Refuser une demande. Usage : !deny @joueur [raison]"""
        self.pending.pop(member.id, None)
        try:
            await member.send(f"Ta demande DME 6Mans a ete refusee.\nRaison : {raison}")
        except discord.Forbidden:
            pass
        await ctx.send(f"Demande de **{member.display_name}** refusee.")

    # ── !pending ───────────────────────────────────────────────────────────
    @commands.command(name="pending", aliases=["enattente"])
    @commands.has_permissions(administrator=True)
    async def pending_list(self, ctx):
        """[Admin] Demandes en attente. Usage : !pending"""
        if not self.pending:
            await ctx.send("Aucune demande en attente.")
            return
        embed = discord.Embed(title=f"Demandes en attente ({len(self.pending)})", color=discord.Color.orange())
        for did, data in self.pending.items():
            embed.add_field(
                name=data["member"].display_name,
                value=(
                    f"<@{did}> - {data['plateforme'].upper()} - `{data['pseudo']}`\n"
                    f"[RLStats]({data['url']})\n"
                    f"`!approve <@{did}> <mmr>` - `!deny <@{did}>`"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    # ── !updateroles ───────────────────────────────────────────────────────
    @commands.command(name="updateroles", aliases=["majroles"])
    @commands.has_permissions(administrator=True)
    async def update_roles(self, ctx, member: discord.Member):
        """[Admin] Forcer MAJ des roles. Usage : !updateroles @joueur"""
        joueur = await self.bot.db.get_player(member.id)
        if not joueur:
            await ctx.send(f"**{member.display_name}** n'est pas inscrit.")
            return
        try:
            nom_file, nom_rang = await _assigner_roles(member, joueur["mmr"], joueur.get("wins",0))
            rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins",0))
            await ctx.send(
                f"Roles mis a jour : `{nom_file}` - `{nom_rang}` "
                f"({emoji} {rang} - **{joueur['mmr']}** MMR)"
            )
        except discord.Forbidden:
            await ctx.send("Permissions insuffisantes pour modifier les roles.")

    # ── !whois ─────────────────────────────────────────────────────────────
    @commands.command(name="whois")
    async def whois(self, ctx, member: discord.Member = None):
        """Voir le profil d'un joueur. Usage : !whois [@joueur]"""
        cible  = member or ctx.author
        joueur = await self.bot.db.get_player(cible.id)
        if not joueur:
            await ctx.send(f"**{cible.display_name}** n'est pas inscrit. Utilise `!rc`.")
            return
        lien   = await self.bot.db.get_tracker_link(cible.id)
        rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins",0))
        wins   = joueur.get("wins",0)
        losses = joueur.get("losses",0)
        total  = wins + losses
        wr     = f"{(wins/total*100):.0f}%" if total else "N/A"
        embed  = discord.Embed(title=f"Profil de {cible.display_name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=cible.display_avatar.url)
        embed.add_field(name="MMR",      value=f"**{joueur['mmr']}**", inline=True)
        embed.add_field(name="Rang DME", value=f"{emoji} **{rang}**",  inline=True)
        embed.add_field(name="Record",   value=f"{wins}W / {losses}L", inline=True)
        embed.add_field(name="Winrate",  value=wr,                     inline=True)
        if lien:
            embed.add_field(name="RLStats",
                value=f"[Voir le profil]({lien['tracker_url']})", inline=False)
        await ctx.send(embed=embed)

    @approve.error
    @deny.error
    @pending_list.error
    @update_roles.error
    @resetplayer.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Tu n'as pas la permission.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Argument manquant. Utilise `!help6mans`.")


async def setup(bot):
    await bot.add_cog(VerifyCog(bot))