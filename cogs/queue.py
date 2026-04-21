"""
cogs/queue.py  —  DME 6Mans
- !q     : rejoindre la file du channel actuel
- !dq    : quitter la file
- !queue : voir la file du channel actuel
- DQ automatique après 30 minutes
- Anti double-queue (joueur en match actif)
- Vérification ban queue
"""

import asyncio
import discord
from discord.ext import commands, tasks

from utils.mmr import rang_dme, seuil_min_queue
from utils.logger import setup_logger

log = setup_logger("queue")

QUEUE_SIZE = 6

CHANNEL_QUEUE_MAP = {
    "6mans-open":     "open",
    "6mans-champion": "champion",
    "6mans-gc":       "gc",
    "6mans-ssl":      "ssl",
    "queue":          "open",
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

QUEUE_COLORS = {
    "open":     0x99AAB5,
    "champion": 0x3498DB,
    "gc":       0x9B59B6,
    "ssl":      0xF1C40F,
}

SEUILS = {
    "open":     0,
    "champion": 1200,
    "gc":       1500,
    "ssl":      1900,
}

AFK_MINUTES = 30


def _get_queue_from_channel(channel_name: str) -> str | None:
    name = channel_name.lower()
    for key, queue in CHANNEL_QUEUE_MAP.items():
        if key in name:
            return queue
    return None


def _progress_bar(current: int, total: int, width: int = 10) -> str:
    filled = round(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    return f"`{bar}` {current}/{total}"


class QueueCog(commands.Cog, name="Queue"):
    def __init__(self, bot):
        self.bot = bot
        self.afk_checker.start()

    def cog_unload(self):
        self.afk_checker.cancel()

    # ── Embed file simple ──────────────────────────────────────────────────────

    async def embed_queue_simple(self, queue_name: str) -> discord.Embed:
        joueurs = await self.bot.db.queue_list(queue_name)
        seuil = SEUILS.get(queue_name, 0)
        label = QUEUE_LABELS.get(queue_name, queue_name.upper())
        emoji = QUEUE_EMOJI.get(queue_name, "")
        color = QUEUE_COLORS.get(queue_name, 0xE67E22)

        embed = discord.Embed(
            title=f"{emoji} File {label} — DME 6Mans",
            color=color,
        )

        lignes = []
        for i, joueur in enumerate(joueurs[:QUEUE_SIZE], start=1):
            rang, rang_emoji = rang_dme(joueur["mmr"], joueur.get("wins", 0))
            lignes.append(
                f"`{i}.` <@{joueur['discord_id']}> — **{joueur['mmr']}** MMR {rang_emoji} {rang}"
            )

        embed.add_field(
            name=f"Joueurs · min {seuil} MMR",
            value="\n".join(lignes) if lignes else "*File vide*",
            inline=False,
        )
        embed.add_field(
            name="Progression",
            value=_progress_bar(len(joueurs), QUEUE_SIZE),
            inline=False,
        )
        embed.set_footer(
            text=f"!q rejoindre · !dq quitter · DQ auto {AFK_MINUTES} min"
        )
        return embed

    # ── Embed global (toutes les files) ──────────────────────────────────────

    async def embed_queue_globale(self) -> discord.Embed:
        snapshot = await self.bot.db.queue_snapshot()
        embed = discord.Embed(
            title="🎮 DME 6Mans — Rocket League",
            description="Files actives. Utilise `!q` dans ton channel de file.",
            color=0xE67E22,
        )
        for nom in ["open", "champion", "gc", "ssl"]:
            joueurs = snapshot[nom]
            seuil = SEUILS[nom]
            lignes = []
            for joueur in joueurs[:QUEUE_SIZE]:
                rang, em = rang_dme(joueur["mmr"], joueur.get("wins", 0))
                lignes.append(
                    f"<@{joueur['discord_id']}> — **{joueur['mmr']}** {em}"
                )
            bar = _progress_bar(len(joueurs), QUEUE_SIZE)
            label = f"{QUEUE_EMOJI[nom]} {QUEUE_LABELS[nom]}"
            if seuil > 0:
                label += f" · {seuil}+ MMR"
            valeur = (
                ("\n".join(lignes) + f"\n{bar}")
                if lignes
                else f"*Vide* · {bar}"
            )
            embed.add_field(name=label, value=valeur, inline=False)
        embed.set_footer(
            text=f"!q · !dq · !stats · !top · !rc <plateforme> <pseudo> · DQ {AFK_MINUTES}min"
        )
        return embed

    # ── Logique rejoindre ──────────────────────────────────────────────────────

    async def _rejoindre(
        self, member: discord.Member, queue_name: str
    ) -> tuple[bool, str]:
        # 1. Inscription obligatoire
        lien = await self.bot.db.get_tracker_link(member.id)
        if not lien:
            return False, (
                "Tu dois d'abord t'inscrire avec `!rc <plateforme> <pseudo>`.\n"
                "Exemple : `!rc epic MonPseudo`"
            )

        # 2. Vérification ban queue
        ban = await self.bot.db.is_banned(member.id)
        if ban:
            raison = ban.get("reason", "Aucune raison")
            until = ban.get("banned_until")
            msg = f"Tu es banni de la queue. Raison : **{raison}**"
            if until:
                msg += f"\nExpire : `{until[:16]}`"
            return False, msg

        # 3. Joueur déjà dans un match actif
        match_actif = await self.bot.db.is_player_in_active_match(member.id)
        if match_actif:
            return False, (
                f"Tu es déjà dans le match **#{match_actif}** en cours.\n"
                f"Termine ce match avant de rejoindre une file."
            )

        # 4. MMR suffisant
        joueur = await self.bot.db.get_or_create_player(member.id, member.display_name)
        mmr = joueur["mmr"]
        seuil = SEUILS.get(queue_name, 0)
        if mmr < seuil:
            return False, (
                f"Il faut au moins **{seuil} MMR** pour la file **{QUEUE_LABELS[queue_name]}**.\n"
                f"Ton MMR : **{mmr}**"
            )

        # 5. Quitter l'ancienne file si différente
        await self.bot.db.queue_leave(member.id)

        # 6. Rejoindre
        ajoute = await self.bot.db.queue_join(member.id, queue_name)
        if not ajoute:
            return False, f"Tu es déjà dans la file **{QUEUE_LABELS[queue_name]}**."

        rang, emoji = rang_dme(mmr, joueur.get("wins", 0))
        log.info(
            "Queue join: %s (%d) → %s [%d MMR]",
            member.display_name,
            member.id,
            queue_name,
            mmr,
        )
        return True, (
            f"**{member.display_name}** a rejoint la file **{QUEUE_LABELS[queue_name]}** "
            f"— {emoji} {rang} · **{mmr} MMR**"
        )

    # ── !q ────────────────────────────────────────────────────────────────────

    @commands.command(name="q", aliases=["join", "queue_join"])
    async def q(self, ctx: commands.Context):
        """Rejoindre la file du channel. Usage : !q"""
        queue_name = _get_queue_from_channel(ctx.channel.name)
        if not queue_name:
            await ctx.send(
                "❌ Utilise cette commande dans un channel de file :\n"
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

    # ── !dq ───────────────────────────────────────────────────────────────────

    @commands.command(name="dq", aliases=["leave", "queue_leave"])
    async def dq(self, ctx: commands.Context):
        """Quitter ta file. Usage : !dq"""
        retire = await self.bot.db.queue_leave(ctx.author.id)
        if not retire:
            await ctx.send("❌ Tu n'étais dans aucune file.")
            return

        queue_name = _get_queue_from_channel(ctx.channel.name)
        if queue_name:
            embed = await self.embed_queue_simple(queue_name)
        else:
            embed = await self.embed_queue_globale()

        await ctx.send(
            f"**{ctx.author.display_name}** a quitté la file.", embed=embed
        )
        log.info("Queue leave: %s (%d)", ctx.author.display_name, ctx.author.id)

    # ── !queue ────────────────────────────────────────────────────────────────

    @commands.command(name="queue", aliases=["files", "ql"])
    async def queue_view(self, ctx: commands.Context):
        """Voir la file. Usage : !queue"""
        queue_name = _get_queue_from_channel(ctx.channel.name)
        if queue_name:
            embed = await self.embed_queue_simple(queue_name)
        else:
            embed = await self.embed_queue_globale()
        await ctx.send(embed=embed)

    # ── DQ automatique AFK ────────────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def afk_checker(self):
        try:
            expiries = await self.bot.db.get_expired_queue_players(AFK_MINUTES)
            for joueur in expiries:
                await self.bot.db.queue_leave(joueur["discord_id"])
                log.info(
                    "AFK DQ: %d de la file %s",
                    joueur["discord_id"],
                    joueur["queue_name"],
                )

                for guild in self.bot.guilds:
                    member = guild.get_member(joueur["discord_id"])
                    if not member:
                        continue

                    try:
                        await member.send(
                            f"⏱️ Tu as été retiré de la file "
                            f"**{QUEUE_LABELS.get(joueur['queue_name'], joueur['queue_name'])}** "
                            f"après {AFK_MINUTES} minutes d'inactivité.\n"
                            f"Utilise `!q` pour te remettre en file."
                        )
                    except discord.Forbidden:
                        pass

                    ch_name = {
                        "open":     "6mans-open",
                        "champion": "6mans-champion",
                        "gc":       "6mans-gc",
                        "ssl":      "6mans-ssl",
                    }.get(joueur["queue_name"])
                    if ch_name:
                        ch = discord.utils.get(guild.text_channels, name=ch_name)
                        if ch:
                            await ch.send(
                                f"⏱️ **{member.display_name}** retiré de la file "
                                f"**{QUEUE_LABELS.get(joueur['queue_name'], joueur['queue_name'])}** "
                                f"(AFK {AFK_MINUTES} min)."
                            )
                    break
        except Exception as exc:
            log.error("AFK checker error: %s", exc)

    @afk_checker.before_loop
    async def before_afk_checker(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(QueueCog(bot))
