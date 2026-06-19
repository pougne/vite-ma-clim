"""Checker Boulanger — version calibrée sur la vraie page (juin 2026).

Constat sur la fiche PortaSplit /ref/1216685 :
  - Le bloc de dispo porte data-product-no-buyable="true" + classe
    product-delivery--unbuyable quand le produit est en rupture
    ("Retrait indisponible" / "Livraison à domicile indisponible").
  - Il n'y a PAS de module "disponibilité magasin par magasin" pour ce
    produit (juste un lien générique "Trouver votre magasin"). On suit donc
    un seul état : achetable en ligne (livraison/retrait) ou non.
  - Attention : la page contient ~35 boutons "Ajouter au panier" (produits
    sponsorisés). On cible donc UNIQUEMENT le bouton produit principal.
"""
from __future__ import annotations

import re

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, accept_cookies, log

# Marqueur fiable de rupture (présent => non achetable).
SEL_UNBUYABLE = "[data-product-no-buyable='true'], .product-delivery--unbuyable"
# Bloc de dispo, pour lire le texte affiché.
SEL_DELIVERY_BLOCK = ".product__delivery-options, .product-delivery"
# Bouton panier DU PRODUIT (pas les sponsorisés Kamino).
SEL_MAIN_ADD_TO_CART = ".card-service__btn-add-to-cart, .js-add-to-cart-services"


class BoulangerChecker(Checker):
    name = "Boulanger"

    def check(self, context, products, zones):
        # zones ignorées : pas de dispo magasin par magasin pour ce produit.
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
            page.wait_for_timeout(1800)
        except Exception as e:
            log(f"Boulanger: chargement KO {url}: {e}")
            return self._mk(label, ref, UNKNOWN, "page inaccessible", url)

        # 1) Rupture explicite ?
        try:
            if page.locator(SEL_UNBUYABLE).count() > 0:
                return self._mk(label, ref, OUT_OF_STOCK,
                                self._block_text(page) or "non achetable", url)
        except Exception:
            pass

        # 2) Sinon, bouton panier produit présent et visible => achetable.
        try:
            add = page.locator(SEL_MAIN_ADD_TO_CART).first
            if add.count() and add.is_visible(timeout=2500):
                return self._mk(label, ref, IN_STOCK,
                                self._block_text(page) or "achetable en ligne", url)
        except Exception:
            pass

        return self._mk(label, ref, UNKNOWN, "état indéterminé", url)

    @staticmethod
    def _block_text(page) -> str:
        """Texte court du bloc dispo (ex : 'Retrait indisponible ...')."""
        try:
            txt = page.locator(SEL_DELIVERY_BLOCK).first.inner_text(timeout=1500)
            txt = re.sub(r"\s+", " ", txt).strip()
            return txt[:90]
        except Exception:
            return ""

    @staticmethod
    def _mk(label, ref, status, detail, url):
        return Availability(
            retailer="Boulanger", product_label=label, product_ref=ref,
            store_key="en-ligne", store_name="En ligne (livraison/retrait)",
            store_city="—", status=status, detail=detail, url=url,
        )
