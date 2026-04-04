"""
main.py  —  DME 6Mans Bot
Bot préfixe uniquement, DME6MANS.
Préfixe par défaut : !
"""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.database import Database

load_dotenv()

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
TOKEN             = os.getenv("DISCORD_TOKEN")
PREFIX            = os.getenv("BOT_PREFIX", "!")
GUILD_ID = int(os.getenv("DME_GUILD_ID", "0") or 0)
DATABASE_PATH     = os.path.join(BASE_DIR, "data", "database.db")
MATCH_CATEGORY_ID = int(os.getenv("MATCH_CATEGORY_ID", "0") or 0)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dme_6mans")

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
    await bot.db.init_verify_table()
    logger.info("Bot connecté en tant que %s (%s)", bot.user, bot.user.id)
    # Pas de sync slash commands — tout est en préfixe


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return  # Ignorer les commandes inconnues silencieusement
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Argument manquant. Utilise `!help6mans` pour voir les commandes.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Argument invalide. Utilise `!help6mans` pour voir les commandes.")
        return
    logger.exception("Erreur commande %s : %s", ctx.command, error)


async def load_extensions():
    for ext in ["cogs.queue", "cogs.matchmaking", "cogs.stats", "cogs.admin", "cogs.verify", "cogs.leaderboard_live"]:
        await bot.load_extension(ext)
        logger.info("Cog chargé : %s", ext)

async def main():
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arrêt du bot.")