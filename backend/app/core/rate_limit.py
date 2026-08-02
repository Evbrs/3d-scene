"""Limitation de débit à fenêtre glissante, en mémoire.

Sert à protéger la connexion contre le bourrage d'identifiants. L'implémentation est
volontairement locale au processus : elle suffit pour un déploiement mono-instance et ne fait
peser aucune dépendance sur Redis avant P9. Le passage à un compteur Redis partagé — nécessaire
dès qu'il y a plusieurs workers — est explicitement inscrit au ticket P12.
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

        if len(events) >= self.max_attempts:
            return False

        events.append(now)
        return True

    def reset(self, key: str) -> None:
        """Remet le compteur à zéro (appelé après une authentification réussie)."""
        self._events.pop(key, None)

    def clear(self) -> None:
        self._events.clear()
