"""Checker Boulanger — attente JS + détection par texte (juin 2026).

La page Boulanger est SSR avec "Ajouter au panier" en placeholder,
puis JS injecte l'état réel de dispo dans la section livraison.
On attend que cette section soit JS-peuplée avant de lire le texte.
"""
from __future__ import annotations
import re
from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, accept_cookies, log

# Section dispo chargée dynamiquement par JS
SEL_DELIVERY = ".product-delivery, .product__delivery-options, [class*='delivery']"
# Textes JS injectés signalant la disponibilité réelle
_KW_BUYABLE  = re.compile(r"ajouter au panier", re.IGNORECASE)
_KW_UNAVAIL  = re.compile(
    r"retrait\s+indisponible|livraison.*indisponible|rupture\s+de\s+stock"
    r"|produit\s+indisponible|non\s+disponible|en\s+rupture"
    r"|temporairement\s+indisponible",
    re.IGNORECASE,
)
# Phrase SSR à ignorer (placeholder statique avant que JS tourne)
_SSR_PLACEHOLDER = re.compile(
    r"^\s*ajouter au panier\s*•\s*\d", re.IGNORECASE | re.MULTILINE
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
        ref   = str(product["ref"])
        label = product.get("label", ref)
        url   = product.get("url", f"https://www.boulanger.com/ref/{ref}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            accept_cookies(page)
        except Exception as e:
            log(f"Boulanger: chargement KO {url}: {e}")
            return self._mk(label, ref, UNKNOWN, "page inaccessible", url)

        # Attendre que JS ait injecté la section livraison (max 12 s)
        try:
            page.wait_for_selector(SEL_DELIVERY, timeout=12000)
            page.wait_for_timeout(1500)   # temps supplémentaire pour les updates JS
        except Exception:
            # Section absente après 12 s → probablement DataDome ou structure inconnue
            log("Boulanger: section livraison non trouvée après 12 s")
            return self._mk(label, ref, UNKNOWN, "section dispo introuvable", url)

        # Lire le texte de la section livraison uniquement (pas du SSR entier)
        text = self._delivery_text(page)
        if not text:
            log("Boulanger: texte livraison vide")
            return self._mk(label, ref, UNKNOWN, "texte livraison vide", url)

        has_cart    = bool(_KW_BUYABLE.search(text))
        has_unavail = bool(_KW_UNAVAIL.search(text))

        log(f"Boulanger: has_cart={has_cart} has_unavail={has_unavail} "
            f"(section livraison {len(text)} chars)")

        if has_cart and not has_unavail:
            return self._mk(label, ref, IN_STOCK,
                            "disponible en ligne (livraison/retrait)", url)
        if has_unavail:
            return self._mk(label, ref, OUT_OF_STOCK,
                            "indisponible en ligne", url)

        return self._mk(label, ref, UNKNOWN, "état indéterminé", url)

    @staticmethod
    def _delivery_text(page) -> str:
        """Texte de la section livraison JS-peuplée (pas le SSR global)."""
        for sel in [".product-delivery", ".product__delivery-options",
                    "[class*='delivery-option']", "[class*='product-delivery']"]:
            try:
                el = page.locator(sel).first
                if el.count():
                    txt = el.inner_text(timeout=3000)
                    if txt and len(txt) > 10:
                        return txt.strip()
            except Exception:
                continue
        # Fallback : tout le main, mais en filtrant le placeholder SSR
        try:
            txt = page.locator("#main, main").first.inner_text(timeout=4000)
            # Retire la ligne SSR "Ajouter au panier • 999€" du début
            txt = _SSR_PLACEHOLDER.sub("", txt)
            return txt.strip()
        except Exception:
            return ""

    @staticmethod
    def _mk(label, ref, status, detail, url):
        return Availability(
            retailer="Boulanger", product_label=label, product_ref=ref,
            store_key="en-ligne", store_name="En ligne (livraison/retrait)",
            store_city="—", status=status, detail=detail, url=url,
        )