"""Application Celery (`docs/spec-complete.md` §6 et §8, cas 2).

Rappel de l'arbitrage §8 : « Construire en synchrone (P6), migrer vers Celery (P9) **en mesurant
le gain avant/après** ». La bascule est donc explicite et réversible : l'API expose les deux
chemins, et `tests/test_export_api.py` mesure la latence perçue dans les deux cas.

`task_always_eager` permet aux tests de tourner sans broker : la tâche s'exécute alors dans le
processus appelant, ce qui vérifie la logique de la tâche mais pas le transport.
"""

from celery import Celery

from app.core.config import get_settings


def build_celery_app() -> Celery:
    settings = get_settings()
    # En mode immédiat, le broker et le backend de résultats sont en mémoire : la suite de tests
    # tourne ainsi sans Redis, sans que le code applicatif ait à connaître ce cas.
    broker = "memory://" if settings.celery_eager else settings.redis_url
    backend = "cache+memory://" if settings.celery_eager else settings.redis_url

    app = Celery("renovation", broker=broker, backend=backend, include=["app.tasks.exports"])
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Un export perdu vaut mieux qu'un export rejoué indéfiniment : la tâche écrit un
        # fichier, elle n'est pas idempotente au sens strict.
        task_acks_late=False,
        task_time_limit=300,
        task_soft_time_limit=240,
        result_expires=86_400,
        # Les tests basculent ce drapeau : la tâche s'exécute alors sur place, sans broker.
        task_always_eager=settings.celery_eager,
        task_eager_propagates=True,
        # Sans ça, un résultat produit en mode immédiat n'est jamais stocké et sa relecture
        # tenterait quand même de joindre le backend réel.
        task_store_eager_result=settings.celery_eager,
    )
    return app


celery_app = build_celery_app()
