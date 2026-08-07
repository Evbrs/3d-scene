"""Journalisation structurée et corrélation des requêtes.

Avant ce module, `grep -rn 'logging\\|logger\\|getLogger' app/` ne remontait qu'un `print()` dans
`cli.py` : le service ne disait rien de ce qu'il faisait, et le gestionnaire d'erreur non gérée
avalait la trace avant de renvoyer sa réponse muette. Un incident de production était donc
irreconstituable.

Trois choix structurants :

- **JSON sur stdout**, une ligne par évènement. C'est la seule forme qu'un collecteur indexe sans
  expression régulière, et stdout la seule destination qu'un conteneur n'a pas à monter.
- **Un identifiant de requête porté par un `ContextVar`**, donc automatiquement présent sur tout
  ce qui est journalisé pendant le traitement, y compris depuis du code qui ne connaît pas la
  requête (couche géométrie, tâches). Il est renvoyé au client dans `X-Request-Id` : un
  utilisateur qui signale un problème donne un identifiant qui retrouve la trace exacte.
- **Aucune adresse IP complète**. La ligne RGPD du projet impose des journaux anonymisés ; les
  adresses sont donc tronquées à leur réseau (/24 en IPv4, /48 en IPv6), ce qui reste suffisant
  pour repérer une attaque distribuée sans identifier une personne.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings

# Journal des évènements de sécurité, séparé du journal d'accès : une politique de rétention ou
# une alerte se pose sur un nom de logger, pas sur une sous-chaîne de message.
SECURITY_LOGGER = "app.security"
ACCESS_LOGGER = "app.access"

REQUEST_ID_HEADER = "X-Request-Id"

# Codes de statut qui, quelle que soit la route, décrivent un refus : ce sont eux qu'on veut
# corréler pour repérer un bourrage d'identifiants ou un balayage de ressources.
REFUSAL_EVENTS = {
    401: "auth.refused",
    403: "access.denied",
    429: "rate_limit.exceeded",
}
# Un identifiant fourni par le client est repris pour corréler avec l'amont, mais borné : il finit
# dans les journaux, où une valeur de plusieurs kilo-octets serait une injection de volume.
MAX_INBOUND_REQUEST_ID = 64

_request_id: ContextVar[str] = ContextVar("request_id", default="")

# Attributs posés par `logging` lui-même : tout le reste d'un `LogRecord` est un champ métier
# ajouté par l'appelant via `extra=`, et part donc dans la charge JSON.
_STANDARD_RECORD_FIELDS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


def current_request_id() -> str:
    return _request_id.get()


def anonymized_ip(host: str | None) -> str:
    """Adresse tronquée à son réseau, seule forme autorisée dans les journaux.

    Une adresse illisible (en-tête forgé, socket unix) n'est pas journalisée telle quelle : elle
    serait une porte d'entrée pour injecter du texte arbitraire dans le journal.
    """
    if not host:
        return "inconnu"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "invalide"
    prefix = 24 if address.version == 4 else 48
    return str(ipaddress.ip_network(f"{host}/{prefix}", strict=False))


class JsonFormatter(logging.Formatter):
    """Une ligne JSON par évènement, identifiant de requête inclus d'office."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = current_request_id()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # `default=str` plutôt qu'un échec : un champ non sérialisable ne doit pas faire perdre
        # l'évènement entier, qui est justement celui qu'on cherchera pendant l'incident.
        return json.dumps(payload, ensure_ascii=False, default=str)


_installed_handler: logging.Handler | None = None


def configure_logging(settings: Settings) -> None:
    """Installe le format sur la racine, et n'y touche rien d'autre.

    Seul le gestionnaire posé par un appel précédent est retiré : la racine peut porter des
    gestionnaires qui ne nous appartiennent pas (harnais de test, agent d'observabilité), et les
    supprimer en bloc les ferait disparaître sans le dire.

    Uvicorn, lui, installe ses propres gestionnaires sur ses loggers : les retirer est ce qui
    évite d'avoir chaque ligne en double, une fois au format uvicorn et une fois au nôtre.
    """
    global _installed_handler

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if settings.log_json
        else logging.Formatter("%(levelname)s %(name)s %(message)s")
    )

    root = logging.getLogger()
    if _installed_handler is not None:
        root.removeHandler(_installed_handler)
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    _installed_handler = handler

    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers.clear()
        logger.propagate = True

    # `logging.config.fileConfig` — celui qu'appelle l'`env.py` d'Alembic — pose
    # `disabled = True` sur tous les loggers qu'il ne connaît pas, dont les nôtres. Un processus
    # qui joue une migration avant de servir du trafic deviendrait muet sans le moindre message
    # d'erreur pour le signaler.
    for name, logger_or_placeholder in logging.root.manager.loggerDict.items():
        if name.startswith("app") and isinstance(logger_or_placeholder, logging.Logger):
            logger_or_placeholder.disabled = False


def log_security_event(event: str, *, client_host: str | None = None, **fields: Any) -> None:
    """Journalise un évènement de sécurité, adresse tronquée.

    Passer par cette fonction plutôt que par un `logger.warning` direct est ce qui garantit que
    personne n'écrit une adresse complète dans un journal par inadvertance.
    """
    logging.getLogger(SECURITY_LOGGER).warning(
        event, extra={"event": event, "client": anonymized_ip(client_host), **fields}
    )


class RequestContextMiddleware:
    """Attribue un identifiant à chaque requête, le renvoie, et journalise l'accès.

    Placé **sous** le middleware qui rétablit l'adresse réelle du visiteur : sans cet ordre, tous
    les accès seraient journalisés avec l'adresse du relais.

    C'est aussi ici que les refus deviennent des évènements de sécurité, et non dans chaque route.
    Un appel posé route par route se périme au premier endpoint ajouté par quelqu'un qui ne
    connaît pas la convention ; observer le code de statut ne peut pas s'oublier.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._inbound_request_id(scope) or uuid.uuid4().hex
        token = _request_id.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = message.setdefault("headers", [])
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            client = scope.get("client")
            client_host = client[0] if client else None
            method = str(scope.get("method", ""))
            # Le chemin seul, jamais la chaîne de requête : elle peut porter un jeton de partage,
            # dont la présence dans un journal annulerait le caractère secret.
            path = str(scope.get("path", ""))

            logging.getLogger(ACCESS_LOGGER).info(
                "requête traitée",
                extra={
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client": anonymized_ip(client_host),
                },
            )
            event = REFUSAL_EVENTS.get(status_code)
            if event is not None:
                log_security_event(event, client_host=client_host, method=method, path=path)
            _request_id.reset(token)

    @staticmethod
    def _inbound_request_id(scope: Scope) -> str:
        wanted = REQUEST_ID_HEADER.lower().encode()
        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        for name, value in headers:
            if name.lower() == wanted:
                candidate = value.decode("latin-1")[:MAX_INBOUND_REQUEST_ID].strip()
                # Jeu de caractères restreint : un identifiant est recopié dans un en-tête de
                # réponse et dans les journaux, deux endroits où une valeur libre s'injecte.
                if candidate and all(c.isalnum() or c in "-_" for c in candidate):
                    return candidate
        return ""
