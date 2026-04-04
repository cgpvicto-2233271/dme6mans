import asyncio
import os
import discord
from dotenv import load_dotenv

load_dotenv()
TOKEN    = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)

async def main():
    client = discord.Client(intents=discord.Intents.default())
    tree   = discord.app_commands.CommandTree(client)
    await client.login(TOKEN)

    guild = discord.Object(id=GUILD_ID)

    cmds_guild  = await tree.fetch_commands(guild=guild)
    cmds_global = await tree.fetch_commands()
    print("Commandes serveur  :", [c.name for c in cmds_guild])
    print("Commandes globales :", [c.name for c in cmds_global])

    tree.clear_commands(guild=guild)
    await tree.sync(guild=guild)
    tree.clear_commands(guild=None)
    await tree.sync()
    print("Suppression OK - relance main.py")

    await client.close()

asyncio.run(main())