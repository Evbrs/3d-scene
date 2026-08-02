"""Tâches d'export en arrière-plan (`docs/spec-complete.md` §7, phase P9)."""

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.models.base import utcnow
from app.services.export_pdf import render_project_pdf


def export_directory() -> Path:
    directory = Path(get_settings().export_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def export_path(project_id: int, task_id: str) -> Path:
    """Chemin du fichier produit.

    L'identifiant de tâche fait partie du nom : deux exports du même projet ne s'écrasent pas, et
    le nom n'est pas devinable à partir du seul identifiant de projet.
    """
    return export_directory() / f"projet-{project_id}-{task_id}.pdf"


async def _load_project(project_id: int) -> dict[str, Any] | None:
    """Charge le projet sous forme de dictionnaire simple, hors de tout contexte HTTP."""
    from sqlalchemy.orm import selectinload
    from sqlmodel import col, select

    from app.api.scene import project_to_plain_dict
    from app.db import get_session_factory, reset_engine
    from app.models.plan import Face, Project, Room

    # Le worker Celery est un autre processus : son moteur doit être créé chez lui, jamais hérité
    # d'un fork du serveur web — les connexions ne survivent pas au fork.
    reset_engine()
    async with get_session_factory()() as session:
        project = (
            await session.execute(
                select(Project)
                .where(col(Project.id) == project_id)
                .options(
                    selectinload(Project.rooms)  # type: ignore[arg-type]
                    .selectinload(Room.faces)  # type: ignore[arg-type]
                    .selectinload(Face.elements)  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if project is None:
            return None
        payload = project_to_plain_dict(project)
        payload["name"] = project.name
        return payload


def run_blocking[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Exécute une coroutine depuis un contexte synchrone, boucle en cours ou non.

    Un worker Celery n'a pas de boucle d'évènements : `asyncio.run` suffirait. Mais en mode
    `task_always_eager` — celui des tests — la tâche s'exécute *dans* la boucle de l'appelant, et
    `asyncio.run` y échoue. Basculer sur un thread dédié couvre les deux cas avec un seul chemin
    de code, donc sans comportement propre aux tests.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


# `type: ignore` : le décorateur de Celery n'est pas typé, et `celery.*` est déclaré sans stubs
# dans la configuration mypy. L'ignorer ici est plus honnête que de typer la tâche en `Any`.
@celery_app.task(name="exports.project_pdf", bind=True)  # type: ignore[untyped-decorator]
def export_project_pdf(self: Any, project_id: int) -> dict[str, Any]:
    """Génère le PDF d'un projet et l'écrit sur disque.

    Renvoie un descriptif sérialisable, jamais le contenu du PDF : faire transiter des
    mégaoctets par le backend de résultats Redis serait un contresens.
    """
    project = run_blocking(_load_project(project_id))
    if project is None:
        raise ValueError(f"projet {project_id} introuvable")

    content = render_project_pdf(project, utcnow())
    target = export_path(project_id, str(self.request.id or "synchrone"))
    target.write_bytes(content)

    return {
        "project_id": project_id,
        "filename": target.name,
        "size_bytes": len(content),
        "generated_at": utcnow().isoformat(),
    }
