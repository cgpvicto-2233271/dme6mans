"""
run.py - Lance le bot Discord + l'API FastAPI ensemble
Railway utilise ce fichier comme point d'entree via le Procfile.
"""
import asyncio
import os
import threading

import uvicorn

from api import app


def run_bot():
    """Lance le bot Discord dans un thread separé."""
    import asyncio
    from main import main
    asyncio.run(main())


if __name__ == "__main__":
    # Lancer le bot dans un thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Lancer l'API en foreground (Railway detecte le port ici)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")