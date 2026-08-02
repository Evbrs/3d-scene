"""API d'export (`docs/spec-complete.md` §7 phase P9, §8 cas 2).

Les **deux** chemins sont exposés : synchrone et asynchrone. Ce n'est pas de l'indécision, c'est
l'arbitrage §8 (cas 2) pris au mot — « construire en synchrone, migrer vers Celery en mesurant le
gain avant/après ». Garder le chemin synchrone rend la mesure rejouable à tout moment, et sert de
repli si le broker est indisponible.
"""

import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, SessionDep
from app.api.permissions import get_owned_project
from app.core.celery_app import celery_app
from app.models.base import utcnow
from app.services.export_pdf import render_project_pdf
from app.tasks.exports import _load_project, export_path, export_project_pdf

router = APIRouter(prefix="/api", tags=["export"])


class ExportAccepted(BaseModel):
    """Réponse du chemin asynchrone : la requête HTTP rend la main immédiatement."""

    task_id: str
    status: Literal["queued"] = "queued"
    poll_url: str


class ExportStatus(BaseModel):
    task_id: str
    state: str
    ready: bool
    result: dict[str, Any] | None = None
    error: str | None = None


@router.post(
    "/projects/{project_id}/exports/pdf",
    response_model=ExportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_pdf_export(
    project_id: int, session: SessionDep, current_user: CurrentUser
) -> ExportAccepted:
    """Demande un export PDF en tâche de fond.

    La réponse est immédiate : c'est tout l'intérêt du passage à Celery pour un traitement dont
    la durée croît avec la taille du plan.
    """
    await get_owned_project(session, project_id, current_user)

    task = export_project_pdf.delay(project_id)
    return ExportAccepted(task_id=task.id, poll_url=f"/api/exports/{task.id}")


@router.get("/exports/{task_id}", response_model=ExportStatus)
async def read_export_status(
    task_id: Annotated[str, Path(min_length=8, max_length=64)],
    current_user: CurrentUser,
) -> ExportStatus:
    """État d'un export.

    Le contenu du résultat n'expose aucun chemin absolu : seul le nom de fichier est renvoyé, et
    le téléchargement repasse par une route qui revérifie la propriété du projet.
    """
    result = celery_app.AsyncResult(task_id)
    payload = ExportStatus(task_id=task_id, state=result.state, ready=result.ready())

    if result.ready():
        if result.successful():
            payload.result = result.result
        else:
            # Le détail de l'exception n'est pas renvoyé au client : il peut contenir des
            # informations internes (chemins, requêtes SQL).
            payload.error = "La génération a échoué."
    return payload


@router.get("/projects/{project_id}/exports/{filename}")
async def download_export(
    project_id: int,
    filename: Annotated[str, Path(pattern=r"^projet-\d+-[A-Za-z0-9\-]+\.pdf$")],
    session: SessionDep,
    current_user: CurrentUser,
) -> Response:
    """Téléchargement d'un export déjà généré.

    Le motif du nom de fichier est verrouillé par le routeur, et le chemin est reconstruit à
    partir du répertoire d'export : un `../` n'a aucun moyen d'y entrer (traversée de chemin).
    La propriété du projet est revérifiée ici, pas seulement à la demande d'export.
    """
    await get_owned_project(session, project_id, current_user)

    if not filename.startswith(f"projet-{project_id}-"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export introuvable")

    from app.tasks.exports import export_directory

    target = (export_directory() / filename).resolve()
    if not target.is_file() or target.parent != export_directory().resolve():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export introuvable")

    return Response(
        content=target.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{project_id}/exports/pdf/direct")
async def export_pdf_synchronously(
    project_id: int,
    session: SessionDep,
    current_user: CurrentUser,
    measure: Annotated[bool, Query()] = False,
) -> Response:
    """Génère et renvoie le PDF dans la requête (chemin synchrone de référence).

    Conservé délibérément : c'est le point de comparaison qui rend mesurable le gain apporté par
    Celery, comme l'exige `docs/spec-complete.md` §8 (cas 2). `measure=true` ajoute la durée de
    génération dans un en-tête, pour comparer sans instrumenter le client.
    """
    await get_owned_project(session, project_id, current_user)

    started = time.perf_counter()
    project = await _load_project(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable")
    content = render_project_pdf(project, utcnow())
    elapsed_ms = (time.perf_counter() - started) * 1000

    headers = {"Content-Disposition": f'inline; filename="projet-{project_id}.pdf"'}
    if measure:
        headers["X-Generation-Ms"] = f"{elapsed_ms:.1f}"

    return Response(content=content, media_type="application/pdf", headers=headers)


__all__ = ["export_path", "router"]
