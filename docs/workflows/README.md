# Workflows à installer à la main

Le jeton utilisé pour préparer cette PR n'a pas le droit de modifier
`.github/workflows/`. Copier ces deux fichiers à leur place
(interface GitHub : « Add file » → coller le contenu, ou en local) :

- `watch.yml` → `.github/workflows/watch.yml` : ajoute les secrets
  `VMC_CASTO_TOKEN` / `VMC_SMTP_*`, publie `version.json`, et ne committe
  `state.json` que s'il a changé utilement (pull --rebase + 3 essais, échec visible).
- `tests.yml` → `.github/workflows/tests.yml` : pytest sur chaque pull request.

**Indispensable** : sans le nouveau `watch.yml`, `VMC_CASTO_TOKEN` n'est pas
transmis au script et Castorama est ignoré.
