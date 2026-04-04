import json
import os
import aiosqlite
from typing import Optional


class Database:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS seasons (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT NOT NULL,
                    active     INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            # Saison par defaut
            await db.execute("""
                INSERT INTO seasons (name, active)
                SELECT 'Saison 1', 1
                WHERE NOT EXISTS (SELECT 1 FROM seasons)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    discord_id  INTEGER PRIMARY KEY,
                    username    TEXT NOT NULL,
                    mmr         INTEGER DEFAULT 1000,
                    wins        INTEGER DEFAULT 0,
                    losses      INTEGER DEFAULT 0,
                    created_at  TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    discord_id  INTEGER PRIMARY KEY,
                    queue_name  TEXT NOT NULL DEFAULT 'open',
                    joined_at   TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    season_id            INTEGER NOT NULL,
                    queue_name           TEXT NOT NULL,
                    status               TEXT DEFAULT 'active',
                    team_orange          TEXT NOT NULL DEFAULT '[]',
                    team_blue            TEXT NOT NULL DEFAULT '[]',
                    draft_pool           TEXT DEFAULT '[]',
                    captain_orange_id    INTEGER,
                    captain_blue_id      INTEGER,
                    next_pick            TEXT DEFAULT 'orange',
                    pick_order           TEXT DEFAULT '[]',
                    winner               TEXT,
                    score_orange         INTEGER DEFAULT 0,
                    score_blue           INTEGER DEFAULT 0,
                    report_votes         TEXT DEFAULT '{}',
                    channel_text_id      INTEGER,
                    channel_voice_orange_id INTEGER,
                    channel_voice_blue_id   INTEGER,
                    created_at           TEXT DEFAULT (datetime('now')),
                    finished_at          TEXT,
                    FOREIGN KEY(season_id) REFERENCES seasons(id)
                )
            """)
            await db.commit()

    # ── Seasons ────────────────────────────────────────────────────────────────

    async def get_current_season(self) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM seasons WHERE active = 1 ORDER BY id DESC LIMIT 1") as cur:
                row = await cur.fetchone()
                return dict(row) if row else {"id": 1, "name": "Saison 1"}

    async def create_new_season(self, name: str) -> dict:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE seasons SET active = 0")
            cur = await db.execute("INSERT INTO seasons (name, active) VALUES (?, 1)", (name,))
            season_id = cur.lastrowid
            await db.commit()
            return {"id": season_id, "name": name}

    # ── Players ────────────────────────────────────────────────────────────────

    async def get_or_create_player(self, discord_id: int, username: str) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT OR IGNORE INTO players (discord_id, username) VALUES (?, ?)",
                (discord_id, username)
            )
            await db.commit()
            async with db.execute("SELECT * FROM players WHERE discord_id = ?", (discord_id,)) as cur:
                return dict(await cur.fetchone())

    async def get_player(self, discord_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM players WHERE discord_id = ?", (discord_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_mmr(self, discord_id: int, new_mmr: int, won: bool):
        async with aiosqlite.connect(self.path) as db:
            if won:
                await db.execute(
                    "UPDATE players SET mmr = ?, wins = wins + 1 WHERE discord_id = ?",
                    (new_mmr, discord_id)
                )
            else:
                await db.execute(
                    "UPDATE players SET mmr = ?, losses = losses + 1 WHERE discord_id = ?",
                    (new_mmr, discord_id)
                )
            await db.commit()

    async def set_mmr(self, discord_id: int, mmr: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE players SET mmr = ? WHERE discord_id = ?", (mmr, discord_id))
            await db.commit()

    async def reset_player(self, discord_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE players SET mmr = 1000, wins = 0, losses = 0 WHERE discord_id = ?",
                (discord_id,)
            )
            await db.commit()

    async def delete_player(self, discord_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM players WHERE discord_id = ?", (discord_id,))
            await db.commit()

    async def get_leaderboard(self, limit: int = 15) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM players ORDER BY mmr DESC LIMIT ?", (limit,)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ── Queue ──────────────────────────────────────────────────────────────────

    async def queue_join(self, discord_id: int, queue_name: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            try:
                await db.execute(
                    "INSERT INTO queue (discord_id, queue_name) VALUES (?, ?)",
                    (discord_id, queue_name)
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def queue_leave(self, discord_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM queue WHERE discord_id = ?", (discord_id,))
            await db.commit()
            return cur.rowcount > 0

    async def queue_clear(self, queue_name: Optional[str] = None):
        async with aiosqlite.connect(self.path) as db:
            if queue_name:
                await db.execute("DELETE FROM queue WHERE queue_name = ?", (queue_name,))
            else:
                await db.execute("DELETE FROM queue")
            await db.commit()

    async def queue_count(self, queue_name: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT COUNT(*) FROM queue WHERE queue_name = ?", (queue_name,)) as cur:
                return (await cur.fetchone())[0]

    async def queue_list(self, queue_name: str) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            # APRÈS
            async with db.execute("""
            SELECT p.discord_id, p.username, p.mmr, p.wins, p.losses, q.joined_at
            FROM queue q JOIN players p ON q.discord_id = p.discord_id
            WHERE q.queue_name = ?
            ORDER BY q.joined_at ASC
            """, (queue_name,)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def queue_snapshot(self) -> dict:
        result = {}
        for qname in ["open", "champion", "gc", "ssl"]:
            result[qname] = await self.queue_list(qname)
        return result
    
    async def get_expired_queue_players(self, minutes: int) -> list[dict]:
        """Retourne les joueurs en file depuis plus de X minutes."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT discord_id, queue_name, joined_at
                FROM queue
                WHERE datetime(joined_at) <= datetime('now', ? || ' minutes')
            """, (f"-{minutes}",)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def pop_queue_players(self, queue_name: str, count: int) -> list[dict]:
        players = await self.queue_list(queue_name)
        selected = players[:count]
        async with aiosqlite.connect(self.path) as db:
            for p in selected:
                await db.execute("DELETE FROM queue WHERE discord_id = ?", (p["discord_id"],))
            await db.commit()
        return selected

    # ── Matches ────────────────────────────────────────────────────────────────

    async def get_active_match(self, queue_name: str) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM matches
                WHERE queue_name = ? AND status IN ('draft', 'active')
                ORDER BY created_at DESC LIMIT 1
            """, (queue_name,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_match(self, match_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def create_draft_match(
        self, season_id: int, queue_name: str,
        captain_orange: int, captain_blue: int, pool: list[int]
    ) -> int:
        pick_order = ["orange", "blue", "blue", "orange"]
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""
                INSERT INTO matches
                (season_id, queue_name, status, team_orange, team_blue, draft_pool,
                 captain_orange_id, captain_blue_id, next_pick, pick_order)
                VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, 'orange', ?)
            """, (
                season_id, queue_name,
                json.dumps([captain_orange]),
                json.dumps([captain_blue]),
                json.dumps(pool),
                captain_orange, captain_blue,
                json.dumps(pick_order)
            ))
            await db.commit()
            return cur.lastrowid

    async def draft_pick(self, match_id: int, captain_id: int, joueur_id: int) -> dict:
        match = await self.get_match(match_id)
        if not match:
            raise ValueError("Match introuvable.")
        if match["status"] != "draft":
            raise ValueError("Ce match n'est plus en draft.")

        pool       = json.loads(match["draft_pool"])
        pick_order = json.loads(match["pick_order"])
        next_pick  = match["next_pick"]
        cap_orange = match["captain_orange_id"]
        cap_blue   = match["captain_blue_id"]

        if next_pick == "orange" and captain_id != cap_orange:
            raise ValueError("Ce n'est pas ton tour (equipe Orange).")
        if next_pick == "blue" and captain_id != cap_blue:
            raise ValueError("Ce n'est pas ton tour (equipe Blue).")
        if joueur_id not in pool:
            raise ValueError("Ce joueur n'est pas dans le pool.")

        pool.remove(joueur_id)
        pick_order.pop(0)

        team_orange = json.loads(match["team_orange"])
        team_blue   = json.loads(match["team_blue"])
        if next_pick == "orange":
            team_orange.append(joueur_id)
        else:
            team_blue.append(joueur_id)

        next_team  = pick_order[0] if pick_order else None
        new_status = "draft" if pool else "active"

        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE matches SET team_orange=?, team_blue=?, draft_pool=?,
                pick_order=?, next_pick=?, status=? WHERE id=?
            """, (
                json.dumps(team_orange), json.dumps(team_blue),
                json.dumps(pool), json.dumps(pick_order),
                next_team, new_status, match_id
            ))
            await db.commit()

        return await self.get_match(match_id)

    async def update_match_channels(self, match_id: int, text_id, orange_id, blue_id):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE matches SET channel_text_id=?, channel_voice_orange_id=?, channel_voice_blue_id=?
                WHERE id=?
            """, (text_id, orange_id, blue_id, match_id))
            await db.commit()

    async def register_report_vote(self, match_id: int, user_id: int, winner: str, score_orange: int, score_blue: int) -> dict:
        match = await self.get_match(match_id)
        votes = json.loads(match["report_votes"] or "{}")
        votes[str(user_id)] = {"winner": winner, "score_orange": score_orange, "score_blue": score_blue}
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE matches SET report_votes=? WHERE id=?", (json.dumps(votes), match_id))
            await db.commit()
        return votes

    async def finish_match(self, match_id: int, winner: str, score_orange: int, score_blue: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE matches SET status='finished', winner=?, score_orange=?, score_blue=?,
                finished_at=datetime('now') WHERE id=?
            """, (winner, score_orange, score_blue, match_id))
            await db.commit()

    async def cancel_match(self, match_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE matches SET status='cancelled' WHERE id=?", (match_id,))
            await db.commit()

    async def get_player_matches(self, discord_id: int, limit: int = 7) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM matches
                WHERE (team_orange LIKE ? OR team_blue LIKE ?) AND status='finished'
                ORDER BY finished_at DESC LIMIT ?
            """, (f"%{discord_id}%", f"%{discord_id}%", limit)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ── Tracker / Verification ─────────────────────────────────────────────────

    async def init_verify_table(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tracker_links (
                    discord_id  INTEGER PRIMARY KEY,
                    tracker_url TEXT NOT NULL,
                    rl_rank     TEXT NOT NULL,
                    verified_at TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.commit()

    async def save_tracker_link(self, discord_id: int, url: str, rl_rank: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO tracker_links (discord_id, tracker_url, rl_rank)
                VALUES (?, ?, ?)
            """, (discord_id, url, rl_rank))
            await db.commit()

    async def get_tracker_link(self, discord_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tracker_links WHERE discord_id = ?", (discord_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def delete_tracker_link(self, discord_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM tracker_links WHERE discord_id = ?", (discord_id,))
            await db.commit()