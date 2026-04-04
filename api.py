"""
api.py  -  DME 6Mans REST API
Lance en parallele du bot Discord pour exposer les donnees au site web.
Port : variable PORT de Railway ou 8000 en local

Endpoints :
  GET /api/leaderboard?queue=all&sort=mmr&search=&limit=50
  GET /api/player/<discord_id>
  GET /api/stats
"""

import os
import aiosqlite
import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "data", "database.db")


def get_rang(mmr: int, wins: int) -> str:
    paliers = [
        (2100, 200, "SS"), (1900, 150, "S"),  (1700, 100, "A"),
        (1500, 75,  "B"),  (1350, 50,  "C"),  (1200, 30,  "D"),
        (1050, 15,  "E"),  (900,  5,   "F"),
    ]
    for mmr_min, wins_min, nom in paliers:
        if mmr >= mmr_min and wins >= wins_min:
            return nom
    return "NC"

def get_queue(mmr: int) -> str:
    if mmr >= 1900: return "ssl"
    if mmr >= 1500: return "gc"
    if mmr >= 1200: return "champion"
    return "open"

def score_classement(mmr: int, wins: int, losses: int) -> float:
    total   = wins + losses
    winrate = (wins / total) if total > 0 else 0
    poids   = min(total / 20, 1.0)
    return (winrate * 0.5 + (mmr / 2000) * 0.35 + (total / 100) * 0.15) * poids


app = FastAPI(title="DME 6Mans API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"status": "DME 6Mans API en ligne"}


@app.get("/api/leaderboard")
async def leaderboard(
    queue:  str = Query("all"),
    sort:   str = Query("mmr"),
    search: str = Query(""),
    limit:  int = Query(50, le=200),
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT discord_id, username, mmr,
                   COALESCE(wins, 0)   AS wins,
                   COALESCE(losses, 0) AS losses
            FROM players
            ORDER BY mmr DESC
        """) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    data = []
    for r in rows:
        total = r["wins"] + r["losses"]
        data.append({
            **r,
            "rang":    get_rang(r["mmr"], r["wins"]),
            "queue":   get_queue(r["mmr"]),
            "pts":     r["wins"] * 3 + r["losses"],
            "winrate": round(r["wins"] / total * 100) if total else 0,
            "score":   score_classement(r["mmr"], r["wins"], r["losses"]),
        })

    if queue != "all":
        data = [p for p in data if p["queue"] == queue]
    if search:
        data = [p for p in data if search.lower() in p["username"].lower()]

    if sort == "wins":    data.sort(key=lambda p: p["wins"],              reverse=True)
    elif sort == "winrate": data.sort(key=lambda p: p["winrate"],         reverse=True)
    elif sort == "matchs":  data.sort(key=lambda p: p["wins"]+p["losses"],reverse=True)
    elif sort == "pts":     data.sort(key=lambda p: p["pts"],             reverse=True)
    elif sort == "score":   data.sort(key=lambda p: p["score"],           reverse=True)
    else:                   data.sort(key=lambda p: p["mmr"],             reverse=True)

    stats = {
        "totalJoueurs": len(data),
        "totalMatchs":  sum(p["wins"] + p["losses"] for p in data),
        "totalMmr":     sum(p["mmr"] for p in data),
    }

    return {"joueurs": data[:limit], "stats": stats}


@app.get("/api/player/{discord_id}")
async def get_player(discord_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"error": "Joueur introuvable"}
    r = dict(row)
    total = r["wins"] + r["losses"]
    return {
        **r,
        "rang":    get_rang(r["mmr"], r["wins"]),
        "queue":   get_queue(r["mmr"]),
        "pts":     r["wins"] * 3 + r["losses"],
        "winrate": round(r["wins"] / total * 100) if total else 0,
    }


@app.get("/api/stats")
async def stats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) as total, SUM(wins) as wins, SUM(losses) as losses FROM players"
        ) as cur:
            row = dict(await cur.fetchone())
        async with db.execute(
            "SELECT COUNT(*) as total FROM matches WHERE status='finished'"
        ) as cur:
            matchs = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT name FROM seasons WHERE active=1 ORDER BY id DESC LIMIT 1"
        ) as cur:
            season = await cur.fetchone()

    return {
        "totalJoueurs": row["total"] or 0,
        "totalMatchs":  matchs or 0,
        "totalWins":    row["wins"] or 0,
        "saison":       season[0] if season else "Saison 1",
    }


def start_api():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")