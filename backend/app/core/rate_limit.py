"""Limitation de débit à fenêtre glissante, en mémoire.

Protège la connexion et l'inscription contre le bourrage d'identifiants. L'implémentation est
volontairement locale au processus : elle suffit pour un déploiement mono-instance et ne fait
peser aucune dépendance sur Redis avant P9. Le passage à un compteur Redis partagé — nécessaire
dès qu'il y a plusieurs workers — est inscrit au ticket P12.

**Deux seaux, pas un seul.** Un unique seau par IP remis à zéro à chaque succès est entièrement
contournable : il suffit d'intercaler une connexion réussie sur son propre compte tous les N
essais pour attaquer indéfiniment le compte d'autrui. D'où la séparation :

- un seau **par cible** (l'adresse e-mail visée), remis à zéro quand *cette* cible s'authentifie ;
- un seau **par IP**, jamais remis à zéro, qui plafonne le volume total quelle que soit la cible.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class SlidingWindowRateLimiter:
    """Autorise au plus `max_attempts` évènements par clé sur `window_seconds`."""

    max_attempts: int
    window_seconds: float
    _events: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def hit(self, key: str, now: float) -> bool:
        """Enregistre une tentative. Retourne `False` si le quota est dépassé.

        `now` est injecté plutôt que lu depuis l'horloge interne : c'est ce qui rend le
        comportement testable sans `sleep`.
        """
        events = self._events[key]
        horizon = now - self.window_seconds
        while events and events[0] <= horizon:
            events.popleft()

        if not events:
            # Une clé vidée est retirée : sinon le dictionnaire grossit d'une entrée par IP vue,
            # indéfiniment et sans plafond — sur un endpoint public, c'est un levier de
            # saturation mémoire offert à qui n'a même pas de compte.
            self._events.pop(key, None)

        if len(events) >= self.max_attempts:
            return False

        self._events[key].append(now)
        return True

    def reset(self, key: str) -> None:
        """Remet à zéro le compteur d'**une** clé."""
        self._events.pop(key, None)

    def clear(self) -> None:
        self._events.clear()

    @property
    def tracked_keys(self) -> int:
        """Nombre de clés encore suivies (sert à vérifier l'absence de fuite mémoire)."""
        return len(self._events)


@dataclass
class LoginRateLimiter:
    """Composition des deux seaux décrits dans le docstring du module."""

    per_target: SlidingWindowRateLimiter
    per_client: SlidingWindowRateLimiter

    def allow(self, client_key: str, target_key: str, now: float) -> bool:
        """Vrai si la tentative est autorisée. Les deux seaux sont incrémentés.

        Les deux `hit` sont évalués sans court-circuit : sauter l'incrément du second seau parce
        que le premier a déjà refusé laisserait une fenêtre d'attaque en alternant les cibles.
        """
        target_ok = self.per_target.hit(f"{client_key}|{target_key}", now)
        client_ok = self.per_client.hit(client_key, now)
        return target_ok and client_ok

    def reset_target(self, client_key: str, target_key: str) -> None:
        """Après une authentification réussie, seul le seau de *cette* cible est libéré.

        Le seau par IP reste intact : c'est lui qui empêche d'utiliser un succès sur son propre
        compte comme jeton de remise à zéro pour attaquer d'autres comptes.
        """
        self.per_target.reset(f"{client_key}|{target_key}")

    def clear(self) -> None:
        self.per_target.clear()
        self.per_client.clear()


def build_login_rate_limiter() -> LoginRateLimiter:
    return LoginRateLimiter(
        per_target=SlidingWindowRateLimiter(max_attempts=10, window_seconds=300),
        per_client=SlidingWindowRateLimiter(max_attempts=60, window_seconds=300),
    )
