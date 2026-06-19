"""Persistance de l'état + détection des transitions.

But : ne déclencher une notification que lorsqu'un point de vente PASSE
de "non dispo / inconnu" à "dispo". On évite ainsi le spam à chaque passage.
"""
from __future__ import annotations

import json
from pathlib import Path

from models import Availability, IN_STOCK, UNKNOWN


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def previous_status(self, key: str) -> str | None:
        entry = self._data.get(key)
        return entry["status"] if entry else None

    def update(self, results: list[Availability]) -> list[Availability]:
        """Met à jour l'état et renvoie la liste des résultats à NOTIFIER.

        On notifie quand le statut courant est IN_STOCK et que le statut
        précédent n'était PAS IN_STOCK (nouvelle dispo).
        """
        to_notify: list[Availability] = []
        for r in results:
            prev = self.previous_status(r.key)
            if r.status == IN_STOCK and prev != IN_STOCK:
                to_notify.append(r)
            # On mémorise toujours le dernier statut connu.
            self._data[r.key] = {
                "status": r.status,
                "detail": r.detail,
                "store_name": r.store_name,
                "checked_at": r.checked_at,
            }
        return to_notify

    # ------------------------------------------------------------------
    # Surveillance de l'état de marche des enseignes (« alerte panne »).
    # Une enseigne est considérée « muette » sur un passage si elle ne renvoie
    # aucune ligne, ou que des statuts « inconnu » (scraping cassé / site qui
    # change / blocage). On alerte après HEALTH_THRESHOLD passages muets
    # consécutifs, une seule fois, puis on signale le rétablissement.
    # ------------------------------------------------------------------
    HEALTH_KEY = "__health__"
    HEALTH_THRESHOLD = 3

    def health_update(self, expected_names, results):
        health = self._data.get(self.HEALTH_KEY, {})
        by: dict[str, list] = {}
        for r in results:
            by.setdefault(r.retailer, []).append(r.status)
        broken_now, recovered_now = [], []
        for name in expected_names:
            statuses = by.get(name, [])
            mute = (len(statuses) == 0) or all(st == UNKNOWN for st in statuses)
            h = health.get(name, {"streak": 0, "alerted": False})
            if mute:
                h["streak"] = h.get("streak", 0) + 1
                if h["streak"] >= self.HEALTH_THRESHOLD and not h.get("alerted"):
                    broken_now.append(name)
                    h["alerted"] = True
            else:
                if h.get("alerted"):
                    recovered_now.append(name)
                h["streak"] = 0
                h["alerted"] = False
            health[name] = h
        self._data[self.HEALTH_KEY] = health
        return broken_now, recovered_now

    def snapshot(self) -> dict[str, dict]:
        return dict(self._data)
