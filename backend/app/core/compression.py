"""Compression GZip restreinte à une liste blanche de types de contenu.

`GZipMiddleware` de Starlette compresse **tout** sauf `text/event-stream`. Appliqué tel quel à ce
service, il recompresserait les PDF et les archives d'export — c'est-à-dire les plus grosses
réponses émises — pour un gain nul (ces formats sont déjà compressés) et un coût processeur
proportionnel à leur taille, payé dans la boucle d'évènements. La liste blanche inverse la
logique : on ne compresse que ce dont on sait que ça se comprime.

La compression n'est appliquée qu'aux réponses complètes. Une réponse en flux est relayée telle
quelle : la mettre en tampon pour la compresser annulerait la raison même de la diffuser en flux,
et ferait porter au processus la taille entière du contenu.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Types réellement compressibles produits par ce service : JSON de l'API, schéma OpenAPI, et les
# gabarits et feuilles de style du back-office. Volontairement pas `application/pdf`,
# `application/zip` ni `image/*`.
COMPRESSIBLE_TYPES = frozenset(
    {
        "application/javascript",
        "application/json",
        "application/manifest+json",
        "application/problem+json",
        "application/xml",
        "image/svg+xml",
        "text/css",
        "text/csv",
        "text/html",
        "text/javascript",
        "text/plain",
        "text/xml",
    }
)


def is_compressible(content_type: str) -> bool:
    """Vrai si le type de média — paramètres exclus — figure dans la liste blanche."""
    return content_type.split(";")[0].strip().lower() in COMPRESSIBLE_TYPES


class SelectiveGZipMiddleware:
    """Compresse les réponses complètes dont le type figure dans la liste blanche."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        minimum_size: int = 500,
        compress_level: int = 6,
        compressible_types: Iterable[str] = COMPRESSIBLE_TYPES,
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        # 6 et non 9 : au-delà, le gain de taille se compte en pour-cent et le coût processeur
        # double, dans un thread qui est celui de la boucle d'évènements.
        self.compress_level = compress_level
        self.compressible_types = frozenset(t.lower() for t in compressible_types)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._accepts_gzip(scope):
            await self.app(scope, receive, send)
            return

        start_message: Message | None = None
        forwarded = False

        async def send_maybe_compressed(message: Message) -> None:
            nonlocal start_message, forwarded

            if forwarded:
                await send(message)
                return

            if message["type"] == "http.response.start":
                start_message = message
                return

            if message["type"] != "http.response.body" or start_message is None:
                # Extension ASGI inconnue (`http.response.pathsend`, par exemple) : on relaie
                # sans y toucher, mais après avoir libéré le `start` mis en attente — l'émettre
                # ensuite produirait une réponse dont l'en-tête arrive après le corps.
                forwarded = True
                if start_message is not None:
                    await send(start_message)
                await send(message)
                return

            body = message.get("body", b"")
            if message.get("more_body", False) or not self._should_compress(start_message, body):
                forwarded = True
                await send(start_message)
                await send(message)
                return

            packed = gzip.compress(body, compresslevel=self.compress_level)
            forwarded = True
            await send(self._with_encoding(start_message, len(packed)))
            await send({"type": "http.response.body", "body": packed})

        await self.app(scope, receive, send_maybe_compressed)

    @staticmethod
    def _accepts_gzip(scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() == b"accept-encoding" and b"gzip" in value.lower():
                return True
        return False

    def _should_compress(self, start_message: Message, body: bytes) -> bool:
        if len(body) < self.minimum_size:
            return False
        content_type = ""
        for name, value in start_message.get("headers", []):
            lowered = name.lower()
            if lowered == b"content-encoding":
                # Déjà encodée par l'application : la recompresser produirait un corps illisible.
                return False
            if lowered == b"content-type":
                content_type = value.decode("latin-1")
        return content_type.split(";")[0].strip().lower() in self.compressible_types

    @staticmethod
    def _with_encoding(start_message: Message, length: int) -> Message:
        dropped = (b"content-length", b"content-encoding", b"vary")
        headers = [
            (name, value)
            for name, value in start_message.get("headers", [])
            if name.lower() not in dropped
        ]
        previous_vary = [
            value.decode("latin-1")
            for name, value in start_message.get("headers", [])
            if name.lower() == b"vary"
        ]
        # Sans `Vary: Accept-Encoding`, un intermédiaire partagé servirait la version compressée à
        # un client qui n'a pas annoncé savoir la lire.
        criteria = [part.strip() for value in previous_vary for part in value.split(",")]
        if not any(part.lower() == "accept-encoding" for part in criteria):
            criteria.append("Accept-Encoding")

        headers.append((b"content-encoding", b"gzip"))
        headers.append((b"content-length", str(length).encode()))
        headers.append((b"vary", ", ".join(filter(None, criteria)).encode("latin-1")))
        return {**start_message, "headers": headers}
