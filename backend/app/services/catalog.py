"""Catalogue de mobilier générique (`docs/spec-complete.md` §4.3).

Une entrée par ligne du tableau de la spec. Chaque entrée est une recette de composition
(§4.1) : des primitives en coordonnées relatives, pas un modèle 3D importé — cohérent avec la
volonté de rester générique et sans dépendance à des bibliothèques de marques.

Convention des coordonnées relatives, valable pour tout le catalogue :
- origine au coin bas-arrière-gauche de la boîte englobante, axes dans [0, 1] ;
- `rel_position` est le **centre** de la primitive, `rel_size` sa taille ;
- x = largeur, y = hauteur, z = profondeur.
"""

from typing import Any

from app.models.base import FurnitureCategory, PartPrimitive

# `operation: "subtract"` marque les primitives qui exigent une opération booléenne (§4.2) :
# vasque et baignoire. C'est le seul endroit du catalogue qui déclenche le recours au CSG.
SUBTRACT = "subtract"


def _box(
    position: tuple[Any, Any, Any],
    size: tuple[float, float, float],
    color_slot: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "type": PartPrimitive.BOX.value,
        "rel_position": list(position),
        "rel_size": list(size),
        "color_slot": color_slot,
        **extra,
    }


def _cylinder(
    position: tuple[Any, Any, Any],
    size: tuple[float, float, float],
    color_slot: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "type": PartPrimitive.CYLINDER.value,
        "rel_position": list(position),
        "rel_size": list(size),
        "color_slot": color_slot,
        **extra,
    }


def _sphere(
    position: tuple[Any, Any, Any],
    size: tuple[float, float, float],
    color_slot: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "type": PartPrimitive.SPHERE.value,
        "rel_position": list(position),
        "rel_size": list(size),
        "color_slot": color_slot,
        **extra,
    }


def _variant(
    name: str,
    axis: str,
    applies_to: list[str],
    minimum: int,
    maximum: int,
) -> dict[str, Any]:
    """Paramètre de variation d'une recette (spec §4.4).

    Les emplacements visés sont désignés par leur `color_slot` et non par leur rang dans `parts` :
    un rang change dès qu'on insère une primitive, un `color_slot` non.

    `minimum` et `maximum` bornent la valeur reçue de l'instance plutôt que de la refuser : une
    saisie hors bornes donne alors un meuble prévisible et corrigeable, là où un refus le ferait
    disparaître du plan.
    """
    return {
        "name": name,
        "axis": axis,
        "applies_to": applies_to,
        "min": minimum,
        "max": maximum,
    }


CATALOG: list[dict[str, Any]] = [
    # --- Général --------------------------------------------------------------------------
    {
        "slug": "porte-battante",
        "name": "Porte battante",
        "category": FurnitureCategory.GENERAL,
        "color_slots": ["panneau", "poignee"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "panneau"),
            # Béquille perpendiculaire au panneau : sans `axis`, la révolution reste verticale et
            # la poignée se dresse au lieu de pointer vers l'utilisateur.
            _cylinder((0.9, 0.45, 1.2), (0.06, 0.06, 0.3), "poignee", axis="z"),
        ],
        "default_width_cm": 83.0,
        "default_height_cm": 204.0,
        "default_depth_cm": 4.0,
    },
    {
        "slug": "porte-coulissante",
        "name": "Porte coulissante",
        "category": FurnitureCategory.GENERAL,
        "color_slots": ["panneau", "rail"],
        "parts": [
            _box((0.5, 0.48, 0.5), (1.0, 0.96, 1.0), "panneau"),
            _box((0.5, 0.99, 0.5), (1.1, 0.02, 1.4), "rail"),
        ],
        "default_width_cm": 83.0,
        "default_height_cm": 204.0,
        "default_depth_cm": 4.0,
    },
    {
        "slug": "fenetre",
        "name": "Fenêtre",
        "category": FurnitureCategory.GENERAL,
        "color_slots": ["dormant", "vitrage"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "dormant"),
            _box((0.5, 0.5, 0.5), (0.88, 0.88, 1.05), "vitrage"),
            _box((0.5, 0.5, 0.5), (0.03, 0.9, 1.1), "dormant"),
        ],
        "default_width_cm": 120.0,
        "default_height_cm": 110.0,
        "default_depth_cm": 12.0,
    },
    {
        "slug": "radiateur",
        "name": "Radiateur",
        "category": FurnitureCategory.GENERAL,
        "color_slots": ["corps", "ailette"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 0.6), "corps"),
            # Les ailettes verticales : une seule primitive répétée, pas N primitives figées.
            _box(("auto", 0.5, 0.85), (0.04, 0.92, 0.5), "ailette", repeat_x=12, gap=0.02),
        ],
        "default_width_cm": 100.0,
        "default_height_cm": 60.0,
        "default_depth_cm": 10.0,
    },
    {
        "slug": "prise",
        "name": "Prise électrique",
        "category": FurnitureCategory.GENERAL,
        "color_slots": ["plaque", "alveole"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "plaque"),
            # Alvéole percée dans la plaque : le disque regarde la pièce, il n'est pas couché.
            _cylinder((0.5, 0.5, 1.05), (0.5, 0.5, 0.2), "alveole", axis="z"),
        ],
        "default_width_cm": 8.0,
        "default_height_cm": 8.0,
        "default_depth_cm": 1.0,
    },
    {
        "slug": "interrupteur",
        "name": "Interrupteur",
        "category": FurnitureCategory.GENERAL,
        "color_slots": ["plaque", "bascule"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "plaque"),
            _box((0.5, 0.5, 1.1), (0.55, 0.55, 0.3), "bascule"),
        ],
        "default_width_cm": 8.0,
        "default_height_cm": 8.0,
        "default_depth_cm": 1.0,
    },
    {
        "slug": "applique",
        "name": "Applique murale",
        "category": FurnitureCategory.GENERAL,
        "color_slots": ["socle", "diffuseur"],
        "parts": [
            _box((0.5, 0.2, 0.3), (0.5, 0.4, 0.6), "socle"),
            _sphere((0.5, 0.65, 0.5), (0.9, 0.7, 0.9), "diffuseur"),
        ],
        "default_width_cm": 20.0,
        "default_height_cm": 25.0,
        "default_depth_cm": 12.0,
    },
    {
        "slug": "suspension",
        "name": "Suspension",
        "category": FurnitureCategory.GENERAL,
        "color_slots": ["cable", "abat_jour"],
        "parts": [
            _cylinder((0.5, 0.8, 0.5), (0.03, 0.4, 0.03), "cable"),
            _cylinder((0.5, 0.3, 0.5), (1.0, 0.6, 1.0), "abat_jour"),
        ],
        "default_width_cm": 40.0,
        "default_height_cm": 80.0,
        "default_depth_cm": 40.0,
    },
    # --- Salle de bain --------------------------------------------------------------------
    {
        "slug": "vasque",
        "name": "Vasque",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["ceramique", "robinet"],
        # Spec §4.2 : intersection d'une boîte et d'une forme creusée → `three-bvh-csg`.
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "ceramique"),
            _sphere((0.5, 0.85, 0.5), (0.75, 0.9, 0.75), "ceramique", operation=SUBTRACT),
            _cylinder((0.5, 1.05, 0.12), (0.08, 0.35, 0.08), "robinet"),
        ],
        "default_width_cm": 60.0,
        "default_height_cm": 18.0,
        "default_depth_cm": 45.0,
    },
    {
        "slug": "meuble-sous-vasque",
        "name": "Meuble sous-vasque",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["corps", "facade", "poignee"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "corps"),
            _box((0.5, "auto", 1.01), (0.94, 0.44, 0.03), "facade", repeat_y=2, gap=0.03),
            _box((0.5, "auto", 1.05), (0.3, 0.03, 0.03), "poignee", repeat_y=2, gap=0.44),
        ],
        "variants": [_variant("nb_tiroirs", "y", ["facade", "poignee"], 1, 4)],
        "default_width_cm": 60.0,
        "default_height_cm": 55.0,
        "default_depth_cm": 45.0,
    },
    {
        "slug": "baignoire",
        "name": "Baignoire",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["email", "robinet"],
        # Spec §4.2 : soustraction d'une boîte plus petite dans une boîte pleine.
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "email"),
            _box((0.5, 0.62, 0.5), (0.88, 0.85, 0.86), "email", operation=SUBTRACT),
            _cylinder((0.06, 1.05, 0.5), (0.06, 0.25, 0.06), "robinet"),
        ],
        "default_width_cm": 170.0,
        "default_height_cm": 55.0,
        "default_depth_cm": 75.0,
    },
    {
        "slug": "bac-de-douche",
        "name": "Bac de douche",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["receveur", "bonde"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "receveur"),
            _box((0.5, 0.75, 0.5), (0.92, 0.7, 0.92), "receveur", operation=SUBTRACT),
            _cylinder((0.5, 0.35, 0.5), (0.12, 0.06, 0.12), "bonde"),
        ],
        "default_width_cm": 90.0,
        "default_height_cm": 8.0,
        "default_depth_cm": 90.0,
    },
    {
        "slug": "wc",
        "name": "WC",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["ceramique", "abattant"],
        "parts": [
            _box((0.5, 0.25, 0.55), (0.7, 0.5, 0.9), "ceramique"),
            _box((0.5, 0.62, 0.55), (0.75, 0.08, 0.95), "abattant"),
            _box((0.5, 0.72, 0.12), (0.9, 0.55, 0.25), "ceramique"),
        ],
        "default_width_cm": 38.0,
        "default_height_cm": 80.0,
        "default_depth_cm": 60.0,
    },
    {
        "slug": "miroir",
        "name": "Miroir",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["cadre", "glace"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "cadre"),
            _box((0.5, 0.5, 1.02), (0.92, 0.92, 0.1), "glace"),
        ],
        "default_width_cm": 60.0,
        "default_height_cm": 80.0,
        "default_depth_cm": 3.0,
    },
    {
        "slug": "colonne-de-rangement",
        "name": "Colonne de rangement",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["corps", "etagere"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "corps"),
            _box((0.5, "auto", 0.5), (0.92, 0.02, 0.92), "etagere", repeat_y=4, gap=0.02),
        ],
        "variants": [_variant("nb_etageres", "y", ["etagere"], 1, 8)],
        "default_width_cm": 40.0,
        "default_height_cm": 180.0,
        "default_depth_cm": 35.0,
    },
    {
        "slug": "panier-a-linge",
        "name": "Panier à linge",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["corps", "couvercle"],
        "parts": [
            _cylinder((0.5, 0.45, 0.5), (1.0, 0.9, 1.0), "corps"),
            _cylinder((0.5, 0.95, 0.5), (1.02, 0.1, 1.02), "couvercle"),
        ],
        "default_width_cm": 40.0,
        "default_height_cm": 60.0,
        "default_depth_cm": 40.0,
    },
    {
        "slug": "barre-d-appui",
        "name": "Barre d'appui",
        "category": FurnitureCategory.BATHROOM,
        "color_slots": ["barre", "platine"],
        "parts": [
            # Barre et platines sont allongées sur la profondeur : l'axe de révolution suit leur
            # plus grande dimension relative, sinon chacune dégénère en disque écrasé.
            _cylinder((0.5, 0.5, 0.6), (0.06, 0.06, 1.0), "barre", axis="z"),
            _cylinder((0.06, 0.5, 0.1), (0.12, 0.12, 0.2), "platine", axis="z"),
            _cylinder((0.94, 0.5, 0.1), (0.12, 0.12, 0.2), "platine", axis="z"),
        ],
        "default_width_cm": 60.0,
        "default_height_cm": 8.0,
        "default_depth_cm": 8.0,
    },
    # --- Chambre --------------------------------------------------------------------------
    {
        "slug": "lit",
        "name": "Lit",
        "category": FurnitureCategory.BEDROOM,
        "color_slots": ["sommier", "matelas", "tete_de_lit"],
        "parts": [
            _box((0.5, 0.2, 0.5), (1.0, 0.4, 1.0), "sommier"),
            _box((0.5, 0.55, 0.5), (0.96, 0.3, 0.96), "matelas"),
            _box((0.5, 0.7, 0.02), (1.0, 0.6, 0.04), "tete_de_lit"),
        ],
        "default_width_cm": 140.0,
        "default_height_cm": 100.0,
        "default_depth_cm": 200.0,
    },
    {
        "slug": "commode",
        "name": "Commode",
        "category": FurnitureCategory.BEDROOM,
        "color_slots": ["corps", "facade", "poignee"],
        # Exemple canonique de la spec §4.1, repris tel quel.
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "corps"),
            _box((0.5, "auto", 1.01), (0.9, 0.18, 0.02), "facade", repeat_y=4, gap=0.02),
            _box((0.5, "auto", 1.04), (0.25, 0.03, 0.03), "poignee", repeat_y=4, gap=0.17),
        ],
        "variants": [_variant("nb_tiroirs", "y", ["facade", "poignee"], 1, 6)],
        "default_width_cm": 100.0,
        "default_height_cm": 85.0,
        "default_depth_cm": 45.0,
    },
    {
        "slug": "armoire",
        "name": "Armoire",
        "category": FurnitureCategory.BEDROOM,
        "color_slots": ["corps", "porte", "poignee"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "corps"),
            _box(("auto", 0.5, 1.01), (0.48, 0.96, 0.03), "porte", repeat_x=2, gap=0.02),
            _box(("auto", 0.5, 1.05), (0.03, 0.2, 0.03), "poignee", repeat_x=2, gap=0.1),
        ],
        "variants": [_variant("nb_portes", "x", ["porte", "poignee"], 1, 4)],
        "default_width_cm": 120.0,
        "default_height_cm": 200.0,
        "default_depth_cm": 60.0,
    },
    {
        "slug": "table-de-chevet",
        "name": "Table de chevet",
        "category": FurnitureCategory.BEDROOM,
        "color_slots": ["corps", "facade", "pied"],
        "parts": [
            _box((0.5, 0.6, 0.5), (1.0, 0.8, 1.0), "corps"),
            _box((0.5, "auto", 1.01), (0.9, 0.3, 0.02), "facade", repeat_y=2, gap=0.04),
            _box(("auto", 0.1, 0.5), (0.08, 0.2, 0.08), "pied", repeat_x=2, gap=0.8),
        ],
        # `applies_to` ne cite pas "pied" : les deux pieds se répètent eux aussi, mais leur nombre
        # ne suit pas celui des tiroirs.
        "variants": [_variant("nb_tiroirs", "y", ["facade"], 1, 3)],
        "default_width_cm": 45.0,
        "default_height_cm": 55.0,
        "default_depth_cm": 40.0,
    },
    {
        "slug": "bureau",
        "name": "Bureau",
        "category": FurnitureCategory.BEDROOM,
        "color_slots": ["plateau", "pied"],
        "parts": [
            _box((0.5, 0.95, 0.5), (1.0, 0.1, 1.0), "plateau"),
            _box(("auto", 0.45, 0.5), (0.06, 0.9, 0.9), "pied", repeat_x=2, gap=0.88),
        ],
        "default_width_cm": 140.0,
        "default_height_cm": 75.0,
        "default_depth_cm": 70.0,
    },
    # --- Salon ----------------------------------------------------------------------------
    {
        "slug": "canape",
        "name": "Canapé",
        "category": FurnitureCategory.LIVING_ROOM,
        "color_slots": ["structure", "assise", "dossier"],
        "parts": [
            _box((0.5, 0.25, 0.5), (1.0, 0.5, 1.0), "structure"),
            _box(("auto", 0.55, 0.55), (0.46, 0.2, 0.85), "assise", repeat_x=2, gap=0.02),
            _box((0.5, 0.7, 0.1), (1.0, 0.6, 0.2), "dossier"),
        ],
        "default_width_cm": 200.0,
        "default_height_cm": 85.0,
        "default_depth_cm": 90.0,
    },
    {
        "slug": "table-basse",
        "name": "Table basse",
        "category": FurnitureCategory.LIVING_ROOM,
        "color_slots": ["plateau", "pied"],
        "parts": [
            _box((0.5, 0.9, 0.5), (1.0, 0.2, 1.0), "plateau"),
            _box(("auto", 0.4, 0.5), (0.08, 0.8, 0.85), "pied", repeat_x=2, gap=0.84),
        ],
        "default_width_cm": 110.0,
        "default_height_cm": 40.0,
        "default_depth_cm": 60.0,
    },
    {
        "slug": "meuble-tv",
        "name": "Meuble TV",
        "category": FurnitureCategory.LIVING_ROOM,
        "color_slots": ["corps", "facade"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "corps"),
            _box(("auto", 0.5, 1.01), (0.3, 0.8, 0.02), "facade", repeat_x=3, gap=0.02),
        ],
        "variants": [_variant("nb_niches", "x", ["facade"], 1, 5)],
        "default_width_cm": 160.0,
        "default_height_cm": 45.0,
        "default_depth_cm": 40.0,
    },
    {
        "slug": "bibliotheque",
        "name": "Bibliothèque / étagère",
        "category": FurnitureCategory.LIVING_ROOM,
        "color_slots": ["corps", "etagere"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "corps"),
            _box((0.5, "auto", 0.5), (0.94, 0.03, 0.94), "etagere", repeat_y=5, gap=0.02),
        ],
        "variants": [_variant("nb_etageres", "y", ["etagere"], 1, 10)],
        "default_width_cm": 80.0,
        "default_height_cm": 180.0,
        "default_depth_cm": 30.0,
    },
    # --- Cuisine --------------------------------------------------------------------------
    {
        "slug": "meuble-bas",
        "name": "Meuble bas",
        "category": FurnitureCategory.KITCHEN,
        "color_slots": ["caisson", "facade", "plan_de_travail"],
        "parts": [
            _box((0.5, 0.46, 0.5), (1.0, 0.92, 1.0), "caisson"),
            _box((0.5, 0.46, 1.01), (0.96, 0.88, 0.02), "facade"),
            _box((0.5, 0.96, 0.5), (1.02, 0.08, 1.04), "plan_de_travail"),
        ],
        "default_width_cm": 60.0,
        "default_height_cm": 88.0,
        "default_depth_cm": 60.0,
    },
    {
        "slug": "meuble-haut",
        "name": "Meuble haut",
        "category": FurnitureCategory.KITCHEN,
        "color_slots": ["caisson", "facade"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), "caisson"),
            _box(("auto", 0.5, 1.01), (0.48, 0.96, 0.02), "facade", repeat_x=2, gap=0.02),
        ],
        "variants": [_variant("nb_portes", "x", ["facade"], 1, 3)],
        "default_width_cm": 60.0,
        "default_height_cm": 70.0,
        "default_depth_cm": 35.0,
    },
    {
        "slug": "ilot",
        "name": "Îlot",
        "category": FurnitureCategory.KITCHEN,
        "color_slots": ["caisson", "plan_de_travail"],
        "parts": [
            _box((0.5, 0.46, 0.5), (1.0, 0.92, 1.0), "caisson"),
            _box((0.5, 0.96, 0.5), (1.06, 0.08, 1.1), "plan_de_travail"),
        ],
        "default_width_cm": 180.0,
        "default_height_cm": 90.0,
        "default_depth_cm": 90.0,
    },
    {
        "slug": "table",
        "name": "Table",
        "category": FurnitureCategory.KITCHEN,
        "color_slots": ["plateau", "pied"],
        "parts": [
            _box((0.5, 0.94, 0.5), (1.0, 0.12, 1.0), "plateau"),
            _box(("auto", 0.44, 0.5), (0.07, 0.88, 0.9), "pied", repeat_x=2, gap=0.86),
        ],
        "default_width_cm": 160.0,
        "default_height_cm": 75.0,
        "default_depth_cm": 90.0,
    },
    {
        "slug": "chaise",
        "name": "Chaise",
        "category": FurnitureCategory.KITCHEN,
        "color_slots": ["assise", "dossier", "pied"],
        "parts": [
            _box((0.5, 0.5, 0.5), (1.0, 0.06, 1.0), "assise"),
            _box((0.5, 0.78, 0.05), (1.0, 0.45, 0.08), "dossier"),
            _box(("auto", 0.25, 0.5), (0.06, 0.5, 0.9), "pied", repeat_x=2, gap=0.88),
        ],
        "default_width_cm": 45.0,
        "default_height_cm": 90.0,
        "default_depth_cm": 45.0,
    },
]
