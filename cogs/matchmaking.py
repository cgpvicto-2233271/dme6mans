"""
cogs/matchmaking.py  —  DME 6Mans
MMR dynamique via Elo, draft capitaines, auto-balance optionnel.
!pick / !w / !l / !autofill / !forcematch / !cancelmatch
"""

import json
from collections import Counter

import discord
from discord.ext import commands

from utils.mmr import (
    calculer_mmr_equipes,
    find_balanced_teams,
    mmr_diff_label,
    mmr_change_arrow,
    moyenne_equipe,
    rang_dme,
)
from utils.logger import setup_logger

log = setup_logger("matchmaking")

QUEUE_SIZE = 6
QUEUE_LABELS = {
    "open":     "Open",
    "champion": "Champion+",
    "gc":       "GC+",
    "ssl":      "SSL",
}

VOCAL_FILE = {
    "open":     "Vocal Open",
    "champion": "Vocal Champion+",
    "gc":       "Vocal GC+",
    "ssl":      "Vocal SSL",
}

ROLES_RANG      = ["Rang F", "Rang E", "Rang D", "Rang C", "Rang B", "Rang A", "Rang S", "Rang SS"]
TOUS_ROLES_FILE = ["6Mans Open", "6Mans Champion+", "6Mans GC+", "6Mans SSL"]


def _get_role_file(mmr: int) -> str:
    if mmr >= 1900:
        return "6Mans SSL"
    if mmr >= 1500:
        return "6Mans GC+"
    if mmr >= 1200:
        return "6Mans Champion+"
    return "6Mans Open"


def _get_role_rang(mmr: int, wins: int) -> str:
    paliers = [
        (2100, 200, "Rang SS"),
        (1900, 150, "Rang S"),
        (1700, 100, "Rang A"),
        (1500,  75, "Rang B"),
        (1350,  50, "Rang C"),
        (1200,  30, "Rang D"),
        (1050,  15, "Rang E"),
        (900,    5, "Rang F"),
        (0,      0, "Rang F"),
    ]
    for mmr_min, wins_min, nom in paliers:
        if mmr >= mmr_min and wins >= wins_min:
            return nom
    return "Rang F"


async def _assigner_roles(member: discord.Member, mmr: int, wins: int):
    guild = member.guild
    a_ajouter, a_retirer = [], []

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
    for nom in ROLES_RANG:
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


class MatchmakingCog(commands.Cog, name="Matchmaking"):
    def __init__(self, bot):
        self.bot = bot

    # ── Channels privés ───────────────────────────────────────────────────────

    async def _create_private_channels(self, guild, match, queue_name):
        category = (
            guild.get_channel(self.bot.match_category_id)
            if self.bot.match_category_id
            else None
        )
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=False, connect=False
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True,
                connect=True, manage_channels=True,
            ),
        }
        ids = json.loads(match["team_orange"]) + json.loads(match["team_blue"])
        for uid in ids:
            m = guild.get_member(uid)
            if m:
                overwrites[m] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True,
                    connect=True, view_channel=True,
                )
        try:
            text = await guild.create_text_channel(
                f"match-{match['id']}", category=category, overwrites=overwrites
            )
            voice_orange = await guild.create_voice_channel(
                f"🟠 Match {match['id']} Orange",
                category=category, overwrites=overwrites,
            )
            voice_blue = await guild.create_voice_channel(
                f"🔵 Match {match['id']} Blue",
                category=category, overwrites=overwrites,
            )
            return text.id, voice_orange.id, voice_blue.id
        except discord.Forbidden:
            log.warning("Permissions insuffisantes pour créer les channels match %d", match["id"])
            return None, None, None

    async def _deplacer_joueurs(self, guild, match, voice_orange_id, voice_blue_id):
        if not voice_orange_id or not voice_blue_id:
            return
        ch_orange = guild.get_channel(voice_orange_id)
        ch_blue = guild.get_channel(voice_blue_id)
        if not ch_orange or not ch_blue:
            return
        for uid in json.loads(match["team_orange"]):
            m = guild.get_member(uid)
            if m and m.voice:
                try:
                    await m.move_to(ch_orange)
                except discord.Forbidden:
                    pass
        for uid in json.loads(match["team_blue"]):
            m = guild.get_member(uid)
            if m and m.voice:
                try:
                    await m.move_to(ch_blue)
                except discord.Forbidden:
                    pass

    async def _ramener_joueurs(self, guild, match, queue_name):
        vocal_attente = discord.utils.get(
            guild.voice_channels, name=VOCAL_FILE.get(queue_name, "")
        )
        if not vocal_attente:
            return
        tous_ids = json.loads(match["team_orange"]) + json.loads(match["team_blue"])
        for uid in tous_ids:
            m = guild.get_member(uid)
            if m and m.voice:
                try:
                    await m.move_to(vocal_attente)
                except discord.Forbidden:
                    pass

    # ── Démarrer le draft ─────────────────────────────────────────────────────

    async def start_draft(self, guild, channel, queue_name):
        joueurs = await self.bot.db.queue_list(queue_name)
        if len(joueurs) < QUEUE_SIZE:
            return

        joueurs_match = await self.bot.db.pop_queue_players(queue_name, QUEUE_SIZE)
        joueurs_tries = sorted(joueurs_match, key=lambda j: j["mmr"], reverse=True)
        captain_orange = joueurs_tries[0]
        captain_blue = joueurs_tries[1]
        pool = joueurs_tries[2:]

        season = await self.bot.db.get_current_season()
        match_id = await self.bot.db.create_draft_match(
            season["id"],
            queue_name,
            captain_orange["discord_id"],
            captain_blue["discord_id"],
            [j["discord_id"] for j in pool],
        )

        log.info(
            "Draft lancé: match #%d | %s | caps: %d vs %d",
            match_id,
            queue_name,
            captain_orange["discord_id"],
            captain_blue["discord_id"],
        )

        pool_lines = []
        for j in pool:
            rang, emoji = rang_dme(j["mmr"], j.get("wins", 0))
            pool_lines.append(
                f"<@{j['discord_id']}> — **{j['mmr']}** MMR {emoji} {rang}"
            )

        embed = discord.Embed(
            title=f"🏆 Draft lancée — Match #{match_id}",
            description=(
                f"File : **{QUEUE_LABELS.get(queue_name, queue_name.upper())}** · Format **BO5**\n\n"
                f"🟠 Capitaine Orange : <@{captain_orange['discord_id']}> — **{captain_orange['mmr']}** MMR\n"
                f"🔵 Capitaine Blue   : <@{captain_blue['discord_id']}> — **{captain_blue['mmr']}** MMR\n\n"
                f"Ordre de pick : **Orange → Blue → Blue → Orange**\n"
                f"Commande : `!pick {match_id} @joueur`"
            ),
            color=0xE67E22,
        )
        embed.add_field(
            name="🎯 Joueurs disponibles",
            value="\n".join(pool_lines),
            inline=False,
        )
        await channel.send(embed=embed)

    # ── Démarrer auto-balance ─────────────────────────────────────────────────

    async def start_autobalance(self, guild, channel, queue_name):
        joueurs = await self.bot.db.queue_list(queue_name)
        if len(joueurs) < QUEUE_SIZE:
            return

        joueurs_match = await self.bot.db.pop_queue_players(queue_name, QUEUE_SIZE)
        team_orange, team_blue = find_balanced_teams(joueurs_match)

        season = await self.bot.db.get_current_season()
        match_id = await self.bot.db.create_balanced_match(
            season["id"],
            queue_name,
            [j["discord_id"] for j in team_orange],
            [j["discord_id"] for j in team_blue],
        )

        log.info("Auto-balance: match #%d | %s", match_id, queue_name)

        moy_o = moyenne_equipe(team_orange)
        moy_b = moyenne_equipe(team_blue)

        def lines(players):
            out = []
            for p in players:
                rang, emoji = rang_dme(p["mmr"], p.get("wins", 0))
                out.append(f"<@{p['discord_id']}> — **{p['mmr']}** {emoji}")
            return "\n".join(out)

        embed = discord.Embed(
            title=f"⚖️ Auto-Balance — Match #{match_id}",
            description=(
                f"File : **{QUEUE_LABELS.get(queue_name, queue_name.upper())}** · **BO5**\n"
                f"{mmr_diff_label(moy_o, moy_b)}\n\n"
                f"Rapport : `!w {match_id}` victoire · `!l {match_id}` défaite"
            ),
            color=0x2ECC71,
        )
        embed.add_field(
            name=f"🟠 Orange — moy. {moy_o:.0f} MMR",
            value=lines(team_orange),
            inline=True,
        )
        embed.add_field(
            name=f"🔵 Blue — moy. {moy_b:.0f} MMR",
            value=lines(team_blue),
            inline=True,
        )

        match = await self.bot.db.get_match(match_id)
        text_id, orange_voice_id, blue_voice_id = await self._create_private_channels(
            guild, match, queue_name
        )
        if text_id:
            await self.bot.db.update_match_channels(match_id, text_id, orange_voice_id, blue_voice_id)
            embed.add_field(
                name="Channel privé", value=f"<#{text_id}>", inline=False
            )

        await channel.send(embed=embed)

        match = await self.bot.db.get_match(match_id)
        await self._deplacer_joueurs(guild, match, orange_voice_id, blue_voice_id)

        if text_id:
            ch = guild.get_channel(text_id)
            if ch:
                await ch.send(
                    f"🎮 **Match #{match_id}** — Format **BO5**\n"
                    f"`!w {match_id}` victoire · `!l {match_id}` défaite\n"
                    f"Validation : **2 capitaines** ou **4 votes sur 6**"
                )

    # ── Finaliser le draft ────────────────────────────────────────────────────

    async def _finalize_match_if_ready(self, guild, public_channel, match):
        pool = json.loads(match["draft_pool"] or "[]")
        if pool:
            return

        team_orange_ids = json.loads(match["team_orange"])
        team_blue_ids = json.loads(match["team_blue"])
        team_orange = [
            p for p in [await self.bot.db.get_player(x) for x in team_orange_ids] if p
        ]
        team_blue = [
            p for p in [await self.bot.db.get_player(x) for x in team_blue_ids] if p
        ]

        text_id, orange_voice_id, blue_voice_id = await self._create_private_channels(
            guild, match, match["queue_name"]
        )
        await self.bot.db.update_match_channels(
            match["id"], text_id, orange_voice_id, blue_voice_id
        )
        match = await self.bot.db.get_match(match["id"])
        await self._deplacer_joueurs(guild, match, orange_voice_id, blue_voice_id)

        moy_orange = moyenne_equipe(team_orange)
        moy_blue = moyenne_equipe(team_blue)

        def lines(players):
            out = []
            for p in players:
                rang, emoji = rang_dme(p["mmr"], p.get("wins", 0))
                pts = p.get("wins", 0) * 3 + p.get("losses", 0)
                out.append(
                    f"<@{p['discord_id']}> — **{p['mmr']}** MMR {emoji} {rang}"
                )
            return "\n".join(out)

        embed = discord.Embed(
            title=f"⚔️ Match #{match['id']} — Prêt à jouer !",
            description=(
                f"File : **{QUEUE_LABELS.get(match['queue_name'], match['queue_name'].upper())}** · **BO5**\n"
                f"{mmr_diff_label(moy_orange, moy_blue)}\n\n"
                f"Rapport : `!w {match['id']}` victoire · `!l {match['id']}` défaite\n"
                f"Validation : **2 capitaines alignés** ou **4 votes sur 6**"
            ),
            color=0x2ECC71,
        )
        embed.add_field(
            name=f"🟠 Orange — moy. {moy_orange:.0f} MMR",
            value=lines(team_orange),
            inline=True,
        )
        embed.add_field(
            name=f"🔵 Blue — moy. {moy_blue:.0f} MMR",
            value=lines(team_blue),
            inline=True,
        )
        if text_id:
            embed.add_field(
                name="Channel privé", value=f"<#{text_id}>", inline=False
            )
        await public_channel.send(embed=embed)

        if text_id:
            ch = guild.get_channel(text_id)
            if ch:
                await ch.send(
                    f"🎮 **Match #{match['id']}** — Format **BO5**\n"
                    f"`!w {match['id']}` victoire · `!l {match['id']}` défaite\n"
                    f"Validation : **2 capitaines** ou **4 votes sur 6**"
                )

    # ── !pick ─────────────────────────────────────────────────────────────────

    @commands.command(name="pick")
    async def pick(self, ctx: commands.Context, match_id: int, joueur: discord.Member):
        """Choisir un joueur pendant le draft. Usage : !pick <match_id> @joueur"""
        match = await self.bot.db.get_match(match_id)
        if not match:
            await ctx.send(f"❌ Match #{match_id} introuvable.")
            return
        try:
            updated = await self.bot.db.draft_pick(match_id, ctx.author.id, joueur.id)
        except ValueError as exc:
            await ctx.send(f"❌ {exc}")
            return

        team_orange = json.loads(updated["team_orange"])
        team_blue = json.loads(updated["team_blue"])
        pool = json.loads(updated["draft_pool"])

        embed = discord.Embed(
            title=f"🎯 Draft — Match #{match_id}",
            color=0xE67E22,
        )
        embed.add_field(
            name="🟠 Orange",
            value="\n".join(f"<@{x}>" for x in team_orange),
            inline=True,
        )
        embed.add_field(
            name="🔵 Blue",
            value="\n".join(f"<@{x}>" for x in team_blue),
            inline=True,
        )
        embed.add_field(
            name="Pool restant",
            value="\n".join(f"<@{x}>" for x in pool) if pool else "✅ Draft terminée",
            inline=False,
        )
        if pool and updated["next_pick"]:
            cap_id = (
                updated["captain_orange_id"]
                if updated["next_pick"] == "orange"
                else updated["captain_blue_id"]
            )
            embed.add_field(
                name="Prochain pick",
                value=f"<@{cap_id}> ({updated['next_pick'].capitalize()})",
                inline=False,
            )
        await ctx.send(embed=embed)
        await self._finalize_match_if_ready(ctx.guild, ctx.channel, updated)

    # ── Report (win/loss) ─────────────────────────────────────────────────────

    async def _reporter(self, ctx: commands.Context, match_id: int, winner: str):
        match = await self.bot.db.get_match(match_id)
        if not match or match["status"] != "active":
            await ctx.send(f"❌ Le match #{match_id} n'est pas actif.")
            return

        team_orange_ids = json.loads(match["team_orange"])
        team_blue_ids = json.loads(match["team_blue"])
        tous = team_orange_ids + team_blue_ids

        is_admin = ctx.author.guild_permissions.administrator
        if ctx.author.id not in tous and not is_admin:
            await ctx.send("❌ Tu ne fais pas partie de ce match.")
            return

        votes = await self.bot.db.register_report_vote(match_id, ctx.author.id, winner, 0, 0)
        signature = Counter(v["winner"] for v in votes.values())
        top_winner, top_count = signature.most_common(1)[0]

        cap_orange_vote = votes.get(str(match["captain_orange_id"]))
        cap_blue_vote = votes.get(str(match["captain_blue_id"]))
        captains_ok = (
            cap_orange_vote
            and cap_blue_vote
            and cap_orange_vote["winner"] == cap_blue_vote["winner"]
        )
        majorite_ok = top_count >= 4

        if not is_admin and not captains_ok and not majorite_ok:
            await ctx.message.add_reaction("✅")
            await ctx.send(
                f"🗳️ Vote enregistré : **{top_winner}** ({top_count}/6).\n"
                f"En attente : **2 capitaines alignés** ou **4 votes identiques**."
            )
            return

        # ── Validation — calcul Elo ────────────────────────────────────────
        gagnants_ids = team_orange_ids if top_winner == "orange" else team_blue_ids
        perdants_ids = team_blue_ids if top_winner == "orange" else team_orange_ids

        gagnants = [
            p for p in [await self.bot.db.get_player(x) for x in gagnants_ids] if p
        ]
        perdants = [
            p for p in [await self.bot.db.get_player(x) for x in perdants_ids] if p
        ]

        resultats = calculer_mmr_equipes(gagnants, perdants)

        for joueur in gagnants:
            nouveau_mmr, variation = resultats[joueur["discord_id"]]
            await self.bot.db.update_mmr(joueur["discord_id"], nouveau_mmr, True)
            await self.bot.db.add_mmr_history(
                joueur["discord_id"],
                joueur["mmr"],
                nouveau_mmr,
                reason="win",
                match_id=match_id,
            )

        for joueur in perdants:
            nouveau_mmr, variation = resultats[joueur["discord_id"]]
            await self.bot.db.update_mmr(joueur["discord_id"], nouveau_mmr, False)
            await self.bot.db.add_mmr_history(
                joueur["discord_id"],
                joueur["mmr"],
                nouveau_mmr,
                reason="loss",
                match_id=match_id,
            )

        await self.bot.db.finish_match(match_id, top_winner, 0, 0)
        await self._ramener_joueurs(ctx.guild, match, match["queue_name"])

        log.info(
            "Match #%d terminé: %s gagne | %d votes",
            match_id,
            top_winner,
            top_count,
        )

        # ── Embed résultat ────────────────────────────────────────────────
        embed = discord.Embed(
            title=f"🏆 Match #{match_id} validé !",
            description=f"Victoire **{'🟠 Orange' if top_winner == 'orange' else '🔵 Blue'}** · BO5",
            color=0xFF6B35 if top_winner == "orange" else 0x4169E1,
        )

        def result_line(joueur: dict, won: bool) -> str:
            new_mmr, variation = resultats[joueur["discord_id"]]
            arrow = mmr_change_arrow(variation)
            new_wins = joueur.get("wins", 0) + (1 if won else 0)
            rang, emoji = rang_dme(new_mmr, new_wins)
            return (
                f"<@{joueur['discord_id']}> {emoji} {rang} "
                f"— {arrow} MMR → **{new_mmr}**"
            )

        embed.add_field(
            name="🏆 Gagnants",
            value="\n".join(result_line(j, True) for j in gagnants),
            inline=False,
        )
        embed.add_field(
            name="❌ Perdants",
            value="\n".join(result_line(j, False) for j in perdants),
            inline=False,
        )
        await ctx.send(embed=embed)

        # Mise à jour des rôles + suppression channels
        for joueur in gagnants:
            updated_p = await self.bot.db.get_player(joueur["discord_id"])
            member = ctx.guild.get_member(joueur["discord_id"])
            if member and updated_p:
                try:
                    await _assigner_roles(member, updated_p["mmr"], updated_p.get("wins", 0))
                except discord.Forbidden:
                    pass

        for joueur in perdants:
            updated_p = await self.bot.db.get_player(joueur["discord_id"])
            member = ctx.guild.get_member(joueur["discord_id"])
            if member and updated_p:
                try:
                    await _assigner_roles(member, updated_p["mmr"], updated_p.get("wins", 0))
                except discord.Forbidden:
                    pass

        for channel_id in [
            match.get("channel_text_id"),
            match.get("channel_voice_orange_id"),
            match.get("channel_voice_blue_id"),
        ]:
            if channel_id:
                ch = ctx.guild.get_channel(channel_id)
                if ch:
                    try:
                        await ch.delete(reason=f"Fin match #{match_id}")
                    except discord.Forbidden:
                        pass

    @commands.command(name="w", aliases=["win"])
    async def win(self, ctx: commands.Context, match_id: int):
        """Reporter une victoire. Usage : !w <match_id>"""
        match = await self.bot.db.get_match(match_id)
        if not match:
            await ctx.send(f"❌ Match #{match_id} introuvable.")
            return
        team_orange_ids = json.loads(match["team_orange"])
        winner = "orange" if ctx.author.id in team_orange_ids else "blue"
        await self._reporter(ctx, match_id, winner)

    @commands.command(name="l", aliases=["loss", "lose"])
    async def loss(self, ctx: commands.Context, match_id: int):
        """Reporter une défaite. Usage : !l <match_id>"""
        match = await self.bot.db.get_match(match_id)
        if not match:
            await ctx.send(f"❌ Match #{match_id} introuvable.")
            return
        team_orange_ids = json.loads(match["team_orange"])
        winner = "blue" if ctx.author.id in team_orange_ids else "orange"
        await self._reporter(ctx, match_id, winner)

    # ── !autofill ─────────────────────────────────────────────────────────────

    @commands.command(name="autofill", aliases=["autobalance", "ab"])
    @commands.has_permissions(administrator=True)
    async def autofill(self, ctx: commands.Context, queue: str = "open"):
        """[Admin] Lancer un match auto-équilibré (sans draft). Usage : !autofill <queue>"""
        queue = queue.lower()
        if queue not in ("open", "champion", "gc", "ssl"):
            await ctx.send("❌ File invalide.")
            return
        total = await self.bot.db.queue_count(queue)
        if total < QUEUE_SIZE:
            await ctx.send(f"❌ Pas assez de joueurs : **{total}/{QUEUE_SIZE}**.")
            return
        await ctx.send(f"⚖️ Auto-balance lancé pour **{QUEUE_LABELS[queue]}**...")
        await self.start_autobalance(ctx.guild, ctx.channel, queue)

    # ── !forcematch ───────────────────────────────────────────────────────────

    @commands.command(name="forcematch")
    @commands.has_permissions(administrator=True)
    async def forcematch(self, ctx: commands.Context, queue: str = "open"):
        """[Admin] Forcer un draft. Usage : !forcematch <queue>"""
        queue = queue.lower()
        if queue not in ("open", "champion", "gc", "ssl"):
            await ctx.send("❌ File invalide.")
            return
        total = await self.bot.db.queue_count(queue)
        if total < QUEUE_SIZE:
            await ctx.send(f"❌ Pas assez de joueurs : **{total}/{QUEUE_SIZE}**.")
            return
        await ctx.send(f"🏆 Draft forcé pour **{QUEUE_LABELS[queue]}**.")
        await self.start_draft(ctx.guild, ctx.channel, queue)

    # ── !cancelmatch ──────────────────────────────────────────────────────────

    @commands.command(name="cancelmatch")
    @commands.has_permissions(administrator=True)
    async def cancelmatch(self, ctx: commands.Context, match_id: int):
        """[Admin] Annuler un match. Usage : !cancelmatch <match_id>"""
        match = await self.bot.db.get_match(match_id)
        if not match:
            await ctx.send(f"❌ Match #{match_id} introuvable.")
            return
        if match["status"] in ("finished", "cancelled"):
            await ctx.send(f"❌ Match #{match_id} déjà terminé/annulé.")
            return
        await self.bot.db.cancel_match(match_id)

        # Supprimer les channels si existants
        for ch_id in [
            match.get("channel_text_id"),
            match.get("channel_voice_orange_id"),
            match.get("channel_voice_blue_id"),
        ]:
            if ch_id:
                ch = ctx.guild.get_channel(ch_id)
                if ch:
                    try:
                        await ch.delete(reason=f"Annulation match #{match_id}")
                    except discord.Forbidden:
                        pass

        await ctx.send(f"✅ Match #{match_id} annulé.")
        log.info("Match #%d annulé par %s", match_id, ctx.author)

    @autofill.error
    @forcematch.error
    @cancelmatch.error
    async def admin_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Tu n'as pas la permission.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Argument manquant.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Argument invalide.")


async def setup(bot: commands.Bot):
    await bot.add_cog(MatchmakingCog(bot))
