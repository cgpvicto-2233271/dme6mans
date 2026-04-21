"""
utils/database.py  —  DME 6Mans
Base de données SQLite avec aiosqlite.
Rétro-compatible avec la structure existante + nouvelles tables premium.
"""

import aiosqlite
import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

from utils.logger import setup_logger

log = setup_logger("database")


class Database:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")

            # ── Tables existantes ──────────────────────────────────────────────
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS seasons (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT NOT NULL,
                    active     INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS players (
                    discord_id  INTEGER PRIMARY KEY,
                    username    TEXT NOT NULL,
                    mmr         INTEGER DEFAULT 1000,
                    wins        INTEGER DEFAULT 0,
                    losses      INTEGER DEFAULT 0,
                    created_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS queue (
                    discord_id  INTEGER PRIMARY KEY,
                    queue_name  TEXT NOT NULL DEFAULT 'open',
                    joined_at   TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS matches (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    season_id               INTEGER NOT NULL,
                    queue_name              TEXT NOT NULL,
                    status                  TEXT DEFAULT 'active',
                    team_orange             TEXT NOT NULL DEFAULT '[]',
                    team_blue               TEXT NOT NULL DEFAULT '[]',
                    draft_pool              TEXT DEFAULT '[]',
                    captain_orange_id       INTEGER,
                    captain_blue_id         INTEGER,
                    next_pick               TEXT DEFAULT 'orange',
                    pick_order              TEXT DEFAULT '[]',
                    winner                  TEXT,
                    score_orange            INTEGER DEFAULT 0,
                    score_blue              INTEGER DEFAULT 0,
                    report_votes            TEXT DEFAULT '{}',
                    channel_text_id         INTEGER,
                    channel_voice_orange_id INTEGER,
                    channel_voice_blue_id   INTEGER,
                    created_at              TEXT DEFAULT (datetime('now')),
                    finished_at             TEXT,
                    FOREIGN KEY(season_id) REFERENCES seasons(id)
                );

                CREATE TABLE IF NOT EXISTS tracker_links (
                    discord_id   INTEGER PRIMARY KEY,
                    tracker_url  TEXT NOT NULL,
                    rl_rank      TEXT NOT NULL DEFAULT '',
                    verified_at  TEXT DEFAULT (datetime('now'))
                );
            """)

            # Saison par défaut
            await db.execute(
                "INSERT INTO seasons (name, active)"
                " SELECT 'Saison 1', 1 WHERE NOT EXISTS (SELECT 1 FROM seasons)"
            )

            # ── Nouvelles tables premium ───────────────────────────────────────
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS mmr_history (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    discord_id INTEGER NOT NULL,
                    mmr_before INTEGER NOT NULL,
                    mmr_after  INTEGER NOT NULL,
                    change     INTEGER NOT NULL,
                    reason     TEXT DEFAULT 'match',
                    match_id   INTEGER,
                    season_id  INTEGER,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS queue_bans (
                    discord_id   INTEGER PRIMARY KEY,
                    reason       TEXT DEFAULT 'Aucune raison',
                    banned_by    INTEGER,
                    banned_until TEXT,
                    created_at   TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS leaderboard_messages (
                    guild_id    INTEGER PRIMARY KEY,
                    channel_id  INTEGER NOT NULL,
                    message_id  INTEGER NOT NULL,
                    updated_at  TEXT DEFAULT (datetime('now'))
                );
            """)

            # ── Migrations (ajout colonnes sans casser l'existant) ─────────────
            migrations = [
                ("players",       "season_wins",       "INTEGER DEFAULT 0"),
                ("players",       "season_losses",     "INTEGER DEFAULT 0"),
                ("players",       "last_match_at",     "TEXT"),
                ("tracker_links", "platform",          "TEXT"),
                ("tracker_links", "rl_username",       "TEXT"),
                ("tracker_links", "rl_mmr",            "INTEGER"),
                ("tracker_links", "last_checked_at",   "TEXT"),
            ]
            for table, col, col_type in migrations:
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                    log.info("Migration: %s.%s ajouté", table, col)
                except Exception:
                    pass  # colonne déjà présente

            await db.commit()
        log.info("Base de données prête: %s", self.path)

    async def init_verify_table(self):
        """Maintenu pour rétro-compatibilité — fusionné dans init()."""
        pass

    # ── Seasons ───────────────────────────────────────────────────────────────

    async def get_current_season(self) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM seasons WHERE active = 1 ORDER BY id DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else {"id": 1, "name": "Saison 1"}

    async def create_new_season(self, name: str) -> dict:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE seasons SET active = 0")
            await db.execute(
                "UPDATE players SET season_wins = 0, season_losses = 0"
            )
            cur = await db.execute(
                "INSERT INTO seasons (name, active) VALUES (?, 1)", (name,)
            )
            season_id = cur.lastrowid
            await db.commit()
        log.info("Nouvelle saison: %s (id=%d)", name, season_id)
        return {"id": season_id, "name": name}

    # ── Players ───────────────────────────────────────────────────────────────

    async def get_or_create_player(self, discord_id: int, username: str) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT OR IGNORE INTO players (discord_id, username) VALUES (?, ?)",
                (discord_id, username),
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
            ) as cur:
                return dict(await cur.fetchone())

    async def get_player(self, discord_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_mmr(self, discord_id: int, new_mmr: int, won: bool):
        """Met à jour le MMR + W/L après un match et logge dans mmr_history."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT mmr FROM players WHERE discord_id = ?", (discord_id,)
            ) as cur:
                row = await cur.fetchone()
            old_mmr = row["mmr"] if row else new_mmr

            if won:
                await db.execute(
                    """UPDATE players SET mmr=?, wins=wins+1,
                       season_wins=COALESCE(season_wins,0)+1,
                       last_match_at=datetime('now') WHERE discord_id=?""",
                    (new_mmr, discord_id),
                )
            else:
                await db.execute(
                    """UPDATE players SET mmr=?, losses=losses+1,
                       season_losses=COALESCE(season_losses,0)+1,
                       last_match_at=datetime('now') WHERE discord_id=?""",
                    (new_mmr, discord_id),
                )
            await db.commit()

        await self.add_mmr_history(discord_id, old_mmr, new_mmr, reason="win" if won else "loss")

    async def add_mmr_history(
        self,
        discord_id: int,
        mmr_before: int,
        mmr_after: int,
        reason: str = "match",
        match_id: Optional[int] = None,
    ) -> None:
        season = await self.get_current_season()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO mmr_history
                   (discord_id, mmr_before, mmr_after, change, reason, match_id, season_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    discord_id,
                    mmr_before,
                    mmr_after,
                    mmr_after - mmr_before,
                    reason,
                    match_id,
                    season["id"],
                ),
            )
            await db.commit()

    async def get_mmr_history(self, discord_id: int, limit: int = 10) -> List[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM mmr_history WHERE discord_id=? ORDER BY created_at DESC LIMIT ?",
                (discord_id, limit),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def add_win(self, discord_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE players SET wins=wins+1,
                   season_wins=COALESCE(season_wins,0)+1,
                   last_match_at=datetime('now') WHERE discord_id=?""",
                (discord_id,),
            )
            await db.commit()

    async def add_loss(self, discord_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE players SET losses=losses+1,
                   season_losses=COALESCE(season_losses,0)+1,
                   last_match_at=datetime('now') WHERE discord_id=?""",
                (discord_id,),
            )
            await db.commit()

    async def get_leaderboard_by_points(self, limit: int = 15) -> list[dict]:
        """Classement par points (wins*3 + losses) DESC, puis wins et mmr en départage."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT *,
                   (COALESCE(wins,0)*3 + COALESCE(losses,0)) AS points
                   FROM players
                   ORDER BY points DESC, wins DESC, mmr DESC
                   LIMIT ?""",
                (limit,),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def set_mmr(
        self,
        discord_id: int,
        mmr: int,
        reason: str = "admin",
        admin_id: Optional[int] = None,
    ):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT mmr FROM players WHERE discord_id=?", (discord_id,)
            ) as cur:
                row = await cur.fetchone()
            old_mmr = row["mmr"] if row else mmr

            await db.execute(
                "UPDATE players SET mmr=? WHERE discord_id=?", (mmr, discord_id)
            )
            await db.commit()

        if row:
            await self.add_mmr_history(discord_id, old_mmr, mmr, reason=reason)

    async def reset_player(self, discord_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE players SET mmr=1000, wins=0, losses=0,
                   season_wins=0, season_losses=0 WHERE discord_id=?""",
                (discord_id,),
            )
            await db.commit()

    async def delete_player(self, discord_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM players WHERE discord_id=?", (discord_id,))
            await db.commit()

    async def get_leaderboard(self, limit: int = 15) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM players ORDER BY mmr DESC LIMIT ?", (limit,)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def get_total_matches(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM matches WHERE status='finished'"
            ) as cur:
                return (await cur.fetchone())[0]

    async def is_player_in_active_match(self, discord_id: int) -> Optional[int]:
        """Retourne match_id si le joueur est dans un match draft/actif, sinon None."""
        id_str = str(discord_id)
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """SELECT id FROM matches WHERE status IN ('draft','active')
                   AND (team_orange LIKE ? OR team_blue LIKE ? OR draft_pool LIKE ?)""",
                (f"%{id_str}%", f"%{id_str}%", f"%{id_str}%"),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    # ── Queue Bans ────────────────────────────────────────────────────────────

    async def is_banned(self, discord_id: int) -> Optional[dict]:
        """Retourne le ban si le joueur est banni, sinon None."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM queue_bans WHERE discord_id=?
                   AND (banned_until IS NULL OR banned_until > datetime('now'))""",
                (discord_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def ban_player(
        self,
        discord_id: int,
        reason: str,
        banned_by: int,
        hours: Optional[int] = None,
    ) -> None:
        banned_until = None
        if hours:
            banned_until = (
                datetime.utcnow() + timedelta(hours=hours)
            ).strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO queue_bans (discord_id, reason, banned_by, banned_until)"
                " VALUES (?,?,?,?)",
                (discord_id, reason, banned_by, banned_until),
            )
            await db.commit()
        log.info("Ban queue: %d (%s) par %d", discord_id, reason, banned_by)

    async def unban_player(self, discord_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM queue_bans WHERE discord_id=?", (discord_id,)
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_all_bans(self) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM queue_bans"
                " WHERE banned_until IS NULL OR banned_until > datetime('now')"
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ── Queue ─────────────────────────────────────────────────────────────────

    async def queue_join(self, discord_id: int, queue_name: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            try:
                await db.execute(
                    "INSERT INTO queue (discord_id, queue_name) VALUES (?, ?)",
                    (discord_id, queue_name),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def queue_leave(self, discord_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM queue WHERE discord_id=?", (discord_id,)
            )
            await db.commit()
            return cur.rowcount > 0

    async def queue_clear(self, queue_name: Optional[str] = None):
        async with aiosqlite.connect(self.path) as db:
            if queue_name:
                await db.execute(
                    "DELETE FROM queue WHERE queue_name=?", (queue_name,)
                )
            else:
                await db.execute("DELETE FROM queue")
            await db.commit()

    async def queue_count(self, queue_name: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM queue WHERE queue_name=?", (queue_name,)
            ) as cur:
                return (await cur.fetchone())[0]

    async def queue_list(self, queue_name: str) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT p.discord_id, p.username, p.mmr, p.wins, p.losses, q.joined_at
                   FROM queue q JOIN players p ON q.discord_id = p.discord_id
                   WHERE q.queue_name = ? ORDER BY q.joined_at ASC""",
                (queue_name,),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def queue_snapshot(self) -> dict:
        result = {}
        for qname in ["open", "champion", "gc", "ssl"]:
            result[qname] = await self.queue_list(qname)
        return result

    async def get_expired_queue_players(self, minutes: int) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT discord_id, queue_name, joined_at FROM queue
                   WHERE datetime(joined_at) <= datetime('now', ? || ' minutes')""",
                (f"-{minutes}",),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def pop_queue_players(self, queue_name: str, count: int) -> list[dict]:
        players = await self.queue_list(queue_name)
        selected = players[:count]
        async with aiosqlite.connect(self.path) as db:
            for p in selected:
                await db.execute(
                    "DELETE FROM queue WHERE discord_id=?", (p["discord_id"],)
                )
            await db.commit()
        return selected

    # ── Matches ───────────────────────────────────────────────────────────────

    async def get_active_match(self, queue_name: str) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM matches WHERE queue_name=? AND status IN ('draft','active')
                   ORDER BY created_at DESC LIMIT 1""",
                (queue_name,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_match(self, match_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM matches WHERE id=?", (match_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def create_draft_match(
        self,
        season_id: int,
        queue_name: str,
        captain_orange: int,
        captain_blue: int,
        pool: list[int],
    ) -> int:
        pick_order = ["orange", "blue", "blue", "orange"]
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT INTO matches
                   (season_id, queue_name, status, team_orange, team_blue, draft_pool,
                    captain_orange_id, captain_blue_id, next_pick, pick_order)
                   VALUES (?,?,'draft',?,?,?,?,?,'orange',?)""",
                (
                    season_id,
                    queue_name,
                    json.dumps([captain_orange]),
                    json.dumps([captain_blue]),
                    json.dumps(pool),
                    captain_orange,
                    captain_blue,
                    json.dumps(pick_order),
                ),
            )
            await db.commit()
            return cur.lastrowid

    async def create_balanced_match(
        self,
        season_id: int,
        queue_name: str,
        team_orange: list[int],
        team_blue: list[int],
    ) -> int:
        """Crée un match avec équipes déjà équilibrées (sans draft)."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT INTO matches
                   (season_id, queue_name, status, team_orange, team_blue, draft_pool,
                    captain_orange_id, captain_blue_id, next_pick, pick_order)
                   VALUES (?,?,'active',?,?,'[]',?,?,'',?)""",
                (
                    season_id,
                    queue_name,
                    json.dumps(team_orange),
                    json.dumps(team_blue),
                    team_orange[0] if team_orange else 0,
                    team_blue[0] if team_blue else 0,
                    json.dumps([]),
                ),
            )
            await db.commit()
            return cur.lastrowid

    async def draft_pick(self, match_id: int, captain_id: int, joueur_id: int) -> dict:
        match = await self.get_match(match_id)
        if not match:
            raise ValueError("Match introuvable.")
        if match["status"] != "draft":
            raise ValueError("Ce match n'est plus en draft.")

        pool = json.loads(match["draft_pool"])
        pick_order = json.loads(match["pick_order"])
        next_pick = match["next_pick"]

        if next_pick == "orange" and captain_id != match["captain_orange_id"]:
            raise ValueError("Ce n'est pas ton tour (equipe Orange).")
        if next_pick == "blue" and captain_id != match["captain_blue_id"]:
            raise ValueError("Ce n'est pas ton tour (equipe Blue).")
        if joueur_id not in pool:
            raise ValueError("Ce joueur n'est pas dans le pool.")

        pool.remove(joueur_id)
        pick_order.pop(0)

        team_orange = json.loads(match["team_orange"])
        team_blue = json.loads(match["team_blue"])
        if next_pick == "orange":
            team_orange.append(joueur_id)
        else:
            team_blue.append(joueur_id)

        next_team = pick_order[0] if pick_order else None
        new_status = "draft" if pool else "active"

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE matches SET team_orange=?, team_blue=?, draft_pool=?,
                   pick_order=?, next_pick=?, status=? WHERE id=?""",
                (
                    json.dumps(team_orange),
                    json.dumps(team_blue),
                    json.dumps(pool),
                    json.dumps(pick_order),
                    next_team,
                    new_status,
                    match_id,
                ),
            )
            await db.commit()

        return await self.get_match(match_id)

    async def update_match_channels(self, match_id: int, text_id, orange_id, blue_id):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE matches SET channel_text_id=?, channel_voice_orange_id=?,
                   channel_voice_blue_id=? WHERE id=?""",
                (text_id, orange_id, blue_id, match_id),
            )
            await db.commit()

    async def register_report_vote(
        self,
        match_id: int,
        user_id: int,
        winner: str,
        score_orange: int,
        score_blue: int,
    ) -> dict:
        match = await self.get_match(match_id)
        votes = json.loads(match["report_votes"] or "{}")
        votes[str(user_id)] = {
            "winner": winner,
            "score_orange": score_orange,
            "score_blue": score_blue,
        }
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE matches SET report_votes=? WHERE id=?",
                (json.dumps(votes), match_id),
            )
            await db.commit()
        return votes

    async def finish_match(
        self, match_id: int, winner: str, score_orange: int, score_blue: int
    ):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE matches SET status='finished', winner=?, score_orange=?,
                   score_blue=?, finished_at=datetime('now') WHERE id=?""",
                (winner, score_orange, score_blue, match_id),
            )
            await db.commit()

    async def cancel_match(self, match_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE matches SET status='cancelled', finished_at=datetime('now') WHERE id=?",
                (match_id,),
            )
            await db.commit()

    async def get_player_matches(self, discord_id: int, limit: int = 7) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM matches
                   WHERE (team_orange LIKE ? OR team_blue LIKE ?) AND status='finished'
                   ORDER BY finished_at DESC LIMIT ?""",
                (f"%{discord_id}%", f"%{discord_id}%", limit),
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ── Tracker / Vérification ────────────────────────────────────────────────

    async def save_tracker_link(
        self,
        discord_id: int,
        url: str,
        rl_rank: str,
        platform: Optional[str] = None,
        rl_username: Optional[str] = None,
        rl_mmr: Optional[int] = None,
    ):
        """rl_rank maintenu pour rétro-compat (stockait la plateforme dans les vieilles lignes)."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO tracker_links
                   (discord_id, tracker_url, rl_rank, platform, rl_username, rl_mmr, verified_at)
                   VALUES (?,?,?,?,?,?,datetime('now'))""",
                (discord_id, url, rl_rank, platform or rl_rank, rl_username, rl_mmr),
            )
            await db.commit()

    async def update_tracker_data(
        self, discord_id: int, rl_rank: str, rl_mmr: int
    ) -> None:
        """Met à jour le cache rank/MMR du tracker."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE tracker_links SET rl_rank=?, rl_mmr=?, last_checked_at=datetime('now')"
                " WHERE discord_id=?",
                (rl_rank, rl_mmr, discord_id),
            )
            await db.commit()

    async def get_tracker_link(self, discord_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tracker_links WHERE discord_id=?", (discord_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def delete_tracker_link(self, discord_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM tracker_links WHERE discord_id=?", (discord_id,)
            )
            await db.commit()

    # ── Leaderboard Message (persistance entre redémarrages) ─────────────────

    async def get_leaderboard_message(self, guild_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM leaderboard_messages WHERE guild_id=?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def set_leaderboard_message(
        self, guild_id: int, channel_id: int, message_id: int
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO leaderboard_messages
                   (guild_id, channel_id, message_id, updated_at)
                   VALUES (?,?,?,datetime('now'))""",
                (guild_id, channel_id, message_id),
            )
            await db.commit()
