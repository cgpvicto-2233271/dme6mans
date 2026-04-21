"""
utils/mmr.py  —  DME 6Mans
Moteur Elo complet, rangs DME, équilibrage d'équipes.
"""

from __future__ import annotations
from itertools import combinations
from typing import Tuple

PLACEMENT_MATCHES = 10

# ── MMR de départ selon le rang RL détecté ────────────────────────────────────

RL_RANK_TO_MMR = {
    "bronze":            600,
    "silver":            700,
    "gold":              800,
    "platinum":          900,
    "diamond":           1050,
    "champion":          1200,
    "grand champion":    1450,
    "grand champion 1":  1450,
    "grand champion 2":  1600,
    "grand champion 3":  1750,
    "supersonic legend": 1900,
}

# Mapping tracker MMR → MMR DME de départ (interpolation linéaire)
_TRACKER_TO_DME = [
    (0,    600),
    (300,  650),
    (500,  700),
    (700,  780),
    (900,  880),
    (1050, 950),
    (1200, 1050),
    (1400, 1180),
    (1600, 1300),
    (1800, 1450),
    (2000, 1600),
    (2200, 1780),
    (2400, 1900),
    (9999, 2100),
]

# ── Paliers de rang DME ────────────────────────────────────────────────────────

_PALIERS = [
    # (wins_min, mmr_min, nom, emoji)
    (200, 2100, "SS",          "👑"),
    (150, 1900, "S",           "💠"),
    (100, 1700, "A",           "🟣"),
    (75,  1500, "B",           "🔵"),
    (50,  1350, "C",           "🟢"),
    (30,  1200, "D",           "🟡"),
    (15,  1050, "E",           "🟠"),
    (5,    900, "F",           "🔴"),
    (0,      0, "Non classe",  "⬛"),
]


def rang_dme(mmr: int, wins: int) -> Tuple[str, str]:
    """Retourne (nom_rang, emoji) selon MMR + victoires."""
    for wins_min, mmr_min, nom, emoji in _PALIERS:
        if wins >= wins_min and mmr >= mmr_min:
            return nom, emoji
    return "Non classe", "⬛"


def seuil_min_queue(file_nom: str) -> int:
    mapping = {"open": 0, "champion": 1200, "gc": 1500, "ssl": 1900}
    return mapping.get(file_nom, 0)


def mmr_depuis_rang_rl(rang_rl: str) -> int:
    """Convertit un rang RL textuel en MMR DME de départ."""
    rang_lower = rang_rl.lower().strip()
    for key, mmr in RL_RANK_TO_MMR.items():
        if key in rang_lower:
            return mmr
    return 1000


def tracker_mmr_to_dme(tracker_mmr: int) -> int:
    """Convertit un MMR tracker.gg en MMR DME de départ (interpolation)."""
    pts = _TRACKER_TO_DME
    for i in range(len(pts) - 1):
        t_lo, d_lo = pts[i]
        t_hi, d_hi = pts[i + 1]
        if t_lo <= tracker_mmr < t_hi:
            ratio = (tracker_mmr - t_lo) / (t_hi - t_lo)
            return round(d_lo + ratio * (d_hi - d_lo))
    return 1000


# ── Moteur Elo ────────────────────────────────────────────────────────────────

def score_attendu(mmr_joueur: int, mmr_adverse: float) -> float:
    return 1 / (1 + 10 ** ((mmr_adverse - mmr_joueur) / 400))


def facteur_k(mmr: int, matchs_joues: int) -> int:
    if matchs_joues < PLACEMENT_MATCHES:
        return 48
    if mmr < 1200:
        return 32
    if mmr < 1500:
        return 28
    if mmr < 1800:
        return 24
    return 20


def calculer_mmr(
    mmr_joueur: int,
    mmr_adverse: float,
    victoire: bool,
    matchs_joues: int,
) -> Tuple[int, int]:
    """Retourne (nouveau_mmr, variation)."""
    attendu = score_attendu(mmr_joueur, mmr_adverse)
    reel = 1.0 if victoire else 0.0
    k = facteur_k(mmr_joueur, matchs_joues)
    variation = round(k * (reel - attendu))
    nouveau = max(100, mmr_joueur + variation)
    return nouveau, variation


def moyenne_equipe(equipe: list[dict]) -> float:
    return sum(j["mmr"] for j in equipe) / len(equipe)


def calculer_mmr_equipes(
    gagnants: list[dict],
    perdants: list[dict],
) -> dict[int, Tuple[int, int]]:
    """
    Calcul Elo pour toute une équipe.
    Retourne {discord_id: (nouveau_mmr, variation)}.
    """
    moyenne_gagnants = moyenne_equipe(gagnants)
    moyenne_perdants = moyenne_equipe(perdants)
    resultats: dict[int, Tuple[int, int]] = {}

    for joueur in gagnants:
        matchs_joues = joueur.get("wins", 0) + joueur.get("losses", 0)
        resultats[joueur["discord_id"]] = calculer_mmr(
            joueur["mmr"], moyenne_perdants, True, matchs_joues
        )

    for joueur in perdants:
        matchs_joues = joueur.get("wins", 0) + joueur.get("losses", 0)
        resultats[joueur["discord_id"]] = calculer_mmr(
            joueur["mmr"], moyenne_gagnants, False, matchs_joues
        )

    return resultats


# ── Équilibrage automatique ───────────────────────────────────────────────────

def find_balanced_teams(
    players: list[dict],
) -> Tuple[list[dict], list[dict]]:
    """
    Trouve la répartition 3v3 qui minimise la différence de MMR moyen.
    Retourne (team_orange, team_blue).
    """
    n = len(players)
    half = n // 2
    mmrs = [p["mmr"] for p in players]

    best_diff = float("inf")
    best_a: list[int] = []
    best_b: list[int] = []

    for combo in combinations(range(n), half):
        remaining = [i for i in range(n) if i not in combo]
        avg_a = sum(mmrs[i] for i in combo) / half
        avg_b = sum(mmrs[i] for i in remaining) / half
        diff = abs(avg_a - avg_b)
        if diff < best_diff:
            best_diff = diff
            best_a = list(combo)
            best_b = remaining

    return [players[i] for i in best_a], [players[i] for i in best_b]


# ── Affichage ────────────────────────────────────────────────────────────────

def mmr_diff_label(avg_orange: float, avg_blue: float) -> str:
    diff = abs(avg_orange - avg_blue)
    if diff < 30:
        return f"⚖️ Match équilibré (Δ{diff:.0f})"
    if diff < 80:
        return f"🟡 Léger avantage (Δ{diff:.0f})"
    if diff < 150:
        return f"🟠 Déséquilibre modéré (Δ{diff:.0f})"
    return f"🔴 Match déséquilibré (Δ{diff:.0f})"


def mmr_change_arrow(change: int) -> str:
    if change > 0:
        return f"▲ +{change}"
    if change < 0:
        return f"▼ {change}"
    return "─ 0"
