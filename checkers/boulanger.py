"""Checker Boulanger — détection par texte (juin 2026).

Méthode : on scrute le texte visible de la page après JS plutôt que des
sélecteurs CSS fragiles (classes Kamino/Boulanger qui changent souvent).

Signaux utilisés :
  - IN_STOCK  : présence du texte "Ajouter au panier" (bouton principal)
  - OUT_OF_STOCK : présence de termes de rupture explicites
  - UNKNOWN   : aucun signal clair (DataDome, timeout, structure inconnue)
"""
from __future__ import annotations

import re

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, accept_cookies, log

# Mots-clés indiquant une disponibilité (insensible à la casse)
_KW_BUYABLE = re.compile(
    r"ajouter au panier",
    re.IGNORECASE,
)
# Mots-clés indiquant une rupture explicite
_KW_UNAVAIL = re.compile(
    r"retrait\s+indisponible|livraison.*indisponible|rupture\s+de\s+stock"
    r"|produit\s+indisponible|non\s+disponible|en\s+rupture",
    re.IGNORECASE,
)


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
        ref  = str(product["ref"])
        label = product.get("label", ref)
        url  = product.get("url", f"https://www.boulanger.com/ref/{ref}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            accept_cookies(page)
            page.wait_for_timeout(2000)
        except Exception as e:
            log(f"Boulanger: chargement KO {url}: {e}")
            return self._mk(label, ref, UNKNOWN, "page inaccessible", url)

        # Texte visible de la zone produit (on évite le menu/footer)
        text = self._product_text(page)

        if not text:
            log("Boulanger: impossible d'extraire le texte produit")
            return self._mk(label, ref, UNKNOWN, "texte introuvable", url)

        has_cart   = bool(_KW_BUYABLE.search(text))
        has_unavail = bool(_KW_UNAVAIL.search(text))

        log(f"Boulanger: has_cart={has_cart} has_unavail={has_unavail} "
            f"(texte {len(text)} chars)")

        if has_cart and not has_unavail:
            return self._mk(label, ref, IN_STOCK,
                            "achetable en ligne (livraison/retrait)", url)
        if has_unavail and not has_cart:
            return self._mk(label, ref, OUT_OF_STOCK,
                            "non disponible en ligne", url)
        if has_unavail and has_cart:
            # Cas ambigu : il y a un bouton panier ET une mention d'indispo
            # (ex : retrait indispo mais livraison dispo) → on considère dispo
            return self._mk(label, ref, IN_STOCK,
                            "partiellement disponible en ligne", url)

        return self._mk(label, ref, UNKNOWN, "état indéterminé", url)

    @staticmethod
    def _product_text(page) -> str:
        """Texte de la zone produit principale (hors nav/footer)."""
        # On essaie d'abord le bloc produit, sinon on prend tout le body
        for sel in ("#main", "main", ".product", "body"):
            try:
                el = page.locator(sel).first
                if el.count():
                    return el.inner_text(timeout=3000)
            except Exception:
                continue
        return ""

    @staticmethod
    def _mk(label, ref, status, detail, url):
        return Availability(
            retailer="Boulanger", product_label=label, product_ref=ref,
            store_key="en-ligne", store_name="En ligne (livraison/retrait)",
            store_city="—", status=status, detail=detail, url=url,
        )
