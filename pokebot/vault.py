"""Cifratura di ciò che il bot salva nei rami pubblici del repository (stato, database, coda dei comandi).

AES-GCM con una chiave ricavata dal token del bot: GitHub Actions e il ponte Cloudflare lo conoscono già, quindi non
serve nessun segreto in più. Formato: b"PKB1" + nonce (12 byte) + testo cifrato con il tag (come WebCrypto).
Se il token del bot cambia, lo stato salvato non si legge più: prima di cambiarlo, vedi il README.
"""
from __future__ import annotations

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config

MAGIC = b"PKB1"


def _key(token: str | None = None) -> bytes:
    token = token if token is not None else config.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN mancante: serve per cifrare e decifrare lo stato")
    return hashlib.sha256(("pokebot-state-v1:" + token).encode()).digest()


def file_id(chat_id: str, token: str | None = None) -> str:
    """Nome del file di stato di una persona: un'impronta del chat id, così nel branch pubblico non compare
    chi usa il bot (il ponte la calcola allo stesso modo)."""
    token = token if token is not None else config.TELEGRAM_BOT_TOKEN
    return hashlib.sha256(f"pokebot-file:{token}:{chat_id}".encode()).hexdigest()[:32]


def seal(data: bytes, token: str | None = None) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(_key(token)).encrypt(nonce, data, None)


def is_sealed(blob: bytes) -> bool:
    return blob[:4] == MAGIC


def unseal(blob: bytes, token: str | None = None) -> bytes:
    """Decifra; un contenuto non cifrato (versioni precedenti) passa così com'è."""
    if not is_sealed(blob):
        return blob
    return AESGCM(_key(token)).decrypt(blob[4:16], blob[16:], None)
