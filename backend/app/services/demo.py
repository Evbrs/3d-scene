"""Projet de démonstration : une salle de bain type, entièrement chiffrable.

L'état vide du produit était la chaîne « Aucun projet pour le moment » devant un canevas blanc.
Personne n'atteint le premier geste monétisé depuis là : il faut d'abord relever une pièce, la
dessiner, l'habiller, puis seulement voir un métré. Ce module fabrique cette pièce une fois, pour
que le premier écran montre le produit fini plutôt que le point de départ.

**Une salle de bain et pas un salon** : c'est la pièce la plus contrainte et la plus lucrative du
second œuvre (`docs/strategie-produit.md` §3.8), celle qui porte le plus de faïence au mètre carré
et le plus d'éléments par mètre linéaire. C'est donc celle où le métré et le devis sont les plus
démonstratifs.

**Entièrement chiffrable** veut dire quelque chose de précis ici : chaque face porte un `material`
que `app/services/pricing.py` sait rattacher tout seul à une ligne du barème par défaut
(`faience` → `FAIENCE`, `carrelage` au sol → `CARRELAGE-SOL`, `peinture` au plafond →
`PEINT-PLAF`). Aucun `FaceCosting` n'est écrit : le devis se génère sans un seul rattachement à
la main, et l'artisan voit d'emblée ce que l'automatisme sait faire.

Le projet est construit **en base directement** et non par une série d'appels HTTP : il faut une
douzaine d'écritures cohérentes entre elles, et les enchaîner côté client ferait autant
d'incréments de version du projet, chacun invalidant celui que le client détient.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.base import ElementKind
from app.models.plan import Element, Face, FurnitureType, Project, Room
from app.services.faces import CEILING_LABEL, FLOOR_LABEL, sync_room_faces

DEMO_PROJECT_NAME = "Salle de bain — démonstration"
DEMO_ROOM_NAME = "Salle de bain"

DEMO_PROJECT_DESCRIPTION = (
    "Chantier de démonstration créé automatiquement : une salle de bain de 2,40 m sur 2,00 m, "
    "habillée et prête à chiffrer. Modifiez-la ou supprimez-la, elle ne sera pas recréée."
)

# 240 cm sur 200 cm, sens trigonométrique — les dimensions d'une salle de bain de
# rénovation courante. Le repère est celui de `Room.polygon` : les murs se lisent A (bas),
# B (droite), C (haut), D (gauche) dans cet ordre.
DEMO_POLYGON: list[list[float]] = [[0, 0], [240, 0], [240, 200], [0, 200]]

DEMO_WALL_THICKNESS_CM = 10.0
DEMO_CEILING_HEIGHT_CM = 250.0

# Revêtements par étiquette de face. `material` est la clé du chiffrage automatique ; les
# dimensions d'unité et le motif sont ce qui rend le calepinage et le taux de chute calculables.
DEMO_COVERINGS: dict[str, dict[str, Any]] = {
    "A": {
        "color": "#E8ECF0",
        "material": "faience",
        "unit_width_cm": 20.0,
        "unit_height_cm": 20.0,
        "pattern": "straight",
    },
    "B": {
        "color": "#E8ECF0",
        "material": "faience",
        "unit_width_cm": 20.0,
        "unit_height_cm": 20.0,
        "pattern": "straight",
    },
    "C": {
        "color": "#D6DBE2",
        "material": "faience",
        "unit_width_cm": 20.0,
        "unit_height_cm": 20.0,
        "pattern": "staggered",
    },
    "D": {
        "color": "#E8ECF0",
        "material": "faience",
        "unit_width_cm": 20.0,
        "unit_height_cm": 20.0,
        "pattern": "straight",
    },
    FLOOR_LABEL: {
        "color": "#8E9296",
        "material": "carrelage",
        "unit_width_cm": 60.0,
        "unit_height_cm": 60.0,
        "pattern": "straight",
    },
    CEILING_LABEL: {"color": "#FFFFFF", "material": "peinture"},
}

# (étiquette de face, nature, slug de recette, largeur, hauteur, profondeur, décalage x, y).
# Les décalages sont mesurés dans le plan de la face : `x` le long du mur, `y` en hauteur — c'est
# l'allège pour une fenêtre, le sol pour un meuble posé.
FaceElementSpec = tuple[str, ElementKind, str, float, float, float, float, float]

DEMO_FACE_ELEMENTS: tuple[FaceElementSpec, ...] = (
    ("A", ElementKind.DOOR_HINGED, "porte-battante", 83.0, 204.0, 8.0, 20.0, 0.0),
    ("C", ElementKind.WINDOW, "fenetre", 60.0, 60.0, 12.0, 90.0, 130.0),
    ("C", ElementKind.FURNITURE, "radiateur", 50.0, 100.0, 12.0, 20.0, 60.0),
    ("B", ElementKind.FURNITURE, "meuble-sous-vasque", 80.0, 85.0, 46.0, 30.0, 0.0),
    ("B", ElementKind.FURNITURE, "vasque", 60.0, 15.0, 40.0, 40.0, 85.0),
    ("B", ElementKind.FURNITURE, "miroir", 60.0, 70.0, 3.0, 40.0, 115.0),
    ("D", ElementKind.FURNITURE, "wc", 38.0, 78.0, 65.0, 40.0, 0.0),
)

# Mobilier posé au sol (spec §10, amendement A4) : un bac de douche ne s'accroche à rien.
# `pos_*` désigne le **centre** de l'emprise dans le repère du plan.
DEMO_ROOM_ELEMENTS: tuple[tuple[str, float, float, float, float, float], ...] = (
    ("bac-de-douche", 90.0, 5.0, 90.0, 55.0, 145.0),
)


async def _furniture_ids(session: AsyncSession) -> dict[str, int]:
    """Identifiants des recettes citées par la démonstration, par slug.

    Le catalogue est **global** (spec §4) et semé par la CLI ou le démarrage d'environnement. Une
    base où il manque n'est pas une erreur : le meuble est alors posé sans recette et rendu comme
    une boîte à ses dimensions. Refuser de construire la démonstration pour ça reviendrait à
    rendre l'accueil dépendant d'un seed dont il n'a pas besoin.
    """
    wanted = {slug for _, _, slug, *_ in DEMO_FACE_ELEMENTS} | {
        slug for slug, *_ in DEMO_ROOM_ELEMENTS
    }
    rows = (
        await session.execute(
            select(FurnitureType).where(col(FurnitureType.slug).in_(sorted(wanted)))
        )
    ).scalars()
    return {row.slug: row.id or 0 for row in rows}


async def create_demo_project(
    session: AsyncSession, *, organization_id: int, owner_id: int
) -> Project:
    """Construit le chantier de démonstration dans l'organisation donnée.

    Ne valide rien à l'exécution : la géométrie est une constante de ce module, vérifiée une fois
    pour toutes par `tests/test_compte.py`, qui la confronte aux mêmes fonctions d'encombrement
    que l'API. Un contrôle ici ferait croire que la donnée est variable alors qu'elle est figée.

    L'appelant commet — la démonstration fait partie de la même transaction que ce qui la
    déclenche, et un projet à moitié construit vaut moins que pas de projet du tout.
    """
    project = Project(
        organization_id=organization_id,
        owner_id=owner_id,
        name=DEMO_PROJECT_NAME,
        description=DEMO_PROJECT_DESCRIPTION,
    )
    session.add(project)
    await session.flush()

    room = Room(
        project_id=project.id or 0,
        name=DEMO_ROOM_NAME,
        polygon=[list(vertex) for vertex in DEMO_POLYGON],
        wall_thickness_cm=DEMO_WALL_THICKNESS_CM,
        ceiling_height_cm=DEMO_CEILING_HEIGHT_CM,
    )
    session.add(room)
    await session.flush()

    faces = await sync_room_faces(session, room)
    await session.flush()
    by_label: dict[str, Face] = {face.label: face for face in faces}

    for label, covering in DEMO_COVERINGS.items():
        face = by_label.get(label)
        if face is not None:
            face.covering = dict(covering)

    recipes = await _furniture_ids(session)

    for label, kind, slug, width, height, depth, offset_x, offset_y in DEMO_FACE_ELEMENTS:
        face = by_label.get(label)
        if face is None:
            continue
        session.add(
            Element(
                face_id=face.id or 0,
                kind=kind,
                furniture_type_id=recipes.get(slug),
                width_cm=width,
                height_cm=height,
                depth_cm=depth,
                x_offset_cm=offset_x,
                y_offset_cm=offset_y,
            )
        )

    for slug, width, height, depth, pos_x, pos_y in DEMO_ROOM_ELEMENTS:
        session.add(
            Element(
                room_id=room.id or 0,
                kind=ElementKind.FURNITURE,
                furniture_type_id=recipes.get(slug),
                width_cm=width,
                height_cm=height,
                depth_cm=depth,
                pos_x_cm=pos_x,
                pos_y_cm=pos_y,
            )
        )

    await session.flush()
    return project


async def organization_has_projects(session: AsyncSession, organization_id: int) -> bool:
    """Vrai dès qu'un chantier existe dans l'organisation.

    C'est la condition d'idempotence de la démonstration : elle ne se pose que sur un espace
    vierge. Sans elle, un artisan qui supprime le projet de démonstration le retrouverait au
    rechargement suivant — un objet qu'on ne peut pas jeter est plus irritant qu'un état vide.
    """
    return (
        await session.execute(
            select(Project.id).where(col(Project.organization_id) == organization_id).limit(1)
        )
    ).scalar_one_or_none() is not None
