"""Modèle de données partagé par tous les checkers."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone


# Statuts normalisés renvoyés par les checkers
IN_STOCK = "in_stock"        # dispo (retrait magasin ou livraison) -> on notifie
OUT_OF_STOCK = "out_of_stock"  # indisponible
UNKNOWN = "unknown"          # impossible à déterminer (page bloquée, sélecteur KO...)


@dataclass
class Availability:
    """Résultat d'une vérification pour 1 produit dans 1 point de vente / canal."""
    retailer: str            # "Castorama", "Boulanger"...
    product_label: str       # libellé court du produit
    product_ref: str         # ref/EAN servant de clé produit
    store_key: str           # identifiant stable du point de vente ("en-ligne", "lille-v2"...)
    store_name: str          # nom lisible ("Lille Villeneuve d'Ascq", "En ligne (livraison)")
    store_city: str          # ville (pour tri / affichage)
    status: str              # IN_STOCK / OUT_OF_STOCK / UNKNOWN
    detail: str = ""         # texte brut lu sur le site ("En stock", "Sous 48h"...)
    url: str = ""            # lien direct vers la fiche / la dispo
    distance_km: float | None = None  # distance depuis "home" (vol d'oiseau)
    lat: float | None = None  # coordonnées du point de vente (pour la carte)
    lon: float | None = None
    quantity: int | None = None  # quantité en stock (Castorama) si connue
    restock: bool = False  # transitoire : True si la notif correspond à une hausse (réassort)
    delta: int | None = None  # transitoire : incrément de stock détecté (réassort)
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def key(self) -> str:
        """Clé unique (retailer + produit + point de vente)."""
        return f"{self.retailer}|{self.product_ref}|{self.store_key}"

    @property
    def is_available(self) -> bool:
        return self.status == IN_STOCK

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Availability":
        return cls(**d)
