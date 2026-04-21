"""
utils/tracker.py  —  DME 6Mans
Intégration tracker.gg (API non-officielle) avec cache 5 min.
Utilisé pour la vérification de rang et la détection de smurfs.
"""

import asyncio
import time
from typing import Dict, Any, Optional

import aiohttp

from utils.logger import setup_logger

log = setup_logger("tracker")

# Mapping des slugs plateforme vers le format tracker.gg
PLATFORM_MAP: Dict[str, str] = {
    "epic":   "epic",
    "steam":  "steam",
    "psn":    "psn",
    "ps4":    "psn",
    "ps5":    "psn",
    "ps":     "psn",
    "xbox":   "xbl",
    "xbl":    "xbl",
    "switch": "nintendo-switch",
    "sw":     "nintendo-switch",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    "Origin": "https://rocketleague.tracker.network",
    "Referer": "https://rocketleague.tracker.network/",
}

CACHE_TTL = 300  # secondes

_cache: Dict[str, Any] = {}


class TrackerProfile:
    """Profil tracker.gg parsé pour un joueur RL."""

    def __init__(
        self,
        platform: str,
        username: str,
        doubles_mmr: Optional[int] = None,
        doubles_rank: Optional[str] = None,
        standard_mmr: Optional[int] = None,
        standard_rank: Optional[str] = None,
        peak_mmr: Optional[int] = None,
    ):
        self.platform = platform
        self.username = username
        self.doubles_mmr = doubles_mmr
        self.doubles_rank = doubles_rank
        self.standard_mmr = standard_mmr
        self.standard_rank = standard_rank
        self.peak_mmr = peak_mmr

    @property
    def best_mmr(self) -> int:
        """MMR compétitif le plus élevé (Doubles ou Standard)."""
        vals = [v for v in [self.standard_mmr, self.doubles_mmr] if v]
        return max(vals) if vals else 0

    @property
    def best_rank(self) -> Optional[str]:
        return self.standard_rank or self.doubles_rank

    def smurf_score(self, internal_mmr: int) -> float:
        """
        Score de suspicion smurf entre 0.0 et 1.0.
        Compare le MMR tracker avec le MMR interne DME.
        """
        tracker = self.best_mmr
        if not tracker or not internal_mmr:
            return 0.0
        delta = tracker - internal_mmr
        if delta <= 100:
            return 0.0
        if delta <= 300:
            return 0.3
        if delta <= 600:
            return 0.6
        return 1.0

    def profile_url(self) -> str:
        return (
            f"https://rocketleague.tracker.network/rocket-league/profile"
            f"/{self.platform}/{self.username}"
        )

    def summary(self) -> str:
        parts = []
        if self.standard_mmr:
            parts.append(f"3v3: **{self.standard_mmr}** ({self.standard_rank or '?'})")
        if self.doubles_mmr:
            parts.append(f"2v2: **{self.doubles_mmr}** ({self.doubles_rank or '?'})")
        return " · ".join(parts) if parts else "Aucune donnée ranked"


def _cache_key(platform: str, username: str) -> str:
    return f"{platform.lower()}:{username.lower()}"


async def fetch_profile(platform: str, username: str) -> Optional[TrackerProfile]:
    """
    Récupère le profil depuis tracker.gg avec cache 5 min.
    Retourne None en cas d'échec (profil introuvable, timeout, rate limit).
    """
    platform_slug = PLATFORM_MAP.get(platform.lower(), platform.lower())
    key = _cache_key(platform_slug, username)
    now = time.time()

    if key in _cache and _cache[key]["expires"] > now:
        log.debug("Cache hit: %s", key)
        return _cache[key]["data"]

    url = (
        f"https://api.tracker.gg/api/v2/rocket-league/standard/profile"
        f"/{platform_slug}/{username}"
    )

    try:
        async with aiohttp.ClientSession(headers=_HEADERS) as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=12)
            ) as resp:
                if resp.status == 404:
                    log.info("Tracker 404: %s / %s", platform_slug, username)
                    _cache[key] = {"data": None, "expires": now + 60}
                    return None
                if resp.status == 429:
                    log.warning("Tracker rate limit: %s / %s", platform_slug, username)
                    return None
                if resp.status != 200:
                    log.warning(
                        "Tracker HTTP %d: %s / %s", resp.status, platform_slug, username
                    )
                    return None
                data = await resp.json(content_type=None)
    except asyncio.TimeoutError:
        log.warning("Tracker timeout: %s / %s", platform_slug, username)
        return None
    except Exception as exc:
        log.error("Tracker error %s / %s: %s", platform_slug, username, exc)
        return None

    profile = _parse_response(platform_slug, username, data)
    _cache[key] = {"data": profile, "expires": now + CACHE_TTL}
    log.info(
        "Tracker fetched: %s / %s → 3v3=%s 2v2=%s",
        platform_slug,
        username,
        profile.standard_mmr if profile else "N/A",
        profile.doubles_mmr if profile else "N/A",
    )
    return profile


def _parse_response(platform: str, username: str, data: dict) -> Optional[TrackerProfile]:
    try:
        segments = data.get("data", {}).get("segments", [])
        doubles_mmr = doubles_rank = standard_mmr = standard_rank = None
        peak_mmr = 0

        for seg in segments:
            if seg.get("type") != "playlist":
                continue
            name = seg.get("metadata", {}).get("name", "")
            stats = seg.get("stats", {})

            mmr_val = stats.get("rating", {}).get("value")
            mmr = int(mmr_val) if mmr_val else None

            tier = stats.get("tier", {}).get("metadata", {}).get("name")
            div = stats.get("division", {}).get("metadata", {}).get("name", "")
            rank = f"{tier} {div}".strip() if tier else None

            p_val = stats.get("peakRating", {}).get("value")
            if p_val:
                peak_mmr = max(peak_mmr, int(p_val))

            if "Doubles" in name or "2v2" in name:
                doubles_mmr, doubles_rank = mmr, rank
            elif "Standard" in name or "3v3" in name:
                standard_mmr, standard_rank = mmr, rank

        return TrackerProfile(
            platform=platform,
            username=username,
            doubles_mmr=doubles_mmr,
            doubles_rank=doubles_rank,
            standard_mmr=standard_mmr,
            standard_rank=standard_rank,
            peak_mmr=peak_mmr or None,
        )
    except Exception as exc:
        log.error("Tracker parse error: %s", exc)
        return None


def invalidate(platform: str, username: str) -> None:
    """Invalide le cache pour forcer un re-fetch."""
    slug = PLATFORM_MAP.get(platform.lower(), platform.lower())
    _cache.pop(_cache_key(slug, username), None)


def cache_stats() -> dict:
    now = time.time()
    valid = sum(1 for v in _cache.values() if v["expires"] > now)
    return {"total": len(_cache), "valid": valid}
