from __future__ import annotations

PLACEMENT_MATCHES = 10

# MMR de depart selon le rang RL detecte sur RLStats
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

def calculer_mmr(mmr_joueur: int, mmr_adverse: float, victoire: bool, matchs_joues: int) -> tuple[int, int]:
    attendu  = score_attendu(mmr_joueur, mmr_adverse)
    reel     = 1.0 if victoire else 0.0
    k        = facteur_k(mmr_joueur, matchs_joues)
    variation = round(k * (reel - attendu))
    nouveau  = max(100, mmr_joueur + variation)
    return nouveau, variation

def moyenne_equipe(equipe: list[dict]) -> float:
    return sum(j["mmr"] for j in equipe) / len(equipe)

def calculer_mmr_equipes(gagnants: list[dict], perdants: list[dict]) -> dict[int, tuple[int, int]]:
    moyenne_gagnants = moyenne_equipe(gagnants)
    moyenne_perdants = moyenne_equipe(perdants)
    resultats: dict[int, tuple[int, int]] = {}
    for joueur in gagnants:
        matchs_joues = joueur["wins"] + joueur["losses"]
        resultats[joueur["discord_id"]] = calculer_mmr(joueur["mmr"], moyenne_perdants, True, matchs_joues)
    for joueur in perdants:
        matchs_joues = joueur["wins"] + joueur["losses"]
        resultats[joueur["discord_id"]] = calculer_mmr(joueur["mmr"], moyenne_gagnants, False, matchs_joues)
    return resultats

def rang_dme(mmr: int, wins: int) -> tuple[str, str]:
    paliers = [
        (200, 2100, "SS",         "👑"),
        (150, 1900, "S",          "💠"),
        (100, 1700, "A",          "🟣"),
        (75,  1500, "B",          "🔵"),
        (50,  1350, "C",          "🟢"),
        (30,  1200, "D",          "🟡"),
        (15,  1050, "E",          "🟠"),
        (5,    900, "F",          "🔴"),
        (0,      0, "Non classe", "⬛"),
    ]
    for wins_min, mmr_min, nom, emoji in paliers:
        if wins >= wins_min and mmr >= mmr_min:
            return nom, emoji
    return "Non classe", "⬛"

def seuil_min_queue(file_nom: str) -> int:
    # Champion = 1200, GC = 1500, SSL = 1900
    mapping = {"open": 0, "champion": 1200, "gc": 1500, "ssl": 1900}
    return mapping.get(file_nom, 0)

def mmr_depuis_rang_rl(rang_rl: str) -> int:
    rang_lower = rang_rl.lower().strip()
    for key, mmr in RL_RANK_TO_MMR.items():
        if key in rang_lower:
            return mmr
    return 1000