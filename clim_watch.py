#!/usr/bin/env python3
"""clim-watch — surveille la dispo du Midea PortaSplit chez plusieurs enseignes.

Usage :
  python clim_watch.py                 # un passage (idéal pour le Planificateur de tâches)
  python clim_watch.py --loop          # tourne en boucle (intervalle = config)
  python clim_watch.py --headful       # navigateur visible (debug)
  python clim_watch.py --self-test     # données factices, teste notif+dashboard sans navigateur
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import yaml

import dashboard
import notify
from models import Availability, IN_STOCK, OUT_OF_STOCK
from state import StateStore

HERE = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Surcharges par variables d'environnement (utile en CI / GitHub Actions :
    # on garde les secrets hors du dépôt public).
    topic = os.environ.get("VMC_NTFY_TOPIC")
    if topic:
        cfg.setdefault("notifications", {}).setdefault("ntfy", {})
        cfg["notifications"]["ntfy"]["enabled"] = True
        cfg["notifications"]["ntfy"]["topic_url"] = topic
    hlat, hlon = os.environ.get("VMC_HOME_LAT"), os.environ.get("VMC_HOME_LON")
    if hlat and hlon:
        try:
            cfg["home"] = {"lat": float(hlat), "lon": float(hlon)}
        except ValueError:
            pass
    return cfg


def run_checks(cfg: dict, headful: bool) -> list[Availability]:
    """Lance un vrai passage Playwright sur toutes les enseignes activées."""
    from playwright.sync_api import sync_playwright
    from checkers import REGISTRY
    from checkers.base import make_browser_context

    zones = cfg["zones"]
    results: list[Availability] = []
    with sync_playwright() as pw:
        browser, context = make_browser_context(pw, headless=not headful)
        try:
            for retailer_key, rcfg in cfg["retailers"].items():
                if not rcfg.get("enabled", True):
                    continue
                checker_cls = REGISTRY.get(retailer_key)
                if not checker_cls:
                    print(f"[warn] enseigne inconnue: {retailer_key}")
                    continue
                checker = checker_cls({**rcfg, "home": cfg.get("home")})
                try:
                    res = list(checker.check(context, rcfg["products"], zones))
                    results.extend(res)
                    print(f"[check] {checker.name}: {len(res)} ligne(s)")
                except Exception as e:
                    print(f"[check] {checker.name}: ECHEC -> {e}")
        finally:
            context.close()
            browser.close()
    return results


def self_test_results() -> list[Availability]:
    """Jeu de données factice pour tester notif + dashboard + dédup."""
    return [
        Availability("Castorama", "Midea PortaSplit 3500W", "8431312260509",
                     "casto-1493", "Castorama Cormeilles-en-Parisis", "Paris", IN_STOCK,
                     "retrait:Available · magasin:InStock · livraison:Available",
                     "https://www.castorama.fr/...", 8.4, 48.9539, 2.2031),
        Availability("Castorama", "Midea PortaSplit 3500W", "8431312260509",
                     "casto-1486", "Castorama Place de Clichy", "Paris", OUT_OF_STOCK,
                     "retrait:NotAvailable · magasin:NotStockedInStore",
                     "https://www.castorama.fr/...", 5.1, 48.8850, 2.3296),
        Availability("Castorama", "Midea PortaSplit 3500W", "8431312260509",
                     "casto-lyon", "Castorama Lyon", "Lyon", IN_STOCK,
                     "retrait:Available", "https://www.castorama.fr/...", 392.0, 45.7640, 4.8357),
        Availability("Boulanger", "Midea PortaSplit", "1216685",
                     "en-ligne", "En ligne (livraison/retrait)", "—", IN_STOCK,
                     "achetable en ligne", "https://www.boulanger.com/ref/1216685", None),
    ]


def one_pass(cfg: dict, state: StateStore, headful: bool, self_test: bool) -> None:
    results = self_test_results() if self_test else run_checks(cfg, headful)

    to_notify = state.update(results)
    state.save()

    dash_path = HERE / cfg.get("output", {}).get("dashboard", "dashboard.html")
    dashboard.render(results, dash_path, home=cfg.get("home"))

    n_dispo = sum(1 for r in results if r.status == IN_STOCK)
    print(f"[pass] {len(results)} lignes | {n_dispo} dispo | "
          f"{len(to_notify)} nouvelle(s) -> notif")
    print(f"[pass] dashboard: {dash_path}")

    if to_notify:
        notify.dispatch(cfg["notifications"], to_notify)


def main() -> int:
    ap = argparse.ArgumentParser(description="clim-watch")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--loop", action="store_true", help="boucle infinie")
    ap.add_argument("--headful", action="store_true", help="navigateur visible")
    ap.add_argument("--self-test", action="store_true", help="données factices")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    state = StateStore(HERE / cfg.get("output", {}).get("state", "state.json"))

    if not args.loop:
        one_pass(cfg, state, args.headful, args.self_test)
        return 0

    interval = int(cfg.get("interval_minutes", 60)) * 60
    print(f"[loop] passage toutes les {interval//60} min. Ctrl+C pour arrêter.")
    while True:
        try:
            one_pass(cfg, state, args.headful, args.self_test)
        except KeyboardInterrupt:
            print("\n[loop] arrêt.")
            return 0
        except Exception as e:
            print(f"[loop] erreur passage: {e}", file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
