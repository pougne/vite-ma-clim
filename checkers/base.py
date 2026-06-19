"""Base commune aux checkers : contexte navigateur réaliste + utilitaires.

On pilote un vrai Chromium (Playwright) parce que Castorama et Boulanger sont
des SPA protégées (DataDome / Akamai) : un simple requests.get() renvoie du 403
ou une page vide. Un navigateur qui saisit un code postal comme un humain passe.
"""
from __future__ import annotations

import sys
from typing import Iterable

from models import Availability

# UA récent et crédible (à rafraîchir de temps en temps).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def log(msg: str) -> None:
    print(f"[checker] {msg}", file=sys.stderr, flush=True)


def make_browser_context(playwright, headless: bool = True):
    """Crée un navigateur + contexte avec des réglages 'humains'."""
    browser = playwright.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="fr-FR",
        timezone_id="Europe/Paris",
        viewport={"width": 1366, "height": 900},
        geolocation={"latitude": 48.8566, "longitude": 2.3522},
        permissions=["geolocation"],
    )
    # Petit coup de gomme sur le marqueur navigator.webdriver.
    context.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    return browser, context


# Sélecteurs courants de bandeaux de consentement (Didomi / OneTrust / Axeptio...).
COOKIE_SELECTORS = [
    "#didomi-notice-agree-button",
    "button#onetrust-accept-btn-handler",
    "button[aria-label*='Tout accepter' i]",
    "button:has-text('Tout accepter')",
    "button:has-text('Accepter')",
    "button:has-text('J'accepte')",
]


def accept_cookies(page, timeout_ms: int = 4000) -> None:
    """Tente de fermer le bandeau cookies, sans planter si absent."""
    for sel in COOKIE_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=timeout_ms):
                btn.click(timeout=timeout_ms)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


class Checker:
    """Interface : chaque enseigne implémente check()."""

    name: str = "base"

    def __init__(self, config: dict):
        self.config = config

    def check(self, context, products: list[dict], zones: list[dict]) -> Iterable[Availability]:
        """Renvoie une liste d'Availability pour les produits/zones donnés."""
        raise NotImplementedError
