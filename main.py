"""
main.py  —  DME 6Mans Bot
Bot préfixe uniquement, DME6MANS.
Préfixe par défaut : !
"""

import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.database import Database
from utils.logger import setup_logger

load_dotenv()

log = setup_logger("bot")

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
TOKEN             = os.getenv("DISCORD_TOKEN")
PREFIX            = os.getenv("BOT_PREFIX", "!")
GUILD_ID          = int(os.getenv("DME_GUILD_ID", "0") or 0)
DATABASE_PATH     = os.path.join(BASE_DIR, "data", "database.db")
MATCH_CATEGORY_ID = int(os.getenv("MATCH_CATEGORY_ID", "0") or 0)

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN manquant dans le fichier .env")

intents                 = discord.Intents.default()
intents.message_content = True
intents.members         = True
intents.guilds          = True
intents.voice_states    = True

bot                   = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
bot.db                = Database(DATABASE_PATH)
bot.guild_id          = GUILD_ID
bot.match_category_id = MATCH_CATEGORY_ID


@bot.event
async def on_ready():
    await bot.db.init()
    await bot.db.init_verify_table()  # no-op, maintenu pour compat
    log.info("Bot connecté : %s (%s)", bot.user, bot.user.id)
    log.info("Guild ID : %d | Category ID : %d", GUILD_ID, MATCH_CATEGORY_ID)


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Argument manquant. Utilise `!help6mans` pour voir les commandes.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ Argument invalide. Utilise `!help6mans` pour voir les commandes.")
        return
    if isinstance(error, commands.CommandOnCooldown):
        return  # géré par chaque cog
    log.exception("Erreur commande [%s] : %s", ctx.command, error)


async def load_extensions():
    cogs = [
        "cogs.queue",
        "cogs.matchmaking",
        "cogs.stats",
        "cogs.admin",
        "cogs.verify",
        "cogs.leaderboard_live",
    ]
    for ext in cogs:
        await bot.load_extension(ext)
        log.info("Cog chargé : %s", ext)


async def main():
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Arrêt du bot.")
