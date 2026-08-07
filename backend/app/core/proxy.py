"""Rétablissement de l'adresse réelle du visiteur derrière un relais.

Contrepartie applicative du `proxy_pass` de nginx. Sans elle, `request.client.host` vaut l'adresse
du conteneur de bordure pour **tout le monde** : les limiteurs de débit ne voient plus qu'un seul
client, et le plafond censé protéger un compte devient un déni de service auto-infligé sur
l'ensemble de la plateforme.

Le point délicat n'est pas de lire `X-Forwarded-For`, c'est de refuser de le lire. Un en-tête
`X-Forwarded-For` est du texte libre envoyé par le client : le prendre en compte sans vérifier
**d'où il arrive** revient à laisser chaque visiteur choisir l'adresse sous laquelle il est
compté. D'où la liste de réseaux de confiance, et le refus du joker au niveau de la
configuration.

Note d'exploitation : uvicorn pose déjà son propre `ProxyHeadersMiddleware` quand on lui passe
`--proxy-headers` (c'est le cas en production, avec `--forwarded-allow-ips` réglé sur le
sous-réseau Docker de la pile). Les deux se superposent sans conflit : quand uvicorn a fait le
travail, `scope["client"]` porte déjà l'adresse du visiteur — laquelle n'appartient évidemment
pas au réseau de confiance — et ce middleware-ci ne fait rien. Il reste la seule protection
lorsque l'application est servie autrement (tests, exécution sans bordure, autre serveur ASGI).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from starlette.types import ASGIApp, Receive, Scope, Send

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network

# Schémas acceptés dans `X-Forwarded-Proto`. Toute autre valeur est ignorée plutôt que recopiée :
# `scope["scheme"]` sert à construire des URL absolues.
_KNOWN_SCHEMES = frozenset({"http", "https"})


def parse_networks(entries: Sequence[str]) -> tuple[_Network, ...]:
    """Traduit la configuration en réseaux. Une adresse nue devient un réseau /32 ou /128."""
    networks: list[_Network] = []
    for entry in entries:
        text = entry.strip()
        if not text:
            continue
        networks.append(ipaddress.ip_network(text, strict=False))
    return tuple(networks)


class ProxyHeadersMiddleware:
    """Réécrit `client` et `scheme` d'après `X-Forwarded-*`, si le pair est un relais listé."""

    def __init__(self, app: ASGIApp, *, trusted_proxies: Sequence[str]) -> None:
        self.app = app
        self.trusted = parse_networks(trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        peer = scope.get("client")
        if peer and self.is_trusted(peer[0]):
            headers = scope.get("headers", [])
            forwarded_for = self._joined(headers, b"x-forwarded-for")
            if forwarded_for:
                real_client = self.real_client(forwarded_for)
                if real_client:
                    scope["client"] = (real_client, 0)

            forwarded_proto = self._joined(headers, b"x-forwarded-proto")
            if forwarded_proto:
                # Le premier élément : c'est le saut le plus proche du client.
                candidate = forwarded_proto.split(",")[0].strip().lower()
                if candidate in _KNOWN_SCHEMES:
                    scope["scheme"] = candidate

        await self.app(scope, receive, send)

    def is_trusted(self, host: str) -> bool:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Un pair sans adresse IP (socket unix, transport de test) n'est jamais de confiance :
            # le défaut sûr est de garder l'adresse telle quelle.
            return False
        return any(address in network for network in self.trusted)

    def real_client(self, forwarded_for: str) -> str | None:
        """Adresse la plus à droite qui n'appartient pas à un relais de confiance.

        Parcourir depuis la droite est la seule lecture sûre : la partie gauche de la chaîne est
        entièrement contrôlée par le client, qui peut y écrire autant de fausses adresses qu'il
        veut. Seule la portion écrite par les relais de confiance, en fin de chaîne, fait foi ; la
        première adresse rencontrée en remontant qui n'est pas un relais est le vrai visiteur.
        """
        for candidate in reversed([part.strip() for part in forwarded_for.split(",")]):
            if not candidate:
                continue
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                # Entrée illisible : la chaîne n'est plus fiable au-delà, on s'arrête.
                return None
            if not self.is_trusted(candidate):
                return candidate
        return None

    @staticmethod
    def _joined(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> str:
        """Concatène les occurrences d'un en-tête, comme le ferait un serveur HTTP conforme."""
        values = [value.decode("latin-1") for key, value in headers if key.lower() == name]
        return ", ".join(values)
