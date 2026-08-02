"""Écrit le schéma OpenAPI dans un fichier.

Sert à maintenir `frontend/src/api/openapi-snapshot.json`, l'instantané contre lequel le
frontend vérifie qu'il n'invente aucune route (`docs/plan-generation-ia.md` §6). La CI le
régénère et échoue si le fichier versionné a divergé : sans ça, le test de contrat validerait un
contrat périmé.

Usage : `python -m scripts.dump_openapi ../frontend/src/api/openapi-snapshot.json`
"""

import json
import pathlib
import sys

from app.main import app


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    target = pathlib.Path(argv[0])
    schema = app.openapi()
    target.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"{len(schema['paths'])} chemins écrits dans {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
