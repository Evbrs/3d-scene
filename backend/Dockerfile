# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Les dépendances sont installées avant le code pour tirer parti du cache de couches.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install -e ".[dev]"

COPY . .

# Utilisateur non-root (durcissement de base ; la passe complète est en P12).
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
