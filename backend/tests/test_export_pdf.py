"""Élévations vectorielles cotées — le document de chantier (`docs/strategie-produit.md` §3.3).

Deux niveaux de vérification, et c'est délibéré :

- le **modèle** (`room_elevations`) est une fonction pure : longueurs, allèges et bornage y sont
  contrôlés à la valeur près, sans passer par le rendu ;
- le **PDF produit** est relu, page par page et texte par texte. Sans cette seconde couche, un
  dessin qui n'imprimerait aucune de ses cotes passerait la suite au vert.

Les flux de page de reportlab sont encodés en ASCII85 puis compressés : les chaînes n'apparaissent
pas dans les octets bruts. Elles sont donc décodées ici à la main, plutôt qu'en ajoutant au projet
une dépendance de lecture de PDF pour ce seul usage.
"""

import base64
import json
import re
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.geometry.scene import build_scene_graph
from app.services.export_pdf import (
    SCALE_DENOMINATORS,
    _free_footprint,
    _normalised_scale,
    furniture_count,
    render_project_pdf,
    room_elevations,
    room_floor_fixtures,
    wall_elevations,
)

FIXED_DATE = datetime(2026, 8, 8, 9, 30, tzinfo=UTC)
CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]

FENETRE = {
    "id": 1, "kind": "window", "x_offset_cm": 80, "y_offset_cm": 95,
    "width_cm": 120, "height_cm": 110, "depth_cm": 12,
}
PORTE = {
    "id": 2, "kind": "door_hinged", "x_offset_cm": 40, "y_offset_cm": 0,
    "width_cm": 90, "height_cm": 204, "depth_cm": 6,
}
MEUBLE = {
    "id": 3, "kind": "furniture", "x_offset_cm": 250, "y_offset_cm": 0,
    "width_cm": 90, "height_cm": 85, "depth_cm": 60, "furniture_type_id": 7,
}
# Mobilier posé au sol, ancré à la pièce (spec §10, amendements A4 et A7) : il n'est adossé à
# aucune face, donc aucune planche d'élévation ne le montre.
BAIGNOIRE = {
    "id": 4, "kind": "furniture", "pos_x_cm": 200, "pos_y_cm": 150,
    "width_cm": 170, "height_cm": 55, "depth_cm": 75,
    "rotation_deg": 0, "furniture_type_id": 8, "furniture_name": "Baignoire",
}


def _wall(
    face_id: int, label: str, segment: tuple[float, float, float, float],
    elements: list[dict[str, Any]] | None = None,
    covering: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start_x, start_y, end_x, end_y = segment
    return {
        "id": face_id, "label": label, "kind": "wall",
        "start_x_cm": start_x, "start_y_cm": start_y,
        "end_x_cm": end_x, "end_y_cm": end_y,
        "covering": covering or {}, "elements": elements or [],
    }


def _room(
    faces: list[dict[str, Any]],
    polygon: list[list[float]] | None = None,
    elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": 1, "name": "Salle de bains", "wall_thickness_cm": 10, "ceiling_height_cm": 250,
        "polygon": CARRE if polygon is None else polygon, "faces": faces,
        # Même clé que `app/api/scene.py::project_to_plain_dict` réserve au mobilier libre.
        "elements": elements or [],
    }


def _carre_room() -> dict[str, Any]:
    """La pièce de référence : 400 sur 300, une fenêtre et un meuble en A, une porte en B."""
    return _room(
        [
            _wall(1, "A", (0, 0, 400, 0), [FENETRE, MEUBLE]),
            _wall(2, "B", (400, 0, 400, 300), [PORTE]),
            _wall(3, "C", (400, 300, 0, 300)),
            _wall(4, "D", (0, 300, 0, 0)),
        ]
    )


def _meublee() -> dict[str, Any]:
    """La pièce de référence, plus une baignoire posée au sol : personne ne l'adosse à un mur."""
    room = _carre_room()
    room["elements"] = [BAIGNOIRE]
    return room


def _project(rooms: list[dict[str, Any]], name: str = "Chantier Dupont") -> dict[str, Any]:
    return {"project_id": 1, "name": name, "rooms": rooms}


# --- Relecture du PDF ---------------------------------------------------------------------------


_TEXT_LITERAL = re.compile(rb"\((?:[^()\\]|\\.)*\)", re.DOTALL)


def _decode_literal(raw: bytes) -> str:
    """Une chaîne PDF : les caractères hors ASCII y sont écrits en octal, le reste échappé."""
    octal = re.sub(rb"\\([0-7]{1,3})", lambda found: bytes([int(found.group(1), 8)]), raw)
    plain = re.sub(rb"\\(.)", lambda found: found.group(1), octal)
    # Les polices de base sont posées en WinAnsi, dont cp1252 est la table : c'est elle, et non
    # latin-1, qui rend le tiret cadratin des titres.
    return plain.decode("cp1252", errors="replace")


def _pdf_text(content: bytes) -> str:
    literals: list[str] = []
    for stream in re.finditer(rb"stream\r?\n(.*?)\s*endstream", content, re.DOTALL):
        payload = stream.group(1).strip()
        if payload.endswith(b"~>"):
            payload = base64.a85decode(payload[:-2], ignorechars=b" \t\r\n")
        try:
            payload = zlib.decompress(payload)
        except zlib.error:
            continue
        literals.extend(
            _decode_literal(literal[1:-1]) for literal in _TEXT_LITERAL.findall(payload)
        )
    return "\n".join(literals)


def _page_count(content: bytes) -> int:
    """`/Type /Page` n'est jamais compressé par reportlab : le compte est lisible en brut."""
    return len(re.findall(rb"/Type\s*/Page[^s]", content))


def test_the_reader_of_this_test_file_actually_reads_the_pdf() -> None:
    """Garde-fou du garde-fou : un décodeur muet validerait silencieusement toutes les planches."""
    content = render_project_pdf(_project([_carre_room()]), FIXED_DATE)

    assert "Chantier Dupont" in _pdf_text(content)


# --- Modèle des élévations ----------------------------------------------------------------------


def test_there_is_one_elevation_per_wall() -> None:
    elevations = room_elevations(_carre_room())

    assert [elevation.face_label for elevation in elevations] == ["A", "B", "C", "D"]
    assert [round(elevation.length_cm) for elevation in elevations] == [400, 300, 400, 300]
    assert {elevation.height_cm for elevation in elevations} == {250.0}


def test_the_opening_carries_its_width_height_and_sill() -> None:
    """L'allège est la cote qu'aucun autre document ne donne : c'est elle qu'on vient chercher."""
    opening = room_elevations(_carre_room())[0].openings[0]

    assert opening.label == "Fenêtre"
    assert (opening.x_cm, opening.width_cm, opening.height_cm, opening.sill_cm) == (
        80.0, 120.0, 110.0, 95.0
    )


def test_a_door_has_a_zero_sill() -> None:
    opening = room_elevations(_carre_room())[1].openings[0]

    assert opening.label == "Porte battante"
    assert opening.sill_cm == 0.0


def test_furniture_is_not_taken_for_an_opening() -> None:
    """Un meuble ne perce pas le mur : il ne doit produire ni trou ni cote d'allège."""
    elevation = room_elevations(_carre_room())[0]

    assert [fixture.x_cm for fixture in elevation.fixtures] == [250.0]
    assert len(elevation.openings) == 1
    assert len(elevation.holes) == 1


def test_the_openings_are_ordered_along_the_wall() -> None:
    """L'ordre de saisie n'a aucune raison d'être celui du mur, et une chaîne de cotes se croise."""
    room = _room(
        [
            _wall(
                1, "A", (0, 0, 600, 0),
                [
                    {**FENETRE, "id": 1, "x_offset_cm": 400},
                    {**FENETRE, "id": 2, "x_offset_cm": 40},
                    {**FENETRE, "id": 3, "x_offset_cm": 220},
                ],
            )
        ]
    )

    assert [opening.x_cm for opening in room_elevations(room)[0].openings] == [40.0, 220.0, 400.0]


def test_an_opening_overflowing_the_wall_is_clipped() -> None:
    """Ceinture et bretelles : une donnée qui déborde produit un dessin faux, pas un mur troué."""
    debordante = {**FENETRE, "x_offset_cm": 350, "width_cm": 200}
    room = _room([_wall(1, "A", (0, 0, 400, 0), [debordante])])

    opening = room_elevations(room)[0].openings[0]

    assert (opening.x_cm, opening.width_cm) == (350.0, 50.0)


def test_an_opening_entirely_outside_the_wall_is_dropped() -> None:
    room = _room([_wall(1, "A", (0, 0, 400, 0), [{**FENETRE, "x_offset_cm": 800}])])

    assert room_elevations(room)[0].openings == []


def test_a_wall_without_coordinates_produces_no_sheet() -> None:
    """Un mur non tracé n'a pas d'élévation : une planche vide n'apprend rien à personne."""
    room = _room([{**_wall(1, "A", (0, 0, 0, 0)), "start_x_cm": None, "end_x_cm": None}])

    assert room_elevations(room) == []


def test_the_outline_and_holes_match_the_scene_graph() -> None:
    """Le PDF et la 3D doivent décrire le même mur.

    S'ils divergent, l'écart est invisible : l'artisan lit une cote sur la planche et voit un autre
    percement à l'écran, sans que rien ne signale lequel des deux ment.
    """
    room = _carre_room()
    scene = build_scene_graph({"project_id": 1, "rooms": [room]}, {})
    walls = [node for node in scene["rooms"][0]["nodes"] if node["kind"] == "wall"]

    for elevation, node in zip(room_elevations(room), walls, strict=True):
        assert elevation.face_label == node["face_label"]
        assert elevation.outline == node["outline"]
        assert elevation.holes == node["holes"]


def test_the_elevations_of_the_project_follow_the_rooms() -> None:
    project = _project([_carre_room(), {**_carre_room(), "id": 2, "name": "Cuisine"}])

    assert [elevation.room_name for elevation in wall_elevations(project)] == (
        ["Salle de bains"] * 4 + ["Cuisine"] * 4
    )


# --- Mobilier posé au sol (spec §10, amendement A7) ----------------------------------------------

FIXTURE_MOBILIER = (
    Path(__file__).parent / "geometry" / "fixtures" / "11_mobilier_libre.json"
)


def test_the_floor_fixture_carries_its_centre_its_size_and_its_rotation() -> None:
    """Le plan coté est le seul endroit du dossier où un meuble libre existe : s'il n'y porte pas
    son encombrement réel, il n'y sert à rien."""
    fixture = room_floor_fixtures(_meublee())[0]

    assert fixture.label == "Baignoire"
    assert (fixture.center, fixture.width_cm, fixture.depth_cm) == ((200.0, 150.0), 170.0, 75.0)


def test_the_footprint_is_the_one_the_rest_of_the_project_computes() -> None:
    """Confrontation directe à la fixture de référence 11, qui fige la convention de rotation
    partagée par le scene graph et par `services/faces.py::free_element_footprint`.

    Deux calculs d'une même emprise finissent toujours par diverger, et l'écart serait invisible :
    un meuble tourné à 90° s'imprimerait avec sa largeur et sa profondeur échangées, sans que rien
    ne signale lequel des deux documents ment.
    """
    fixture: dict[str, Any] = json.loads(FIXTURE_MOBILIER.read_text(encoding="utf-8"))
    elements = fixture["input"]["rooms"][0]["elements"]

    for element in elements:
        attendu = fixture["expected_footprints"][str(element["id"])]

        assert _free_footprint(element) == pytest.approx(
            [tuple(corner) for corner in attendu]
        ), f"emprise du meuble {element['id']}"


def test_a_rotated_fixture_does_not_occupy_the_same_footprint_as_a_straight_one() -> None:
    """Le garde-fou du garde-fou : une convention de rotation ignorée passerait le test précédent
    si les deux emprises étaient identiques."""
    droite = _free_footprint({**BAIGNOIRE, "rotation_deg": 0})
    tournee = _free_footprint({**BAIGNOIRE, "rotation_deg": 90})

    assert max(corner[0] for corner in droite) == pytest.approx(285.0)
    assert max(corner[0] for corner in tournee) == pytest.approx(237.5)


def test_a_fixture_without_a_position_is_ignored_rather_than_placed_at_the_origin() -> None:
    """L'origine du plan est presque toujours hors de la pièce : y poser un meuble par défaut
    dessinerait un plan faux sans le dire."""
    room = _room([], elements=[{**BAIGNOIRE, "pos_x_cm": None, "pos_y_cm": None}])

    assert room_floor_fixtures(room) == []


def test_the_floor_fixture_stays_off_the_wall_elevations() -> None:
    """Une élévation est la vue d'un mur : un îlot de cuisine n'est sur aucun mur, et l'y faire
    figurer inventerait un adossement (spec §10, A7)."""
    elevations = room_elevations(_meublee())

    assert [fixture.label for elevation in elevations for fixture in elevation.fixtures] == [
        "Meuble"
    ]


def test_the_room_count_of_elements_no_longer_ignores_the_floor() -> None:
    """La colonne « nombre d'éléments » sous-comptait : elle ne parcourait que les faces, et une
    pièce entièrement meublée au sol s'y annonçait vide."""
    assert furniture_count(_carre_room()) == 1
    assert furniture_count(_meublee()) == 2


def test_an_opening_is_not_a_piece_of_furniture_in_that_count() -> None:
    """La fenêtre et la porte de la pièce de référence sont des percements, pas des meubles."""
    assert furniture_count(_room([_wall(1, "A", (0, 0, 400, 0), [FENETRE, PORTE])])) == 0


def test_the_plan_draws_the_floor_fixture_with_its_name_and_its_footprint() -> None:
    text = _pdf_text(render_project_pdf(_project([_meublee()]), FIXED_DATE))

    assert "Baignoire" in text
    # L'emprise au sol — largeur x profondeur — et non la hauteur, qui ne se lit pas sur un plan.
    assert "170 x 75" in text
    assert "mobilier posé au sol en trait fin" in text


def test_the_room_summary_lists_what_is_standing_on_the_floor() -> None:
    """Le récapitulatif renvoie vers le plan, et non vers une planche : c'est là qu'il se voit."""
    text = _pdf_text(render_project_pdf(_project([_meublee()]), FIXED_DATE))

    assert "— · Posé au sol" in text
    assert "1 meuble(s) au sol" in text


def test_the_cover_page_counts_the_furniture_of_each_room() -> None:
    """Deux meubles dans la pièce : celui du mur A et la baignoire posée au sol."""
    text = _pdf_text(render_project_pdf(_project([_meublee()]), FIXED_DATE))

    assert "Meubles" in text
    assert furniture_count(_meublee()) == 2


def test_a_room_without_floor_fixtures_says_nothing_about_them() -> None:
    """Le dossier d'une pièce sans mobilier au sol ne change pas : pas de ligne vide, pas de
    légende inutile."""
    text = _pdf_text(render_project_pdf(_project([_carre_room()]), FIXED_DATE))

    assert "Posé au sol" not in text
    assert "mobilier posé au sol en trait fin" not in text


# --- Revêtements ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("straight", "pose droite"),
        ("staggered", "pose à coupe de pierre"),
        ("diagonal", "pose en diagonale"),
        ("herringbone", "bâton rompu"),
        ("chevron", "chevron"),
    ],
)
def test_every_laying_pattern_is_written_in_french_on_the_sheet(
    pattern: str, expected: str
) -> None:
    """Les clés de `PATTERN_LABELS` sont les valeurs de `LayingPattern`, et rien d'autre : une
    pose décalée s'imprimait « staggered » sur un document de chantier, faute de traduction."""
    room = _room(
        [_wall(1, "A", (0, 0, 400, 0), covering={"material": "carrelage", "pattern": pattern})]
    )

    assert expected in _pdf_text(render_project_pdf(_project([room]), FIXED_DATE))


# --- Échelle ------------------------------------------------------------------------------------


@pytest.mark.parametrize("length_cm", [120, 400, 750, 1800, 6000])
def test_the_scale_is_always_a_normalised_one(length_cm: int) -> None:
    """« 1:37 » interdit de reporter une cote au double-décimètre : l'échelle reste normalisée."""
    denominator = _normalised_scale(length_cm, 250, 700.0, 320.0)

    assert denominator in SCALE_DENOMINATORS or denominator % 100 == 0


@pytest.mark.parametrize("length_cm", [120, 400, 750, 1800, 6000, 40000])
def test_the_drawing_never_overflows_its_box(length_cm: int) -> None:
    points_per_cm = 72.0 / 2.54
    denominator = _normalised_scale(length_cm, 250, 700.0, 320.0)

    assert length_cm * points_per_cm / denominator <= 700.0
    assert 250 * points_per_cm / denominator <= 320.0


def test_a_longer_wall_gets_a_smaller_scale() -> None:
    assert _normalised_scale(400, 250, 700.0, 320.0) < _normalised_scale(4000, 250, 700.0, 320.0)


# --- Document produit ---------------------------------------------------------------------------


def test_the_document_has_one_page_per_wall() -> None:
    """Page de garde, puis pour chaque pièce le plan et une planche par mur."""
    content = render_project_pdf(_project([_carre_room()]), FIXED_DATE)

    assert _page_count(content) == 1 + 1 + 4


def test_a_second_room_adds_its_plan_and_its_walls() -> None:
    two_rooms = _project([_carre_room(), {**_carre_room(), "id": 2, "name": "Cuisine"}])

    assert _page_count(render_project_pdf(two_rooms, FIXED_DATE)) == 1 + 2 * (1 + 4)


def test_each_wall_has_its_own_titled_sheet() -> None:
    text = _pdf_text(render_project_pdf(_project([_carre_room()]), FIXED_DATE))

    for label in ("A", "B", "C", "D"):
        assert f"Élévation {label} — Salle de bains" in text


def test_every_sheet_states_the_scale_it_was_drawn_at() -> None:
    """Un dessin dont l'échelle n'est pas écrite n'est pas un document de chantier."""
    text = _pdf_text(render_project_pdf(_project([_carre_room()]), FIXED_DATE))

    assert text.count("Échelle 1:") >= 1 + 4
    assert "cotes en centimètres" in text


def test_the_sheet_carries_the_length_and_the_height_of_the_wall() -> None:
    text = _pdf_text(render_project_pdf(_project([_carre_room()]), FIXED_DATE))

    assert "400 cm" in text
    assert "250 cm" in text


def test_the_sheet_carries_the_dimensions_of_each_opening() -> None:
    text = _pdf_text(render_project_pdf(_project([_carre_room()]), FIXED_DATE))

    assert "Fenêtre" in text
    assert "120 x 110 cm" in text
    assert "allège 95 cm" in text
    assert "Porte battante" in text
    assert "allège 0 cm" in text


def test_the_sheet_chains_the_dimensions_along_the_wall() -> None:
    """Trumeau, ouverture, trumeau : 80 + 120 + 200 = les 400 cm du mur A."""
    text = _pdf_text(render_project_pdf(_project([_carre_room()]), FIXED_DATE))
    printed = text.splitlines()

    assert {"80", "120", "200"} <= set(printed)


def test_the_furniture_is_situated_on_the_wall() -> None:
    text = _pdf_text(render_project_pdf(_project([_carre_room()]), FIXED_DATE))

    assert "90 x 85" in text
    assert "Mobilier" in text


def test_the_cover_page_recaps_the_rooms() -> None:
    project = _project([_carre_room(), {**_carre_room(), "id": 2, "name": "Cuisine"}])

    text = _pdf_text(render_project_pdf(project, FIXED_DATE))

    assert "Chantier Dupont" in text
    assert "généré le 08/08/2026" in text
    assert "Salle de bains" in text
    assert "Cuisine" in text
    # 11.31 et non 12.00 : la garde annonce l'aire **nette**, au nu intérieur (390 sur 290), et
    # non l'aire du contour saisi, qui est la ligne médiane des murs. Voir le test suivant.
    assert "11.31 m²" in text
    assert "22.62 m²" in text
    # Les planches annoncées par la garde sont celles qui sont réellement numérotées.
    assert "1 à 5" in text
    assert "planche 5/10" in text


def test_the_cover_area_is_the_one_the_takeoff_will_bill() -> None:
    """La page de garde et le métré ne peuvent pas donner deux surfaces pour la même pièce.

    Elle annonçait l'aire de l'axe des murs, surévaluée de 6 % ici et jusqu'à 20 % avec des murs
    de 30 : l'artisan lisait 12,00 m² sur son dossier de plans et chiffrait 11,31 m² sur son
    devis. Les deux documents lisent désormais la même fonction.
    """
    room = _carre_room()
    scene = build_scene_graph(_project([room]))
    nette_m2 = scene["rooms"][0]["net_floor_area_cm2"] / 10_000

    text = _pdf_text(render_project_pdf(_project([room]), FIXED_DATE))

    assert nette_m2 == pytest.approx(11.31)
    assert f"{nette_m2:.2f} m²" in text
    assert "12.00 m²" not in text


def test_the_thicker_the_walls_the_more_the_centre_line_area_would_overbill() -> None:
    """Garde-fou permanent : aucun chemin du dossier ne doit revenir à l'aire du contour saisi.

    Avec des murs de 30 cm l'écart atteint 20 % — 12,00 m² annoncés pour 9,99 m² réels sur une
    pièce de 400 x 300. À ce niveau ce n'est plus un arrondi, c'est un litige.
    """
    epais = {**_carre_room(), "wall_thickness_cm": 30}

    text = _pdf_text(render_project_pdf(_project([epais]), FIXED_DATE))

    assert "9.99 m²" in text
    assert "12.00 m²" not in text


def test_a_project_without_rooms_still_produces_a_cover() -> None:
    content = render_project_pdf(_project([]), FIXED_DATE)

    assert content.startswith(b"%PDF-")
    assert _page_count(content) == 1
    assert "Ce projet ne contient aucune pièce." in _pdf_text(content)


def test_a_room_without_outline_says_so_rather_than_drawing_nothing() -> None:
    content = render_project_pdf(_project([_room([], polygon=[])]), FIXED_DATE)

    assert "Aucun contour tracé pour cette pièce." in _pdf_text(content)


# --- Filigrane ----------------------------------------------------------------------------------


def test_there_is_no_watermark_by_default() -> None:
    text = _pdf_text(render_project_pdf(_project([_carre_room()]), FIXED_DATE))

    assert "APERÇU" not in text


def test_the_watermark_covers_every_single_page() -> None:
    """Une seule page oubliée suffit à faire un export propre : le filigrane est posé par page."""
    content = render_project_pdf(_project([_carre_room()]), FIXED_DATE, watermark=True)

    assert _pdf_text(content).count("APERÇU") == _page_count(content)


def test_the_watermark_cannot_be_passed_by_position() -> None:
    """Il est décidé par le serveur d'après les droits : un appelant ne doit pas le poser au hasard.

    Argument nommé obligatoire : c'est ce qui interdit qu'une valeur venue de la requête glisse à
    cette place par simple recopie d'ordre des paramètres (`docs/strategie-produit.md` §4).
    """
    with pytest.raises(TypeError):
        render_project_pdf(_project([]), FIXED_DATE, True)  # type: ignore[call-arg]


def test_the_watermarked_export_still_carries_all_its_sheets() -> None:
    """Le fichier filigrané se télécharge vraiment et reste complet : c'est le mur de paiement."""
    plain = render_project_pdf(_project([_carre_room()]), FIXED_DATE)
    marked = render_project_pdf(_project([_carre_room()]), FIXED_DATE, watermark=True)

    assert _page_count(marked) == _page_count(plain)
    assert "Élévation D — Salle de bains" in _pdf_text(marked)
