# syntax=docker/dockerfile:1
#
# Trois étages :
#   builder → installe les dépendances verrouillées dans un préfixe isolé ;
#   runtime → image de production : elle ne reçoit que ce préfixe, donc ni pip, ni compilateur,
#             ni pytest/ruff/mypy ;
#   dev     → runtime + outillage de test, cible utilisée par docker-compose.yml.
#
# Avant ce découpage, l'unique image installait `.[dev]` : la production embarquait la suite de
# tests et les linters. Aucun étage n'est « par défaut » — les deux fichiers compose désignent
# explicitement leur cible, parce que la cible implicite d'un Dockerfile multi-étage est le
# DERNIER étage, c'est-à-dire ici `dev`.

FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Le projet n'est pas installé comme distribution : il est copié dans /app et rendu
    # importable par ce chemin. Sans ça, `uvicorn app.main:app` — lancé par un script console de
    # /usr/local/bin, dont le sys.path[0] est /usr/local/bin — ne trouverait pas le paquet `app`.
    PYTHONPATH=/app

WORKDIR /app


FROM base AS builder

# Seul le verrou est copié : une modification du code applicatif ne réinvalide pas cette couche.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install --prefix=/install -r requirements.txt


FROM base AS runtime

COPY --from=builder /install /usr/local
COPY . .

# Utilisateur non-root.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app

# Le répertoire d'export doit exister ET appartenir à l'utilisateur applicatif AVANT le montage :
# un volume nommé Docker hérite des droits du répertoire de l'image. Sans ça, le worker Celery
# tourne en non-root sur un volume appartenant à root et échoue en `Permission denied` — un
# défaut que les tests en mode `task_always_eager` ne peuvent pas voir, puisqu'ils écrivent
# ailleurs et sous une autre identité.
RUN mkdir -p /exports && chown appuser:appuser /exports

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM runtime AS dev

USER root
# Installation éditable pour que le code monté depuis l'hôte prime, plus les extras de test.
# Le verrou sert de contrainte : développement et production exécutent exactement les mêmes
# versions des dépendances d'exécution, seuls les outils de test s'ajoutent.
RUN pip install -e ".[dev]" -c requirements.txt
USER appuser
