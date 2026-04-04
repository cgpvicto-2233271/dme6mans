"""
cogs/queue.py  -  DME 6Mans
- !q     : rejoindre la file du channel actuel
- !dq    : quitter la file
- !queue : voir la file du channel actuel
- DQ automatique apres 30 minutes d'inactivite
"""

import asyncio
import discord
from discord.ext import commands, tasks

from utils.mmr import rang_dme, seuil_min_queue

QUEUE_SIZE = 6

# Mapping channel -> queue
CHANNEL_QUEUE_MAP = {
    "6mans-open":     "open",
    "6mans-champion": "champion",
    "6mans-gc":       "gc",
    "6mans-ssl":      "ssl",
    "queue":          "open",   # channel generique
}

QUEUE_LABELS = {
    "open":     "Open",
    "champion": "Champion+",
    "gc":       "GC+",
    "ssl":      "SSL",
}

QUEUE_EMOJI = {
    "open":     "⚪",
    "champion": "🔵",
    "gc":       "🟣",
    "ssl":      "👑",
}

# Seuils corrects
SEUILS = {
    "open":     0,
    "champion": 1200,
    "gc":       1500,
    "ssl":      1900,
}

AFK_MINUTES = 30


def _get_queue_from_channel(channel_name: str) -> str | None:
    """Retourne le nom de la file selon le channel."""
    name = channel_name.lower()
    for key, queue in CHANNEL_QUEUE_MAP.items():
        if key in name:
            return queue
    return None


class QueueCog(commands.Cog, name="Queue"):
    def __init__(self, bot):
        self.bot = bot
        self.afk_checker.start()

    def cog_unload(self):
        self.afk_checker.cancel()

    # ── Embed d'une file specifique ────────────────────────────────────────
    async def embed_queue_simple(self, queue_name: str) -> discord.Embed:
        """Embed pour une seule file."""
        joueurs = await self.bot.db.queue_list(queue_name)
        seuil   = SEUILS.get(queue_name, 0)
        label   = QUEUE_LABELS.get(queue_name, queue_name.upper())
        emoji   = QUEUE_EMOJI.get(queue_name, "")

        embed = discord.Embed(
            title=f"{emoji} File {label} — DME 6Mans",
            color=discord.Color.orange(),
        )

        lignes = []
        for joueur in joueurs[:6]:
            rang, rang_emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
            lignes.append(f"<@{joueur['discord_id']}> — **{joueur['mmr']}** {rang_emoji} {rang}")

        embed.add_field(
            name=f"Joueurs ({len(joueurs)}/{QUEUE_SIZE}) · min {seuil} MMR",
            value="\n".join(lignes) if lignes else "*Vide*",
            inline=False,
        )

        # Barre de progression
        filled  = "█" * len(joueurs)
        empty   = "░" * (QUEUE_SIZE - len(joueurs))
        embed.add_field(
            name="Progression",
            value=f"`{filled}{empty}` {len(joueurs)}/{QUEUE_SIZE}",
            inline=False,
        )

        embed.set_footer(text=f"!q pour rejoindre · !dq pour quitter · DQ auto apres {AFK_MINUTES} min")
        return embed

    # ── Embed global (toutes les files) ───────────────────────────────────
    async def embed_queue_globale(self) -> discord.Embed:
        snapshot = await self.bot.db.queue_snapshot()
        embed = discord.Embed(
            title="🎮 DME 6Mans — Rocket League",
            description="Files actives par rang. Utilise `!q` pour rejoindre ta file automatiquement.",
            color=discord.Color.orange(),
        )
        for nom in ["open", "champion", "gc", "ssl"]:
            joueurs = snapshot[nom]
            seuil   = SEUILS.get(nom, 0)
            lignes  = []
            for joueur in joueurs[:6]:
                rang, emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
                lignes.append(f"<@{joueur['discord_id']}> — **{joueur['mmr']}** {emoji} {rang}")
            valeur = "\n".join(lignes) if lignes else "*Vide*"
            label  = f"{QUEUE_EMOJI[nom]} {QUEUE_LABELS[nom]} ({len(joueurs)}/{QUEUE_SIZE})"
            if seuil > 0:
                label += f" · min {seuil} MMR"
            embed.add_field(name=label, value=valeur, inline=False)
        embed.set_footer(text=f"!q · !dq · !stats · !top · !rc <plateforme> <pseudo> · DQ auto {AFK_MINUTES}min")
        return embed

    # ── Logique rejoindre ──────────────────────────────────────────────────
    async def _rejoindre(self, member: discord.Member, queue_name: str) -> tuple[bool, str]:
        # Verifier inscription
        lien = await self.bot.db.get_tracker_link(member.id)
        if not lien:
            return False, "Tu dois d'abord t'inscrire avec `!rc <plateforme> <pseudo>`."

        joueur = await self.bot.db.get_or_create_player(member.id, member.display_name)
        mmr    = joueur["mmr"]
        seuil  = SEUILS.get(queue_name, 0)

        if mmr < seuil:
            return False, (
                f"Il faut au moins **{seuil} MMR** pour la file **{QUEUE_LABELS[queue_name]}**. "
                f"Tu es a **{mmr} MMR**."
            )

        actif = await self.bot.db.get_active_match(queue_name)
        if actif:
            return False, f"Un match est deja en cours dans la file **{QUEUE_LABELS[queue_name]}**."

        await self.bot.db.queue_leave(member.id)
        ajoute = await self.bot.db.queue_join(member.id, queue_name)
        if not ajoute:
            return False, f"Tu es deja dans la file **{QUEUE_LABELS[queue_name]}**."

        rang, emoji = rang_dme(mmr, joueur.get("wins", 0))
        return True, (
            f"**{member.display_name}** a rejoint la file **{QUEUE_LABELS[queue_name]}** "
            f"— {emoji} {rang} · **{mmr} MMR**"
        )

    # ── !q ─────────────────────────────────────────────────────────────────
    @commands.command(name="q", aliases=["join", "queue_join"])
    async def q(self, ctx: commands.Context):
        """Rejoindre la file du channel actuel. Usage : !q"""
        queue_name = _get_queue_from_channel(ctx.channel.name)
        if not queue_name:
            await ctx.send(
                "Utilise cette commande dans un channel de file :\n"
                "`#6mans-open` · `#6mans-champion` · `#6mans-gc` · `#6mans-ssl`"
            )
            return

        succes, message = await self._rejoindre(ctx.author, queue_name)
        embed = await self.embed_queue_simple(queue_name)
        await ctx.send(message, embed=embed)

        if succes and await self.bot.db.queue_count(queue_name) >= QUEUE_SIZE:
            matchmaking = self.bot.get_cog("Matchmaking")
            if matchmaking:
                await matchmaking.start_draft(ctx.guild, ctx.channel, queue_name)

    # ── !dq ────────────────────────────────────────────────────────────────
    @commands.command(name="dq", aliases=["leave", "queue_leave"])
    async def dq(self, ctx: commands.Context):
        """Quitter ta file actuelle. Usage : !dq"""
        retire = await self.bot.db.queue_leave(ctx.author.id)
        if not retire:
            await ctx.send("Tu n'etais dans aucune file.")
            return

        # Afficher la file du channel si applicable, sinon globale
        queue_name = _get_queue_from_channel(ctx.channel.name)
        if queue_name:
            embed = await self.embed_queue_simple(queue_name)
        else:
            embed = await self.embed_queue_globale()

        await ctx.send(f"**{ctx.author.display_name}** a quitte la file.", embed=embed)

    # ── !queue ─────────────────────────────────────────────────────────────
    @commands.command(name="queue", aliases=["files", "ql"])
    async def queue_view(self, ctx: commands.Context):
        """Voir la file du channel actuel. Usage : !queue"""
        queue_name = _get_queue_from_channel(ctx.channel.name)
        if queue_name:
            embed = await self.embed_queue_simple(queue_name)
        else:
            embed = await self.embed_queue_globale()
        await ctx.send(embed=embed)

    # ── DQ automatique apres 30 minutes ───────────────────────────────────
    @tasks.loop(minutes=1)
    async def afk_checker(self):
        """Verifie toutes les minutes si des joueurs sont AFK depuis 30 min."""
        try:
            expiries = await self.bot.db.get_expired_queue_players(AFK_MINUTES)
            for joueur in expiries:
                await self.bot.db.queue_leave(joueur["discord_id"])

                # Notifier le joueur
                for guild in self.bot.guilds:
                    member = guild.get_member(joueur["discord_id"])
                    if member:
                        try:
                            await member.send(
                                f"Tu as ete retire de la file **{QUEUE_LABELS.get(joueur['queue_name'], joueur['queue_name'])}** "
                                f"apres {AFK_MINUTES} minutes d'inactivite. "
                                f"Utilise `!q` pour te remettre en file."
                            )
                        except discord.Forbidden:
                            pass

                        # Notifier dans le channel de la file
                        queue_name = joueur["queue_name"]
                        channel_name = {
                            "open": "6mans-open",
                            "champion": "6mans-champion",
                            "gc": "6mans-gc",
                            "ssl": "6mans-ssl",
                        }.get(queue_name)
                        if channel_name:
                            ch = discord.utils.get(guild.text_channels, name=channel_name)
                            if ch:
                                await ch.send(
                                    f"**{member.display_name}** a ete retire de la file "
                                    f"**{QUEUE_LABELS.get(queue_name, queue_name)}** "
                                    f"(AFK {AFK_MINUTES} min)."
                                )
                        break
        except Exception as e:
            pass  # Silencieux pour eviter les logs a chaque minute

    @afk_checker.before_loop
    async def before_afk_checker(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(QueueCog(bot))