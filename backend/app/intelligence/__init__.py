"""Moteur d'intelligence du plan — algorithmique et local (`docs/strategie-produit.md` §3.8).

Trois moteurs, par valeur décroissante, et **un seul principe** : tout est déterministe, calculé
sur place, sans clé, sans appel sortant et sans modèle de langage. C'est une exigence du
propriétaire et non une préférence — un résultat reproductible est un résultat qu'on peut figer
dans une fixture calculée à la main, exactement comme la géométrie (`CLAUDE.md`).

- `ergonomy` : les seuils **paramétrables**, leur source, et la géométrie d'usage — emprises,
  contour au nu intérieur, secteurs de débattement, mesure d'un passage libre ;
- `rules` : le contrôle de conformité, un moteur de règles qui relit le scene graph et rend des
  anomalies identifiées, hiérarchisées par sévérité et rattachées à des entités du plan ;
- `layout` : le calepinage avancé (sens de pose, première rangée, plinthes) et l'aménagement
  automatique sous contraintes.

L'entrée de tout le paquet est le **scene graph** (`app.geometry.scene.build_scene_graph`) et non
les modèles SQLModel, pour les trois mêmes raisons que le métré : c'est une fonction pure, les
fixtures l'alimentent directement sans base de données, et l'API le sert déjà depuis son cache —
deux chemins qui reconstruiraient la géométrie séparément finiraient par se contredire.
"""

from app.intelligence.ergonomy import Thresholds
from app.intelligence.layout import (
    LayingRules,
    plan_face_tiling,
    plan_room_skirting,
    propose_layouts,
)
from app.intelligence.rules import Anomaly, Severity, inspect_scene

__all__ = [
    "Anomaly",
    "LayingRules",
    "Severity",
    "Thresholds",
    "inspect_scene",
    "plan_face_tiling",
    "plan_room_skirting",
    "propose_layouts",
]
