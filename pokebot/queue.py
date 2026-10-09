"""Coda dei comandi su GitHub (branch `bot-queue`, un file per comando).

Il ponte Cloudflare salva ogni comando come file (cifrato, vedi vault.py: il repository è pubblico) prima di svegliare
il bot: così nessun comando va perso anche se GitHub cancella i run in attesa doppi. A ogni giro il bot legge tutti i
file in ordine, li esegue e li cancella.
"""
from __future__ import annotations

import json
import logging
import os

import requests

from . import config, vault

log = logging.getLogger(__name__)

QUEUE_BRANCH = "bot-queue"
QUEUE_DIR = "queue"
API = "https://api.github.com"


class GitHubQueue:
    def __init__(self, token: str | None = None, repo: str | None = None):
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN", "")
        self.repo = repo if repo is not None else os.getenv("GITHUB_REPOSITORY", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.repo)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pokebot"}

    def pending(self) -> list[dict]:
        """File in coda, dal più vecchio: [{path, sha, download_url}]."""
        r = requests.get(f"{API}/repos/{self.repo}/contents/{QUEUE_DIR}", params={"ref": QUEUE_BRANCH},
                         headers=self._headers(), timeout=config.HTTP_TIMEOUT)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        items = [i for i in r.json() if i.get("type") == "file" and i.get("name", "").endswith(".json")]
        return sorted(items, key=lambda i: i["name"])

    def read(self, item: dict) -> tuple[str, dict | None]:
        """("ok", comando) · ("skip", None) se il file è sparito o illeggibile per sempre · ("retry", None) se
        GitHub non risponde adesso (il file resta lì per il prossimo giro)."""
        try:
            r = requests.get(item["download_url"], headers=self._headers(), timeout=config.HTTP_TIMEOUT)
        except requests.RequestException:
            return "retry", None
        if r.status_code == 404:
            return "skip", None
        if r.status_code != 200:
            log.warning("Coda: lettura rimandata (HTTP %s)", r.status_code)
            return "retry", None
        try:
            data = json.loads(vault.unseal(r.content))
        except Exception:  # noqa: BLE001 - JSON rotto o cifrato con un altro token: non si leggerà mai
            log.warning("Coda: comando illeggibile, scartato")
            return "skip", None
        return ("ok", data) if isinstance(data, dict) else ("skip", None)

    def delete(self, item: dict) -> None:
        try:
            r = requests.delete(f"{API}/repos/{self.repo}/contents/{item['path']}", headers=self._headers(),
                                json={"message": "eseguito", "sha": item["sha"], "branch": QUEUE_BRANCH},
                                timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            log.warning("Coda: non cancello un comando (%s)", type(exc).__name__)
            return
        if r.status_code not in (200, 404):
            log.warning("Coda: non cancello un comando (HTTP %s)", r.status_code)

    def drain(self, handle_payload) -> tuple[int, bool]:
        """Esegue tutti i comandi in coda. Restituisce (quanti, se qualcuno chiede una ricerca)."""
        if not self.enabled:
            return 0, False
        try:
            items = self.pending()
        except requests.RequestException as exc:
            log.warning("Coda comandi non raggiungibile: %s", type(exc).__name__)
            return 0, False
        done, want = 0, False
        for item in items:
            status, data = self.read(item)
            if status == "retry":
                break  # l'ordine conta: questo e i successivi al prossimo giro
            if status == "ok" and (data.get("text") or data.get("payment")):
                try:
                    want = bool(handle_payload(data)) or want
                except Exception:  # noqa: BLE001 - un comando che fallisce non deve bloccare la coda per sempre
                    log.exception("Comando in coda fallito: %s", str(data.get("text") or "pagamento").split(" ", 1)[0])
            self.delete(item)
            done += 1
        if done:
            log.info("Coda comandi: eseguiti %d", done)
        return done, want
