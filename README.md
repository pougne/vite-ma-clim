# clim-watch — surveillance dispo Midea PortaSplit

Surveille la disponibilité du climatiseur Midea PortaSplit chez plusieurs
enseignes (Castorama, Boulanger…), en ligne **et** en magasin dans un rayon
autour de Paris, puis t'envoie une notification (ntfy/push ou email) dès
qu'un point de vente passe en stock. Génère aussi un dashboard HTML.

## 1. Installation (sur le Vivobook)

```powershell
cd clim-watch
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 2. Calibrage des sélecteurs (étape clé, à faire UNE fois par enseigne)

Castorama et Boulanger sont des sites JavaScript protégés anti-bot. L'outil
pilote un vrai Chromium, mais **les sélecteurs CSS exacts changent d'un site à
l'autre et dans le temps** : il faut les vérifier une fois.

```powershell
python discover.py https://www.boulanger.com/ref/1216685
```

Le navigateur s'ouvre. Clique sur « disponibilité en magasin », saisis un code
postal (ex. 59000), laisse la liste des magasins s'afficher, reviens dans la
console et appuie sur Entrée. Le HTML rendu est capturé dans
`discover_dump.html`, et tu peux tester des sélecteurs en direct.

Reporte ensuite les bons sélecteurs dans `checkers/boulanger.py` /
`checkers/castorama.py` (bloc « SÉLECTEURS À VÉRIFIER » en haut du fichier).
Mêmes étapes pour Castorama avec son URL.

> Astuce : dans les DevTools (F12), onglet **Réseau**, filtre `Fetch/XHR`,
> puis re-saisis le code postal. Si tu repères une requête qui renvoie du JSON
> de stock par magasin, c'est encore plus fiable que de lire le DOM — on peut
> alors appeler cette API directement. Note l'URL, je t'aide à la brancher.

## 3. Configuration

Édite `config.yaml` : zones (codes postaux), produits (ref/url), et au moins
un canal de notification.

- **ntfy** (recommandé, le plus simple) : installe l'appli ntfy, abonne-toi à
  un sujet privé (ex. `clim-portasplit-7h2k9`), mets `https://ntfy.sh/<sujet>`
  dans la config. Zéro inscription.
- **email** : pour Gmail, génère un *mot de passe d'application* (jamais ton
  mot de passe principal).

## 4. Test à blanc (sans navigateur)

```powershell
python clim_watch.py --self-test
```

Injecte des dispos factices : vérifie que tu reçois bien la notif et que
`dashboard.html` se génère. Ouvre `dashboard.html` dans ton navigateur.

## 5. Exécution

```powershell
python clim_watch.py            # un passage
python clim_watch.py --loop     # tourne en continu (intervalle = config)
python clim_watch.py --headful  # navigateur visible (debug)
```

## 6. Planification horaire (Windows Task Scheduler)

Préférable au mode `--loop` (survit aux reboots, veille). Crée une tâche :
- Déclencheur : toutes les 1 heure.
- Action : démarrer `run_once.bat` (adapter le chemin).
- Cocher « Exécuter même si l'utilisateur n'est pas connecté » si besoin.

Les logs s'accumulent dans `clim-watch.log`.

## Notes / limites

- **Anti-bot** : si une enseigne renvoie systématiquement `unknown`, elle te
  bloque peut-être. Lance en `--headful` pour voir, espace les passages
  (1h est raisonnable et discret), et n'augmente pas la fréquence sans raison.
- L'outil ne notifie qu'à la **transition** vers « dispo » (pas de spam tant
  que ça reste en stock). Supprime `state.json` pour réinitialiser.
- Ajouter une enseigne = un fichier `checkers/<nom>.py` calqué sur les deux
  existants + une ligne dans `checkers/__init__.py`.
- Reste raisonnable sur la fréquence : c'est un usage personnel, pas un
  martèlement des serveurs.
```
