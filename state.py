"""Persistance de l'état + détection des transitions.

But : ne déclencher une notification que lorsqu'un point de vente PASSE
de "non dispo / inconnu" à "dispo". On évite ainsi le spam à chaque passage.
"""
from __future__ import annotations

import json
import time
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

    # Nombre de passages « rupture » consécutifs avant de réarmer la notif
    # (anti-flap : un 0 isolé de l'API ne doit pas re-déclencher une « 1re dispo »).
    # Compromis : avec un passage toutes les ~20 min, une vraie rupture n'est
    # réarmée qu'après ~40 min ; un réassort plus rapide que ça n'est pas notifié
    # comme « 1re dispo » (il l'est comme réassort s'il dépasse min_restock_delta).
    OOS_RESET_STREAK = 2

    def update(self, results: list[Availability], max_notify_km: float | None = None,
               min_restock_delta: int = 1) -> list[Availability]:
        """Met à jour l'état et renvoie la liste des résultats à NOTIFIER.

        Règles anti-bruit :
          - 1re dispo (seuil=0) ou dispo booléenne -> notif ;
          - réassort (hausse de quantité) -> notif seulement si delta >= min_restock_delta ;
          - magasin plus loin que max_notify_km -> jamais notifié (suivi silencieux) ;
            les lignes sans distance (en ligne) sont toujours éligibles ;
          - rupture : réarmement seulement après OOS_RESET_STREAK passages consécutifs ;
          - statut inconnu : on ne touche à rien (pas de réarmement).
        """
        to_notify: list[Availability] = []
        for r in results:
            prev_entry = self._data.get(r.key, {})
            notified = bool(prev_entry.get("notified", False))      # enseignes sans quantité
            wm = int(prev_entry.get("notified_qty", 0))             # seuil quantité (Castorama)
            oos = int(prev_entry.get("oos_streak", 0))
            qty = r.quantity
            far = (max_notify_km is not None and r.distance_km is not None
                   and r.distance_km > max_notify_km)

            if r.status == IN_STOCK:
                oos = 0
                if qty is None:
                    # Boulanger / Optimea : pas de quantité -> transition booléenne.
                    if not notified:
                        if far:
                            notified = True          # suivi silencieux
                        else:
                            to_notify.append(r)
                else:
                    if qty > wm:
                        delta = qty - wm
                        if far:
                            wm = qty                 # suivi silencieux, pas de rattrapage
                        elif wm == 0 or delta >= min_restock_delta:
                            r.restock = wm > 0
                            r.delta = delta
                            to_notify.append(r)
                            # seuil avancé par mark_notified après envoi réussi
                        # sinon : petite hausse, on garde le seuil -> les hausses
                        # successives se cumulent jusqu'à atteindre le seuil.
                    elif qty < wm:
                        # baisse (achats) : on abaisse le seuil, sans notifier.
                        wm = qty
            elif r.status == UNKNOWN:
                pass                                 # mesure non fiable : état inchangé
            else:
                oos += 1
                if oos >= self.OOS_RESET_STREAK:
                    notified = False
                    wm = 0

            self._data[r.key] = {
                "status": r.status,
                "detail": r.detail,
                "store_name": r.store_name,
                "checked_at": r.checked_at,
                "notified": notified,
                "notified_qty": wm,
                "oos_streak": oos,
            }
        return to_notify

    # --- Cadence des push (cooldown) -------------------------------------
    NOTIFY_KEY = "__notify__"

    def last_push_ts(self) -> int:
        return int(self._data.get(self.NOTIFY_KEY, {}).get("last_push_ts", 0))

    def set_last_push_ts(self, ts: int) -> None:
        self._data.setdefault(self.NOTIFY_KEY, {})["last_push_ts"] = int(ts)

    def pending_health(self) -> dict:
        return dict(self._data.get(self.NOTIFY_KEY, {}).get("pending_health", {}))

    def set_pending_health(self, broken, recovered) -> None:
        n = self._data.setdefault(self.NOTIFY_KEY, {})
        if broken or recovered:
            n["pending_health"] = {"broken": list(broken), "recovered": list(recovered)}
        else:
            n.pop("pending_health", None)

    def mark_notified(self, results) -> None:
        """À appeler APRÈS un envoi réussi. Fige le seuil au niveau notifié
        (quantité pour Castorama, booléen pour les autres)."""
        for r in results:
            e = self._data.get(r.key)
            if not e:
                continue
            if r.quantity is None:
                e["notified"] = True
            else:
                e["notified_qty"] = int(r.quantity)

    # ------------------------------------------------------------------
    # Surveillance de l'état de marche des enseignes (« alerte panne »).
    # Une enseigne est considérée « muette » sur un passage si elle ne renvoie
    # aucune ligne, ou que des statuts « inconnu » (scraping cassé / site qui
    # change / blocage). On alerte après HEALTH_THRESHOLD passages muets
    # consécutifs, une seule fois, puis on signale le rétablissement.
    # ------------------------------------------------------------------
    HEALTH_KEY = "__health__"
    HEALTH_THRESHOLD = 3

    def health_update(self, expected_names, results, now_ts: int | None = None):
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
                h["last_ok_ts"] = int(now_ts or time.time())
            health[name] = h
        self._data[self.HEALTH_KEY] = health
        return broken_now, recovered_now

    # ------------------------------------------------------------------
    # Historique des stocks (Castorama = quantité par magasin).
    #  - heure de 1re apparition (first_seen) par magasin
    #  - série par magasin (points de changement) pour l'évolution
    #  - série du cumul national (un point par passage)
    # Tout est borné pour ne pas faire gonfler state.json.
    # ------------------------------------------------------------------
    HISTORY_KEY = "__history__"
    NATIONAL_MAX = 2016          # ~14 j à 10 min
    STORE_SERIES_MAX = 300       # points de changement par magasin
    STORE_RETENTION = 14 * 24 * 3600  # purge des magasins inactifs (s)

    def history(self) -> dict:
        return self._data.get(self.HISTORY_KEY, {"national": [], "stores": {}})

    NATIONAL_MIN_GAP = 3600      # point « témoin » au moins toutes les heures

    def history_update(self, results, now_ts: int, skip_national: bool = False) -> int:
        hist = self._data.setdefault(self.HISTORY_KEY, {"national": [], "stores": {}})
        stores = hist.setdefault("stores", {})
        nat = hist.setdefault("national", [])

        # Si aucun résultat ne porte de quantité (Castorama muet/bloqué), on ne
        # touche pas à l'historique : pas de faux « 0 », pas de reset des apparitions.
        if not any(r.quantity is not None for r in results):
            return nat[-1][1] if nat else 0

        national_total = 0
        for r in results:
            if r.quantity is None:        # seuls les checkers avec quantité (Castorama)
                continue
            qty = int(r.quantity)
            if qty > 0:
                national_total += qty
            st = stores.setdefault(r.key, {"name": r.store_name, "city": r.store_city,
                                           "first_seen": None, "last": None, "series": []})
            st["name"], st["city"] = r.store_name, r.store_city
            if qty > 0 and not st.get("first_seen"):
                st["first_seen"] = now_ts          # 1re apparition (détection)
            elif qty == 0:
                st["first_seen"] = None
            if qty != st.get("last"):               # n'enregistrer que les changements
                st["series"].append([now_ts, qty])
                if len(st["series"]) > self.STORE_SERIES_MAX:
                    st["series"] = st["series"][-self.STORE_SERIES_MAX:]
            st["last"] = qty

        # cumul national : un point seulement s'il change (ou toutes les heures),
        # pour ne pas modifier state.json à chaque passage. Passage partiel
        # (zone Castorama en échec) -> pas de point : le total serait faux.
        if skip_national:
            return nat[-1][1] if nat else national_total
        if not nat or nat[-1][1] != national_total or now_ts - nat[-1][0] >= self.NATIONAL_MIN_GAP:
            nat.append([now_ts, national_total])
        if len(nat) > self.NATIONAL_MAX:
            hist["national"] = nat[-self.NATIONAL_MAX:]

        # purge des magasins à 0 et inactifs depuis longtemps
        for key in list(stores.keys()):
            st = stores[key]
            last_ts = st["series"][-1][0] if st.get("series") else 0
            if (st.get("last") or 0) == 0 and (now_ts - last_ts) > self.STORE_RETENTION:
                del stores[key]

        return national_total

    # ------------------------------------------------------------------
    # Purge : enseignes désactivées / points de vente plus vus depuis 14 j.
    # ------------------------------------------------------------------
    PURGE_AFTER = 14 * 24 * 3600

    def purge(self, active_retailers, now_ts: int | None = None) -> list[str]:
        from datetime import datetime
        now_ts = int(now_ts or time.time())
        active = set(active_retailers)
        removed = []
        for key in list(self._data):
            if key.startswith("__"):
                continue
            retailer = key.split("|", 1)[0]
            e = self._data[key]
            old = False
            try:
                ts = datetime.fromisoformat(e.get("checked_at", "")).timestamp()
                old = now_ts - ts > self.PURGE_AFTER
            except (TypeError, ValueError):
                pass
            if retailer not in active or old:
                del self._data[key]
                removed.append(key)
        health = self._data.get(self.HEALTH_KEY, {})
        for name in list(health):
            if name not in active:
                del health[name]
        return removed

    def health(self) -> dict:
        return dict(self._data.get(self.HEALTH_KEY, {}))

    def snapshot(self) -> dict[str, dict]:
        return dict(self._data)
