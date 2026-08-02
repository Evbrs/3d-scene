"""Développement d'une recette de mobilier en primitives concrètes (`docs/spec-complete.md` §4.1).

Le développement a lieu **côté serveur**, pas dans le viewer : la spec §3.1 est explicite, le
frontend « ne fait que traduire ce JSON en objets 3D — aucune logique métier côté client ».
C'est aussi ce qui rend la répétition (`repeat_*`, `auto`) testable par des fixtures.
"""

from dataclasses import dataclass
from typing import Any

AUTO = "auto"


@dataclass(frozen=True)
class Primitive:
    """Une primitive développée, en centimètres, relative au centre du meuble."""

    type: str
    offset: tuple[float, float, float]
    size: tuple[float, float, float]
    color_slot: str
    color: str | None
    operation: str

    def to_dict(self, digits: int = 4) -> dict[str, Any]:
        return {
            "type": self.type,
            "offset": [round(value, digits) for value in self.offset],
            "size": [round(value, digits) for value in self.size],
            "color_slot": self.color_slot,
            "color": self.color,
            "operation": self.operation,
        }


def _axis_centers(
    position: float | str, repeat: int, relative_size: float, gap: float
) -> list[float]:
    """Centres relatifs des copies d'une primitive répétée le long d'un axe.

    Le groupe de copies est **centré sur la position demandée**, et `auto` signifie simplement
    « centré sur le milieu de la boîte englobante », c'est-à-dire 0,5. Une seule formule couvre
    donc les deux cas, ce qui évite deux comportements divergents à maintenir.

    Exemple de la spec §4.1 (commode, 4 tiroirs, taille 0,18, jeu 0,02) : pas = 0,20 et centres
    = 0,20 / 0,40 / 0,60 / 0,80.
    """
    base = 0.5 if position == AUTO else float(position)
    if repeat <= 1:
        return [base]

    step = relative_size + gap
    first = base - (repeat - 1) * step / 2.0
    return [first + index * step for index in range(repeat)]


def expand_recipe(
    parts: list[dict[str, Any]],
    size_cm: tuple[float, float, float],
    colors: dict[str, str],
) -> list[Primitive]:
    """Développe une recette en primitives absolues (en cm, relatives au centre du meuble).

    `colors` ne contient que les emplacements choisis par l'instance ; les autres restent à
    `None`, charge au rendu d'appliquer son matériau par défaut. Inventer une couleur ici la
    rendrait indiscernable d'un choix explicite de l'utilisateur.
    """
    width, height, depth = size_cm
    dimensions = (width, height, depth)
    primitives: list[Primitive] = []

    for part in parts:
        relative_size = tuple(float(value) for value in part["rel_size"])
        absolute_size = (
            relative_size[0] * width,
            relative_size[1] * height,
            relative_size[2] * depth,
        )
        repeats = (
            int(part.get("repeat_x", 1)),
            int(part.get("repeat_y", 1)),
            int(part.get("repeat_z", 1)),
        )
        gap = float(part.get("gap", 0.0))
        color_slot = str(part["color_slot"])

        centers_per_axis = [
            _axis_centers(part["rel_position"][axis], repeats[axis], relative_size[axis], gap)
            for axis in range(3)
        ]

        for center_x in centers_per_axis[0]:
            for center_y in centers_per_axis[1]:
                for center_z in centers_per_axis[2]:
                    centers = (center_x, center_y, center_z)
                    primitives.append(
                        Primitive(
                            type=str(part["type"]),
                            # Décalage par rapport au centre de la boîte englobante : une
                            # coordonnée relative de 0,5 tombe donc sur 0.
                            offset=tuple(  # type: ignore[arg-type]
                                (centers[axis] - 0.5) * dimensions[axis] for axis in range(3)
                            ),
                            size=absolute_size,
                            color_slot=color_slot,
                            color=colors.get(color_slot),
                            operation=str(part.get("operation", "add")),
                        )
                    )

    return primitives


def requires_csg(primitives: list[Primitive]) -> bool:
    """Vrai si le meuble exige une opération booléenne (spec §4.2).

    Le frontend s'en sert pour n'activer `three-bvh-csg` — expérimental et coûteux (§3.2) — que
    sur les meubles qui en ont réellement besoin : vasque, baignoire, bac de douche.
    """
    return any(primitive.operation == "subtract" for primitive in primitives)
