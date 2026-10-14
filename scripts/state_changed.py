#!/usr/bin/env python3
"""Sort 0 si state.json a changé de façon UTILE par rapport à HEAD, 1 sinon.

On ignore les champs qui bougent à chaque passage sans valeur (horodatage
`checked_at`, compteurs de passages muets `streak`, `last_ok_ts`), pour ne
committer l'état que quand il y a vraiment du nouveau.
"""
import json
import subprocess
import sys

IGNORED = {"checked_at", "streak", "last_ok_ts"}


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in IGNORED}
    if isinstance(o, list):
        return [strip(v) for v in o]
    return o


def main(path="state.json") -> int:
    try:
        old = json.loads(subprocess.run(["git", "show", f"HEAD:{path}"], check=True,
                                        capture_output=True, text=True).stdout)
    except Exception:
        return 0  # pas d'état versionné -> à committer
    with open(path, encoding="utf-8") as f:
        new = json.load(f)
    return 0 if strip(old) != strip(new) else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
