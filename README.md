# Vite Ma Clim — suivi de dispo du Midea PortaSplit

Surveille le stock du climatiseur **Midea PortaSplit** chez Castorama (stock par
magasin, partout en France), Optimea (vendeur officiel, en ligne) et Boulanger
(désactivé pour l'instant), envoie des **push ntfy** (et/ou email) et publie un
**dashboard** sur GitHub Pages.

## Fonctionnement

- **GitHub Actions** (`.github/workflows/watch.yml`) lance `python clim_watch.py`
  dans l'image Playwright officielle. La cadence réelle (~20 min) est pilotée par
  un pinger externe (cron-job.org → `workflow_dispatch`) ; le `schedule` toutes
  les 2 h n'est qu'un filet de secours.
- Chaque passage : vérification des enseignes actives → mise à jour de
  `state.json` → notifications éventuelles → rendu du dashboard
  (`index.html` + `version.json`) → déploiement Pages.
- `state.json` n'est committé **que s'il a changé utilement**
  (`scripts/state_changed.py` ignore les horodatages `checked_at` / `last_ok_ts`, mais pas le compteur de panne), avec `pull --rebase` et
  3 essais ; le job échoue visiblement si le push est impossible.
- `.github/workflows/tests.yml` lance `pytest` sur chaque pull request et à chaque push sur `master`.

## Notifications (anti-bruit)

Réglages dans `config.yaml`, bloc `notifications.ntfy` :

| Clé | Défaut | Rôle |
|---|---|---|
| `max_notify_km` | 150 | magasins plus loin : suivis en silence (les dispos en ligne sont toujours notifiées) |
| `min_restock_delta` | 3 | hausse minimale pour un « réassort » (la 1re dispo est toujours notifiée) |
| `notify_cooldown_minutes` | 60 | au plus 1 push par période, événements regroupés |
| `cooldown_bypass_km` | 50 | un magasin plus proche passe outre le cooldown |
| `high_priority_km` | 50 | priorité max du push |
| `quiet_hours` | 22:00–07:00 | pas de push (ni email, ni alerte panne) ; tout part groupé à la fin |

Anti-flap : un magasin n'est réarmé qu'après 2 passages « rupture » consécutifs.
Une enseigne muette 3 passages déclenche une alerte « ne répond plus » ; elle est
affichée « hors service » sur le dashboard après `source_down_days` jours (7).

## Secrets GitHub (Settings → Secrets and variables → Actions)

| Secret | Obligatoire | Contenu |
|---|---|---|
| `VMC_NTFY_TOPIC` | oui | URL(s) ntfy, séparées par des virgules (1re = perso) |
| `VMC_CASTO_TOKEN` | oui | identifiant d'application de l'API Kingfisher (Castorama) ; sans lui Castorama est ignoré |
| `VMC_SMTP_HOST`, `VMC_SMTP_USER`, `VMC_SMTP_PASSWORD`, `VMC_SMTP_FROM`, `VMC_SMTP_TO` | non | email (Gmail : mot de passe d'application) ; actif seulement si USER, PASSWORD, FROM **et** TO sont fournis (les adresses d'exemple de `config.yaml` ne sont jamais utilisées) |

Ne mettez **aucun** secret dans `config.yaml` : le dépôt et le dashboard sont publics.
La position de référence (`home`) reste Paris-centre ; chaque visiteur choisit sa
position sur le site (mémorisée dans son navigateur).

## En local

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest && python -m playwright install chromium
export VMC_CASTO_TOKEN=...  VMC_NTFY_TOPIC=https://ntfy.sh/mon-sujet
python clim_watch.py --self-test   # données factices : notif + dashboard
python clim_watch.py               # un vrai passage
python -m pytest -q tests
```

`discover.py <url>` ouvre un navigateur pour recalibrer les sélecteurs d'une
enseigne (Boulanger notamment avant de la réactiver).

## Ajouter une enseigne

Créer `checkers/<nom>.py` sur le modèle d'Optimea, l'enregistrer dans
`checkers/__init__.py`, puis ajouter un bloc dans `retailers:` de `config.yaml`.
