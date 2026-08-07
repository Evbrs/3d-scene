"""Les chaînes qui ne tenaient qu'à un maillon manquant.

Six lots ont été développés en parallèle. Chacun était vert isolément, et pourtant trois
fonctions entières restaient inertes en production : la recette était calculée mais jamais
chargée, le paramètre d'instance validé mais jamais déclaré, la menuiserie développée mais jamais
demandée. Aucun test existant ne les couvrait, précisément parce que chaque lot testait son
propre étage.

Ces tests-ci ne vérifient donc pas un calcul — `tests/geometry/` s'en charge — mais la
**continuité** : que ce que produit un étage arrive bien à l'étage suivant. Chacun est écrit pour
tomber si l'un des maillons est retiré.
"""

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.seed import seed_catalog

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


async def _plan(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    project: dict[str, Any] = (
        await client.post("/api/projects", json={"name": "Assemblage"})
    ).json()
    room: dict[str, Any] = (
        await client.post(
            f"/api/projects/{project['id']}/rooms",
            json={"name": "Salon", "polygon": CARRE, "wall_thickness_cm": 10},
        )
    ).json()
    return project, room


def _nodes(scene: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [node for node in scene["rooms"][0]["nodes"] if node["kind"] == kind]


# --- Menuiserie : la recette d'une ouverture n'a pas d'identifiant de type ----------------------


async def test_an_opening_also_produces_the_joinery_that_fills_it(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Le maillon manquant : une ouverture n'a pas de `furniture_type_id`.

    `load_scene_inputs` ne chargeait que les recettes référencées par un élément, or une porte
    désigne la sienne par son `slug`. Le catalogue arrivait donc au moteur sans elle, et le moteur
    — qui refuse d'inventer une boîte grise — n'émettait rien. Aucune erreur, aucune porte.
    """
    await seed_catalog(session)
    project, room = await _plan(auth_client)
    face_a = next(face for face in room["faces"] if face["label"] == "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "door_hinged", "x_offset_cm": 80, "y_offset_cm": 0,
              "width_cm": 83, "height_cm": 204},
    )

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    joinery = _nodes(scene, "joinery")
    assert len(joinery) == 1
    assert joinery[0]["furniture_type_slug"] == "porte-battante"
    assert joinery[0]["opening_kind"] == "door_hinged"
    # La menuiserie occupe le percement : profondeur du mur, et non celle saisie sur l'élément.
    assert joinery[0]["size_cm"] == [83.0, 204.0, 10.0]
    # Le trou reste un trou : la menuiserie s'ajoute au percement, elle ne le remplace pas.
    wall_a = next(node for node in scene["rooms"][0]["nodes"] if node["face_label"] == "A")
    assert wall_a["holes"] == [[[80.0, 0.0], [163.0, 0.0], [163.0, 204.0], [80.0, 204.0]]]


async def test_an_opening_without_its_recipe_stays_a_plain_hole(
    auth_client: AsyncClient,
) -> None:
    """Sans catalogue, aucune menuiserie inventée — le percement seul, et rien d'autre."""
    project, room = await _plan(auth_client)
    face_a = next(face for face in room["faces"] if face["label"] == "A")
    await auth_client.post(
        f"/api/faces/{face_a['id']}/elements",
        json={"kind": "window", "x_offset_cm": 80, "y_offset_cm": 100,
              "width_cm": 90, "height_cm": 110},
    )

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    assert _nodes(scene, "joinery") == []


# --- Paramètres de variation : la recette déclare, l'instance choisit ---------------------------


async def test_the_shelf_count_of_an_instance_changes_its_geometry(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """`variant_params` était validé, stocké, transmis… et lu par personne.

    La déclaration vit dans la recette (spec §4.4). Elle n'avait pas de colonne où être rangée,
    donc `resolve_variants` recevait toujours `None` et retombait sur les répétitions codées dans
    la recette. Le test compare deux instances de la **même** recette : seule la valeur d'instance
    change, donc seul le câblage peut expliquer une différence.
    """
    await seed_catalog(session)
    bibliotheque_id = (await auth_client.get("/api/furniture-types/bibliotheque")).json()["id"]
    project, room = await _plan(auth_client)
    face_a = next(face for face in room["faces"] if face["label"] == "A")

    async def shelves(variant_params: dict[str, Any]) -> int:
        element = (
            await auth_client.post(
                f"/api/faces/{face_a['id']}/elements",
                json={"kind": "furniture", "furniture_type_id": bibliotheque_id,
                      "x_offset_cm": 0, "y_offset_cm": 0,
                      "width_cm": 80, "height_cm": 180, "depth_cm": 30,
                      "variant_params": variant_params},
            )
        ).json()
        scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()
        node = next(item for item in _nodes(scene, "furniture")
                    if item["element_id"] == element["id"])
        await auth_client.delete(f"/api/elements/{element['id']}")
        return sum(1 for part in node["primitives"] if part["color_slot"] == "etagere")

    assert await shelves({}) == 5  # la valeur de la recette, faute de choix d'instance
    assert await shelves({"nb_etageres": 3}) == 3
    assert await shelves({"nb_etageres": 9}) == 9
    # Bornage plutôt que refus : un meuble prévisible vaut mieux qu'un meuble disparu du plan.
    assert await shelves({"nb_etageres": 99}) == 10
    assert await shelves({"nb_etageres": 0}) == 1
    # `isinstance(True, int)` vaut `True` en Python : sans exclusion explicite des booléens,
    # `true` produirait une étagère unique au lieu d'être ignoré.
    assert await shelves({"nb_etageres": True}) == 5
    assert await shelves({"nb_etageres": "beaucoup"}) == 5
    # Un paramètre que la recette ne déclare pas ne pilote rien.
    assert await shelves({"nb_tiroirs": 2}) == 5


async def test_a_variant_only_touches_the_slots_its_recipe_names(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Les pieds d'une table de chevet se répètent aussi, mais ne suivent pas les tiroirs."""
    await seed_catalog(session)
    chevet_id = (await auth_client.get("/api/furniture-types/table-de-chevet")).json()["id"]
    project, room = await _plan(auth_client)

    await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements",
        json={"kind": "furniture", "furniture_type_id": chevet_id,
              "width_cm": 45, "height_cm": 55, "depth_cm": 40,
              "variant_params": {"nb_tiroirs": 3}},
    )

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    primitives = _nodes(scene, "furniture")[0]["primitives"]
    assert sum(1 for part in primitives if part["color_slot"] == "facade") == 3
    assert sum(1 for part in primitives if part["color_slot"] == "pied") == 2


# --- Axe de révolution : la boîte englobante ne dit pas l'orientation ---------------------------


async def test_a_lying_cylinder_keeps_its_declared_axis_through_the_api(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """`axis` traverse le schéma, la base et le moteur — ou la poignée reste dressée.

    Le schéma de recette interdit les champs inconnus : tant qu'il ne déclarait pas `axis`, le
    catalogue ne pouvait pas le poser, et le seed aurait échoué s'il l'avait tenté.
    """
    await seed_catalog(session)
    porte_id = (await auth_client.get("/api/furniture-types/porte-battante")).json()["id"]
    project, room = await _plan(auth_client)

    await auth_client.post(
        f"/api/faces/{room['faces'][0]['id']}/elements",
        json={"kind": "furniture", "furniture_type_id": porte_id,
              "width_cm": 83, "height_cm": 204, "depth_cm": 4},
    )

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    primitives = _nodes(scene, "furniture")[0]["primitives"]
    poignee = next(part for part in primitives if part["color_slot"] == "poignee")
    assert poignee["axis"] == "z"
    # Le panneau, lui, n'a rien de couché : il garde le défaut.
    assert next(part for part in primitives if part["color_slot"] == "panneau")["axis"] == "y"


async def test_the_catalogue_publishes_its_variation_parameters(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """L'éditeur ne peut proposer le réglage que si l'API le publie."""
    await seed_catalog(session)

    commode = (await auth_client.get("/api/furniture-types/commode")).json()

    assert commode["variants"] == [
        {"name": "nb_tiroirs", "axis": "y", "applies_to": ["facade", "poignee"],
         "min": 1, "max": 6}
    ]


# --- Aire nette : ce qu'on annonce dans un devis ------------------------------------------------


async def test_the_scene_exposes_the_net_floor_area(auth_client: AsyncClient) -> None:
    """L'aire brute est mesurée sur l'axe des murs ; l'aire habitable retire leur épaisseur."""
    project, _room = await _plan(auth_client)

    scene = (await auth_client.get(f"/api/projects/{project['id']}/scene")).json()

    room = scene["rooms"][0]
    assert room["floor_area_cm2"] == 120000.0
    # Contour rentré d'une demi-épaisseur (5 cm) sur les quatre côtés : 390 x 290.
    assert room["net_floor_area_cm2"] == 113100.0


# --- Percement borné au mur --------------------------------------------------------------------


async def test_a_hole_never_escapes_its_wall(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Ceinture et bretelles derrière la validation d'API.

    Un trou qui déborde ne donne pas un mur troué : il donne un contour dont le trou croise le
    bord, donc une triangulation dégénérée et un mur invisible. La validation refuse le cas par
    l'API ; on vérifie ici que le moteur ne dépend pas d'elle, car la donnée peut aussi venir d'un
    import ou d'une correction en base.
    """
    from app.geometry.scene import build_scene_graph

    room = {
        "id": 1, "name": "Salon", "polygon": CARRE,
        "wall_thickness_cm": 10.0, "ceiling_height_cm": 250.0,
        "faces": [
            {
                "id": 1, "label": "A", "kind": "wall", "covering": {},
                "start_x_cm": 0.0, "start_y_cm": 0.0, "end_x_cm": 400.0, "end_y_cm": 0.0,
                "elements": [
                    # Débordement franc en largeur comme en hauteur.
                    {"id": 1, "kind": "window", "x_offset_cm": 380.0, "y_offset_cm": 200.0,
                     "width_cm": 200.0, "height_cm": 200.0, "depth_cm": 10.0,
                     "rotation_deg": 0.0, "furniture_type_id": None,
                     "colors": {}, "variant_params": {}},
                    # Entièrement hors du mur : il ne reste rien à percer.
                    {"id": 2, "kind": "window", "x_offset_cm": 900.0, "y_offset_cm": 0.0,
                     "width_cm": 50.0, "height_cm": 50.0, "depth_cm": 10.0,
                     "rotation_deg": 0.0, "furniture_type_id": None,
                     "colors": {}, "variant_params": {}},
                ],
            }
        ],
    }

    graph = build_scene_graph({"id": 1, "rooms": [room]}, {})

    wall = next(node for node in graph["rooms"][0]["nodes"] if node["kind"] == "wall")
    assert wall["holes"] == [[[380.0, 200.0], [400.0, 200.0], [400.0, 250.0], [380.0, 250.0]]]
