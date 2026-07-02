"""Checker Boulanger — détection par texte, sans dépendance aux classes CSS.

Structure réelle de la page (vérifiée juin 2026) :
  Section "Options de livraison" :
    • Retrait : Indisponible / Disponible
    • Livraison à domicile : Indisponible / Disponible
  Gros bouton d'action : "Indisponible" (rupture) ou "Ajouter au panier" (dispo).

Stratégie : attendre la fin des appels réseau (JS terminé), lire tout le
texte de la page, puis :
  - DISPO si "ajouter au panier" présent
  - RUPTURE si "options de livraison" présent ET retrait+livraison "indisponible"
    OU gros bouton "indisponible"
  - sinon UNKNOWN
"""
from __future__ import annotations

import re

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, accept_cookies, log

_KW_BUYABLE = re.compile(r"ajouter au panier", re.IGNORECASE)
# Section dispo + mentions d'indisponibilité
_KW_DELIVERY_SECTION = re.compile(r"options? de livraison", re.IGNORECASE)
_KW_RETRAIT_INDISPO = re.compile(
    r"retrait\s*:?\s*indisponible|livraison.{0,30}indisponible", re.IGNORECASE)
_KW_GENERIC_INDISPO = re.compile(
    r"\bindisponible\b|rupture\s+de\s+stock|temporairement indisponible",
    re.IGNORECASE)


class BoulangerChecker(Checker):
    name = "Boulanger"

    def check(self, context, products, zones):
        results: list[Availability] = []
        page = context.new_page()
        try:
            for product in products:
                results.append(self._check_product(page, product))
        finally:
            page.close()
        return results

    def _check_product(self, page, product) -> Availability:
        ref = str(product["ref"])
        label = product.get("label", ref)
        url = product.get("url", f"https://www.boulanger.com/ref/{ref}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            accept_cookies(page)
            # Attendre la fin des appels réseau (JS qui injecte la dispo)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                page.wait_for_timeout(3000)
            # Attendre explicitement que la section livraison soit là
            try:
                page.wait_for_function(
                    "() => /options? de livraison/i.test(document.body.innerText)"
                    " || /ajouter au panier/i.test(document.body.innerText)",
                    timeout=8000)
            except Exception:
                pass
        except Exception as e:
            log(f"Boulanger: chargement KO {url}: {e}")
            return self._mk(label, ref, UNKNOWN, "page inaccessible", url)

        try:
            text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            return self._mk(label, ref, UNKNOWN, "texte introuvable", url)

        text = re.sub(r"\s+", " ", text)

        has_cart = bool(_KW_BUYABLE.search(text))
        has_section = bool(_KW_DELIVERY_SECTION.search(text))
        has_retrait_indispo = bool(_KW_RETRAIT_INDISPO.search(text))

        log(f"Boulanger: cart={has_cart} section={has_section} "
            f"retrait_indispo={has_retrait_indispo}")

        # 1) Bouton "Ajouter au panier" présent => dispo (signal le plus fort)
        if has_cart:
            return self._mk(label, ref, IN_STOCK,
                            "disponible en ligne (livraison/retrait)", url)

        # 2) Section livraison chargée + retrait/livraison indisponible => rupture
        if has_section and has_retrait_indispo:
            return self._mk(label, ref, OUT_OF_STOCK,
                            "retrait & livraison indisponibles", url)

        # 3) Section chargée mais pas de panier ni d'indispo explicite :
        #    on lit l'indisponibilité générique en filet
        if has_section and _KW_GENERIC_INDISPO.search(text):
            return self._mk(label, ref, OUT_OF_STOCK,
                            "indisponible en ligne", url)

        # 4) Rien d'exploitable
        return self._mk(label, ref, UNKNOWN, "état indéterminé", url)

    @staticmethod
    def _mk(label, ref, status, detail, url):
        return Availability(
            retailer="Boulanger", product_label=label, product_ref=ref,
            store_key="en-ligne", store_name="En ligne (livraison/retrait)",
            store_city="—", status=status, detail=detail, url=url,
        )
