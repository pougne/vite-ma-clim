"""Checker Optimea — vendeur officiel du PortaSplit (optimea.fr).

Boutique WooCommerce : le statut de stock est exposé DIRECTEMENT dans le HTML
via la balise meta `product:availability` (« in stock » / « out of stock »).
On charge la page dans un VRAI onglet (page.goto) : l'ancien appel
context.request.get était bloqué (pare-feu / anti-bot, réponses vides), alors
qu'une navigation complète passe. On lit ensuite la meta dans le HTML rendu.
"""
from __future__ import annotations

import re

from models import Availability, IN_STOCK, OUT_OF_STOCK, UNKNOWN
from .base import Checker, log


def _meta(html: str, prop: str) -> str | None:
    """Renvoie le content de la 1re balise <meta> contenant `prop`."""
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        tag = m.group(0)
        if prop.lower() in tag.lower():
            cm = re.search(r"content\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
            if cm:
                return cm.group(1).strip()
    return None


def _norm(s: str | None) -> str:
    return re.sub(r"[\s_]+", "", (s or "").lower())


class OptimeaChecker(Checker):
    name = "Optimea"

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
        url = product["url"]
        label = product.get("label", "Optimea")
        ref = str(product.get("ref", "optimea"))
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if resp is not None and resp.status >= 400:
                log(f"Optimea: HTTP {resp.status}")
                return self._mk(label, ref, UNKNOWN, f"HTTP {resp.status}", url)
            page.wait_for_timeout(1500)
            html = page.content()
        except Exception as e:
            log(f"Optimea: chargement KO: {e}")
            return self._mk(label, ref, UNKNOWN, "page inaccessible", url)
        return self.parse_html(html, label, ref, url)

    def parse_html(self, html: str, label: str, ref: str, url: str) -> Availability:
        """Analyse pure du HTML (testable sans navigateur)."""

        avail = _meta(html, "product:availability") or _meta(html, "og:availability")
        price = _meta(html, "product:price:amount")
        detail_bits = []
        if price:
            detail_bits.append(f"{price} €")
        n = _norm(avail)
        if n in ("instock", "available", "available forsale".replace(" ", "")):
            detail_bits.append("en stock")
            status = IN_STOCK
        elif n in ("outofstock", "soldout", "outofstockpreorder"):
            detail_bits.append("rupture")
            status = OUT_OF_STOCK
        else:
            detail_bits.append(f"statut '{avail}'" if avail else "statut introuvable")
            status = UNKNOWN
            log(f"Optimea: availability non reconnue: {avail!r}")
        return self._mk(label, ref, status, " · ".join(detail_bits), url)

    @staticmethod
    def _mk(label, ref, status, detail, url) -> Availability:
        return Availability(
            retailer="Optimea", product_label=label, product_ref=ref,
            store_key="en-ligne", store_name="En ligne (livraison)",
            store_city="—", status=status, detail=detail, url=url,
            distance_km=None, lat=None, lon=None,
        )
