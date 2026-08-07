"""Borne sur la taille des corps de requête.

Aucun corps n'était borné : un seul client pouvait donc faire allouer au processus autant de
mémoire qu'il envoyait d'octets, sur n'importe quelle route qui lit un JSON. nginx impose déjà une
borne en bordure, mais le backend est aussi joignable sans passer par elle (worker, sonde,
déploiement où la bordure est fournie par l'hébergeur) : la borne est donc reposée ici.

Le cas qui compte est le second. Refuser sur le seul `Content-Length` laisse passer
`Transfer-Encoding: chunked`, qui n'en a pas — et c'est justement la forme qu'emploie un client
qui cherche à saturer un serveur. Le middleware compte donc aussi les octets réellement lus.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOO_LARGE_BODY = json.dumps(
    {"detail": "Corps de requête trop volumineux"}, ensure_ascii=False
).encode("utf-8")


class _BodyTooLarge(Exception):
    """Signal interne : le corps a dépassé la borne pendant sa lecture."""


class BodySizeLimitMiddleware:
    """Refuse en 413 tout corps dépassant `max_bytes`, annoncé ou non."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._announced_length(scope) > self.max_bytes:
            # Annoncé trop gros : refusé sans lire un seul octet, ce qui est tout l'intérêt de
            # regarder l'en-tête avant le corps.
            await self._reject(send)
            return

        read = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal read
            message = await receive()
            if message["type"] == "http.request":
                read += len(message.get("body", b""))
                if read > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def watching_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, watching_send)
        except _BodyTooLarge:
            if response_started:
                # L'application a déjà commencé à répondre : on ne peut plus rien émettre de
                # cohérent, et masquer l'incident produirait une réponse tronquée sans trace.
                raise
            await self._reject(send)

    def _announced_length(self, scope: Scope) -> int:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return 0
        return 0

    async def _reject(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(TOO_LARGE_BODY)).encode()),
                    # Le client doit refermer : la fin du corps refusé est encore en vol, et la
                    # laisser sur une connexion réutilisée décalerait la requête suivante.
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": TOO_LARGE_BODY})
