"""
cogs/matchmaking.py  -  DME 6Mans
Systeme de points : +3 victoire, +1 defaite. MMR fixe (peak inscription).
"""

import json
from collections import Counter

import discord
from discord.ext import commands

from utils.mmr import moyenne_equipe, rang_dme

QUEUE_SIZE   = 6
QUEUE_LABELS = {"open": "Open", "champion": "Champion+", "gc": "GC+", "ssl": "SSL"}

VOCAL_FILE = {
    "open":     "Vocal Open",
    "champion": "Vocal Champion+",
    "gc":       "Vocal GC+",
    "ssl":      "Vocal SSL",
}

ROLES_RANG      = ["Rang F","Rang E","Rang D","Rang C","Rang B","Rang A","Rang S","Rang SS"]
TOUS_ROLES_FILE = ["6Mans Open","6Mans Champion+","6Mans GC+","6Mans SSL"]


def _get_role_file(mmr):
    if mmr >= 1900: return "6Mans SSL"
    if mmr >= 1500: return "6Mans GC+"
    if mmr >= 1200: return "6Mans Champion+"
    return "6Mans Open"

def _get_role_rang(mmr, wins):
    paliers = [
        (2100,200,"Rang SS"),(1900,150,"Rang S"),(1700,100,"Rang A"),
        (1500,75,"Rang B"),(1350,50,"Rang C"),(1200,30,"Rang D"),
        (1050,15,"Rang E"),(900,5,"Rang F"),(0,0,"Rang F"),
    ]
    for mmr_min, wins_min, nom in paliers:
        if mmr >= mmr_min and wins >= wins_min: return nom
    return "Rang F"

async def _assigner_roles(member, mmr, wins):
    guild = member.guild
    a_ajouter, a_retirer = [], []
    nom_file = _get_role_file(mmr)
    for nom in TOUS_ROLES_FILE:
        role = discord.utils.get(guild.roles, name=nom)
        if not role: continue
        if nom == nom_file:
            if role not in member.roles: a_ajouter.append(role)
        else:
            if role in member.roles: a_retirer.append(role)
    nom_rang = _get_role_rang(mmr, wins)
    for nom in ROLES_RANG:
        role = discord.utils.get(guild.roles, name=nom)
        if not role: continue
        if nom == nom_rang:
            if role not in member.roles: a_ajouter.append(role)
        else:
            if role in member.roles: a_retirer.append(role)
    if a_retirer: await member.remove_roles(*a_retirer, reason="DME 6Mans - MAJ rang")
    if a_ajouter: await member.add_roles(*a_ajouter, reason="DME 6Mans - MAJ rang")


class MatchmakingCog(commands.Cog, name="Matchmaking"):
    def __init__(self, bot):
        self.bot = bot

    async def _create_private_channels(self, guild, match, queue_name):
        category = guild.get_channel(self.bot.match_category_id) if self.bot.match_category_id else None
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, connect=True, manage_channels=True),
        }
        ids = json.loads(match["team_orange"]) + json.loads(match["team_blue"])
        for user_id in ids:
            member = guild.get_member(user_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, connect=True, view_channel=True
                )
        try:
            text         = await guild.create_text_channel(f"match-{match['id']}", category=category, overwrites=overwrites)
            voice_orange = await guild.create_voice_channel(f"match-{match['id']}-orange", category=category, overwrites=overwrites)
            voice_blue   = await guild.create_voice_channel(f"match-{match['id']}-blue",   category=category, overwrites=overwrites)
            return text.id, voice_orange.id, voice_blue.id
        except discord.Forbidden:
            return None, None, None

    async def _deplacer_joueurs(self, guild, match, voice_orange_id, voice_blue_id, queue_name):
        if not voice_orange_id or not voice_blue_id:
            return
        ch_orange = guild.get_channel(voice_orange_id)
        ch_blue   = guild.get_channel(voice_blue_id)
        if not ch_orange or not ch_blue:
            return
        for user_id in json.loads(match["team_orange"]):
            member = guild.get_member(user_id)
            if member and member.voice:
                try: await member.move_to(ch_orange)
                except discord.Forbidden: pass
        for user_id in json.loads(match["team_blue"]):
            member = guild.get_member(user_id)
            if member and member.voice:
                try: await member.move_to(ch_blue)
                except discord.Forbidden: pass

    async def _ramener_joueurs(self, guild, match, queue_name):
        vocal_attente_nom = VOCAL_FILE.get(queue_name)
        if not vocal_attente_nom:
            return
        vocal_attente = discord.utils.get(guild.voice_channels, name=vocal_attente_nom)
        if not vocal_attente:
            return
        tous_ids = json.loads(match["team_orange"]) + json.loads(match["team_blue"])
        for user_id in tous_ids:
            member = guild.get_member(user_id)
            if member and member.voice:
                try: await member.move_to(vocal_attente)
                except discord.Forbidden: pass

    async def start_draft(self, guild, channel, queue_name):
        joueurs = await self.bot.db.queue_list(queue_name)
        if len(joueurs) < QUEUE_SIZE:
            return

        joueurs_match  = await self.bot.db.pop_queue_players(queue_name, QUEUE_SIZE)
        joueurs_tries  = sorted(joueurs_match, key=lambda j: j["mmr"], reverse=True)
        captain_orange = joueurs_tries[0]
        captain_blue   = joueurs_tries[1]
        pool           = joueurs_tries[2:]

        season   = await self.bot.db.get_current_season()
        match_id = await self.bot.db.create_draft_match(
            season["id"], queue_name,
            captain_orange["discord_id"], captain_blue["discord_id"],
            [j["discord_id"] for j in pool],
        )

        pool_lines = []
        for j in pool:
            rang, emoji = rang_dme(j["mmr"], j.get("wins", 0))
            pool_lines.append(f"<@{j['discord_id']}> - **{j['mmr']}** {emoji} {rang}")

        embed = discord.Embed(
            title=f"Draft lancee - Match #{match_id}",
            description=(
                f"File : **{QUEUE_LABELS.get(queue_name, queue_name.upper())}** · Format **BO5**\n"
                f"🟠 Capitaine Orange : <@{captain_orange['discord_id']}>\n"
                f"🔵 Capitaine Blue   : <@{captain_blue['discord_id']}>\n\n"
                f"Ordre de pick : **Orange -> Blue -> Blue -> Orange**\n"
                f"Commande : `!pick {match_id} @joueur`"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Joueurs a drafter", value="\n".join(pool_lines), inline=False)
        await channel.send(embed=embed)

    async def _finalize_match_if_ready(self, guild, public_channel, match):
        pool = json.loads(match["draft_pool"] or "[]")
        if pool:
            return

        team_orange_ids = json.loads(match["team_orange"])
        team_blue_ids   = json.loads(match["team_blue"])
        team_orange = [p for p in [await self.bot.db.get_player(x) for x in team_orange_ids] if p]
        team_blue   = [p for p in [await self.bot.db.get_player(x) for x in team_blue_ids]   if p]

        text_id, orange_voice_id, blue_voice_id = await self._create_private_channels(
            guild, match, match["queue_name"]
        )
        await self.bot.db.update_match_channels(match["id"], text_id, orange_voice_id, blue_voice_id)
        match = await self.bot.db.get_match(match["id"])

        await self._deplacer_joueurs(guild, match, orange_voice_id, blue_voice_id, match["queue_name"])

        moy_orange = moyenne_equipe(team_orange)
        moy_blue   = moyenne_equipe(team_blue)
        diff       = abs(moy_orange - moy_blue)

        def lines(players):
            out = []
            for p in players:
                rang, emoji = rang_dme(p["mmr"], p.get("wins", 0))
                pts = p.get("wins", 0) * 3 + p.get("losses", 0)
                out.append(f"<@{p['discord_id']}> - **{p['mmr']}** MMR · {pts} pts {emoji} {rang}")
            return "\n".join(out)

        embed = discord.Embed(
            title=f"Match #{match['id']} - Pret a jouer !",
            description=(
                f"File : **{QUEUE_LABELS.get(match['queue_name'], match['queue_name'].upper())}** · **BO5**\n\n"
                f"Rapporte avec `!w {match['id']}` (victoire) ou `!l {match['id']}` (defaite)\n"
                f"Validation : **2 capitaines alignes** ou **4 votes identiques sur 6**"
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name=f"🟠 Orange - moy. {moy_orange:.0f}", value=lines(team_orange), inline=True)
        embed.add_field(name=f"🔵 Blue - moy. {moy_blue:.0f}",     value=lines(team_blue),   inline=True)
        embed.add_field(name="Ecart MMR", value=f"`{diff:.0f}`", inline=False)
        if text_id:
            embed.add_field(name="Channel prive", value=f"<#{text_id}>", inline=False)
        await public_channel.send(embed=embed)

        if text_id:
            ch = guild.get_channel(text_id)
            if ch:
                await ch.send(
                    f"Format **BO5** — premier a 3 victoires.\n"
                    f"`!w {match['id']}` victoire · `!l {match['id']}` defaite\n"
                    f"**+3 pts** victoire · **+1 pt** defaite"
                )

    @commands.command(name="pick")
    async def pick(self, ctx, match_id: int, joueur: discord.Member):
        match = await self.bot.db.get_match(match_id)
        if not match:
            await ctx.send("Match introuvable.")
            return
        try:
            updated = await self.bot.db.draft_pick(match_id, ctx.author.id, joueur.id)
        except ValueError as exc:
            await ctx.send(f"{exc}")
            return

        team_orange = json.loads(updated["team_orange"])
        team_blue   = json.loads(updated["team_blue"])
        pool        = json.loads(updated["draft_pool"])

        embed = discord.Embed(title=f"Draft - Match #{match_id}", color=discord.Color.orange())
        embed.add_field(name="🟠 Orange", value="\n".join(f"<@{x}>" for x in team_orange), inline=True)
        embed.add_field(name="🔵 Blue",   value="\n".join(f"<@{x}>" for x in team_blue),   inline=True)
        embed.add_field(
            name="Pool restant",
            value="\n".join(f"<@{x}>" for x in pool) if pool else "Draft terminee",
            inline=False,
        )
        await ctx.send("Pick valide.", embed=embed)
        await self._finalize_match_if_ready(ctx.guild, ctx.channel, updated)

    async def _reporter(self, ctx, match_id, winner):
        match = await self.bot.db.get_match(match_id)
        if not match or match["status"] != "active":
            await ctx.send(f"Le match #{match_id} n'est pas actif.")
            return

        team_orange_ids = json.loads(match["team_orange"])
        team_blue_ids   = json.loads(match["team_blue"])
        tous = team_orange_ids + team_blue_ids

        if ctx.author.id not in tous and not ctx.author.guild_permissions.administrator:
            await ctx.send("Tu ne fais pas partie de ce match.")
            return

        votes = await self.bot.db.register_report_vote(match_id, ctx.author.id, winner, 0, 0)
        signature  = Counter(v["winner"] for v in votes.values())
        top_winner, top_count = signature.most_common(1)[0]

        cap_orange_vote = votes.get(str(match["captain_orange_id"]))
        cap_blue_vote   = votes.get(str(match["captain_blue_id"]))
        captains_ok = (
            cap_orange_vote and cap_blue_vote
            and cap_orange_vote["winner"] == cap_blue_vote["winner"]
        )
        majorite_ok = top_count >= 4

        if not ctx.author.guild_permissions.administrator and not captains_ok and not majorite_ok:
            await ctx.message.add_reaction("✅")
            await ctx.send(
                f"Vote enregistre pour **{top_winner}** ({top_count}/6).\n"
                f"En attente : **2 capitaines alignes** ou **4 votes identiques**."
            )
            return

        # Validation — MMR fixe, on ajoute seulement wins/losses
        gagnants_ids = team_orange_ids if top_winner == "orange" else team_blue_ids
        perdants_ids = team_blue_ids   if top_winner == "orange" else team_orange_ids

        gagnants = [p for p in [await self.bot.db.get_player(x) for x in gagnants_ids] if p]
        perdants = [p for p in [await self.bot.db.get_player(x) for x in perdants_ids] if p]

        # MMR fixe - juste incrementer wins/losses
        for j in gagnants:
            await self.bot.db.add_win(j["discord_id"])
        for j in perdants:
            await self.bot.db.add_loss(j["discord_id"])

        await self.bot.db.finish_match(match_id, top_winner, 0, 0)
        await self._ramener_joueurs(ctx.guild, match, match["queue_name"])

        embed = discord.Embed(
            title=f"Match #{match_id} valide !",
            description=f"Victoire **{top_winner.capitalize()}** · BO5",
            color=discord.Color.green(),
        )

        gagnants_lignes, perdants_lignes = [], []

        for j in gagnants:
            new_wins = j.get("wins", 0) + 1
            new_losses = j.get("losses", 0)
            pts = new_wins * 3 + new_losses
            rang, emoji = rang_dme(j["mmr"], new_wins)
            gagnants_lignes.append(
                f"<@{j['discord_id']}> `+3 pts` → **{pts} pts** · {emoji} {rang}"
            )
            member = ctx.guild.get_member(j["discord_id"])
            if member:
                try: await _assigner_roles(member, j["mmr"], new_wins)
                except discord.Forbidden: pass

        for j in perdants:
            wins   = j.get("wins", 0)
            losses = j.get("losses", 0) + 1
            pts    = wins * 3 + losses
            rang, emoji = rang_dme(j["mmr"], wins)
            perdants_lignes.append(
                f"<@{j['discord_id']}> `+1 pt` → **{pts} pts** · {emoji} {rang}"
            )
            member = ctx.guild.get_member(j["discord_id"])
            if member:
                try: await _assigner_roles(member, j["mmr"], wins)
                except discord.Forbidden: pass

        embed.add_field(name="🏆 Gagnants (+3 pts)", value="\n".join(gagnants_lignes), inline=False)
        embed.add_field(name="❌ Perdants (+1 pt)",   value="\n".join(perdants_lignes), inline=False)
        await ctx.send(embed=embed)

        for channel_id in [match.get("channel_text_id"), match.get("channel_voice_orange_id"), match.get("channel_voice_blue_id")]:
            if channel_id:
                ch = ctx.guild.get_channel(channel_id)
                if ch:
                    try: await ch.delete(reason=f"Fin du match {match_id}")
                    except discord.Forbidden: pass

    @commands.command(name="w", aliases=["win"])
    async def win(self, ctx, match_id: int):
        match = await self.bot.db.get_match(match_id)
        if not match:
            await ctx.send(f"Match #{match_id} introuvable.")
            return
        team_orange_ids = json.loads(match["team_orange"])
        winner = "orange" if ctx.author.id in team_orange_ids else "blue"
        await self._reporter(ctx, match_id, winner)

    @commands.command(name="l", aliases=["loss", "lose"])
    async def loss(self, ctx, match_id: int):
        match = await self.bot.db.get_match(match_id)
        if not match:
            await ctx.send(f"Match #{match_id} introuvable.")
            return
        team_orange_ids = json.loads(match["team_orange"])
        winner = "blue" if ctx.author.id in team_orange_ids else "orange"
        await self._reporter(ctx, match_id, winner)

    @commands.command(name="forcematch")
    @commands.has_permissions(administrator=True)
    async def forcematch(self, ctx, queue="open"):
        queue = queue.lower()
        if queue not in ("open", "champion", "gc", "ssl"):
            await ctx.send("File invalide.")
            return
        total = await self.bot.db.queue_count(queue)
        if total < QUEUE_SIZE:
            await ctx.send(f"Pas assez de joueurs : **{total}/{QUEUE_SIZE}**.")
            return
        await ctx.send(f"Draft forcee pour **{QUEUE_LABELS[queue]}**.")
        await self.start_draft(ctx.guild, ctx.channel, queue)

    @commands.command(name="cancelmatch")
    @commands.has_permissions(administrator=True)
    async def cancelmatch(self, ctx, match_id: int):
        await self.bot.db.cancel_match(match_id)
        await ctx.send(f"Match #{match_id} annule.")


async def setup(bot):
    await bot.add_cog(MatchmakingCog(bot))