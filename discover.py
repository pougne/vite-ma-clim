#!/usr/bin/env python3
"""Outil de CALIBRAGE — capture HTML + requetes (URL, EN-TETES, reponse).

Sert a voir comment le site s'authentifie aupres de ses API (en-tete
Authorization / cle d'API), pour pouvoir les rejouer ensuite.

Usage :
  python discover.py "https://www.castorama.fr/.../8431312260509_CAFR.prd"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from checkers.base import make_browser_context, accept_cookies

HERE = Path(__file__).resolve().parent

NET_KEYS = ("stock", "availab", "inventory", "fulfil", "fulfilment", "magasin",
            "store", "kingfisher", "delivery", "clickandcollect", "click-collect")


def interesting(url: str) -> bool:
    u = url.lower()
    return any(k in u for k in NET_KEYS)


def capture_html(page) -> str:
    for _ in range(6):
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        try:
            return page.content()
        except Exception:
            page.wait_for_timeout(1500)
    try:
        return page.evaluate("() => document.documentElement.outerHTML")
    except Exception:
        return "<!-- capture impossible -->"


def main():
    if len(sys.argv) < 2:
        print('Usage: python discover.py "<url_fiche_produit>"')
        return 1
    url = sys.argv[1]
    captured: list[dict] = []

    with sync_playwright() as pw:
        browser, context = make_browser_context(pw, headless=False)
        page = context.new_page()

        def on_response(resp):
            try:
                if not interesting(resp.url):
                    return
                req = resp.request
                try:
                    rh = req.all_headers()
                except Exception:
                    rh = dict(req.headers)
                try:
                    body = resp.text()[:1500]
                except Exception:
                    body = ""
                captured.append({
                    "method": req.method,
                    "url": resp.url,
                    "status": resp.status,
                    "request_headers": rh,
                    "response_sample": body,
                })
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        accept_cookies(page)

        print("\n=== Navigateur ouvert ===")
        print("1) Saisis un code postal (ex. 59000) / clique 'disponibilite magasin'")
        print("   pour declencher la recherche de magasins (comme la 1re fois).")
        print("2) Attends que la liste/stock magasin s'affiche.")
        print("3) Reviens ici et tape Entree.")
        input("   [Entree pour capturer] ")
        page.wait_for_timeout(1500)

        (HERE / "discover_dump.html").write_text(capture_html(page), encoding="utf-8")
        net = HERE / "discover_network.json"
        net.write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReseau -> {net}  ({len(captured)} requete(s) capturee(s))")
        for c in captured[:20]:
            auth = c["request_headers"].get("authorization", "")
            flag = " [AUTH]" if auth else ""
            print(f"  [{c['status']}] {c['method']} {c['url'][:95]}{flag}")

        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
