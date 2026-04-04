# DME 6Mans Bot — Installation rapide

## Etape 1 — Remplir le fichier .env
Ouvre le fichier `.env` et remplis les 2 lignes obligatoires:
- DISCORD_TOKEN = ton token bot (portail developpeur Discord)
- GUILD_ID = ID de ton serveur (clic droit sur le serveur > Copier l'identifiant)

## Etape 2 — Installer les dependances
Dans Git Bash depuis le dossier dme-final:
  python -m pip install -r requirements.txt

## Etape 3 — Lancer
  python main.py

## Commandes disponibles
/join queue:open|champion|gc|ssl  — Rejoindre une file
/leave                            — Quitter la file
/queue                            — Voir les files
/pick match_id joueur             — Draft (capitaine)
/reportscore match_id winner ...  — Declarer le score
/profile                          — Voir son MMR
/leaderboard                      — Classement
/history                          — Historique de matchs

## Commandes admin
/setmmr /newseason /clearqueue /cancelmatch /resetplayer /forcematch

## Files par rang
open      — Tout le monde (0 MMR minimum)
champion  — 1300 MMR minimum
gc        — 1500 MMR minimum
ssl       — 1700 MMR minimum
