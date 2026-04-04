import os, threading, asyncio, uvicorn
from api import app

def run_bot():
    from main import main
    asyncio.run(main())

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)