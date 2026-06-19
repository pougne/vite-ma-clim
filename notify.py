"""Envoi des notifications : email (SMTP) et/ou ntfy (push mobile)."""
from __future__ import annotations

import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

from models import Availability


def _format_lines(results: list[Availability]) -> str:
    lines = []
    for r in sorted(results, key=lambda x: (x.retailer, x.store_city, x.store_name)):
        lines.append(f"• {r.retailer} — {r.store_name} ({r.store_city or '—'}) : "
                     f"{r.detail or 'dispo'}\n  {r.url}")
    return "\n".join(lines)


def notify_email(cfg: dict, results: list[Availability]) -> None:
    if not cfg.get("enabled"):
        return
    body = (
        "Nouvelle(s) disponibilité(s) détectée(s) pour le Midea PortaSplit :\n\n"
        + _format_lines(results)
        + "\n\n— Vite Ma Clim"
    )
    msg = EmailMessage()
    msg["Subject"] = f"[Vite Ma Clim] {len(results)} dispo(s) PortaSplit !"
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(cfg["to_addrs"])
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if cfg.get("use_ssl", True):
        with smtplib.SMTP_SSL(cfg["host"], cfg.get("port", 465), context=ctx) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg.get("port", 587)) as s:
            s.starttls(context=ctx)
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)


def notify_ntfy(cfg: dict, results: list[Availability]) -> None:
    if not cfg.get("enabled"):
        return
    body = _format_lines(results).encode("utf-8")
    topic_url = cfg["topic_url"].rstrip("/")
    req = urllib.request.Request(
        topic_url,
        data=body,
        headers={
            "Title": f"Vite Ma Clim · {len(results)} dispo(s) PortaSplit",
            "Priority": "high",
            "Tags": "snowflake",
            # Clique sur la notif -> ouvre la 1re fiche produit.
            "Click": results[0].url if results else topic_url,
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15)


def dispatch(notif_cfg: dict, results: list[Availability]) -> None:
    """Envoie via tous les canaux activés. Ne lève pas : log seulement."""
    if not results:
        return
    for name, fn, key in (
        ("email", notify_email, "email"),
        ("ntfy", notify_ntfy, "ntfy"),
    ):
        sub = notif_cfg.get(key, {})
        if not sub.get("enabled"):
            continue
        try:
            fn(sub, results)
            print(f"[notify] {name}: OK ({len(results)} dispo)")
        except Exception as e:
            print(f"[notify] {name}: ECHEC -> {e}")
