#!/bin/sh
# Crée une base de test distincte de la base de développement.
#
# La suite de tests détruit son schéma à la fin de chaque test (`drop_all`) : la pointer sur la
# base de développement efface silencieusement les données locales. Une base dédiée rend
# l'erreur impossible plutôt que documentée.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE DATABASE ${POSTGRES_DB}_test OWNER ${POSTGRES_USER};
EOSQL
