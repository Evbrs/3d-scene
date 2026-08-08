"""Le mobilier dans le métré, et l'aire qu'aucun chiffrage ne doit lire (spec §10, A7).

Deux sujets, réunis ici parce qu'ils portent la même exigence : **ce qui est chiffré doit être ce
qui existe, et rien d'autre**.

1. **Le mobilier était invisible du métré.** Depuis l'amendement A4 un lit, une table ou un îlot
   s'ancrent à la pièce et non à une face ; `build_takeoff` ne retenait que les nœuds porteurs
   d'un revêtement, si bien qu'aucun meuble — ni libre, ni adossé — n'était compté nulle part.
   L'amendement A7 le fait compter à l'unité, sans jamais lui attacher de montant.

2. **`floor_area_cm2` ne doit être lu par aucun chemin de chiffrage.** C'est l'aire de la ligne
   médiane des murs : elle surévalue le sol de 6 % (murs de 10 cm) à 20 % (murs de 30 cm), et la
   facturer, c'est un litige. Le garde-fou est ici **exécutable** et non déclaratif : la valeur est
   empoisonnée dans le scene graph, puis on exige que la sortie soit rigoureusement identique. Le
   jour où quelqu'un la rebranche, ces tests tombent.

Les fixtures de `tests/geometry/fixtures/` font foi (`CLAUDE.md`) : elles sont lues, jamais
écrites.
"""

import json
from pathlib import Path
from typing import Any

from app.geometry.quantities import build_takeoff
from app.geometry.scene import build_scene_graph
from app.services.pricing import PriceReference, PricingOptions, build_quote_lines

FIXTURES = Path(__file__).parent / "geometry" / "fixtures"


def _fixture(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def _catalog(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Le catalogue de la fixture, réindexé par entier : JSON n'a que des clés textuelles."""
    return {int(key): value for key, value in fixture["input"]["furniture_types"].items()}


def _mobilier_libre() -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """La fixture 11 : un lit et une table posés au sol, un radiateur adossé au mur A."""
    fixture = _fixture("11_mobilier_libre.json")
    return fixture["input"], _catalog(fixture)


def _takeoff_of(plan: dict[str, Any], catalog: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return build_takeoff(build_scene_graph(plan, catalog))


def _lines_by_slug(room: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {line["furniture_type_slug"]: line for line in room["furniture"]}


# --- Le mobilier est compté ----------------------------------------------------------------------


def test_the_takeoff_counts_the_furniture_standing_on_the_floor() -> None:
    """Un lit et une table ancrés à la pièce : ils n'apparaissaient jusqu'ici dans aucun
    livrable — ni sur le plan coté, ni au métré."""
    plan, catalog = _mobilier_libre()

    lines = _lines_by_slug(_takeoff_of(plan, catalog)["rooms"][0])

    assert lines["lit"]["count"] == 1
    assert lines["lit"]["free_count"] == 1
    assert lines["lit"]["on_face_count"] == 0
    assert lines["table"]["free_count"] == 1


def test_the_takeoff_also_counts_what_is_backed_against_a_wall() -> None:
    """Le radiateur du mur A n'était pas mieux loti : le métré n'itémisait aucun mobilier."""
    plan, catalog = _mobilier_libre()

    radiateur = _lines_by_slug(_takeoff_of(plan, catalog)["rooms"][0])["radiateur"]

    assert (radiateur["count"], radiateur["free_count"], radiateur["on_face_count"]) == (1, 0, 1)


def test_the_two_anchorings_are_counted_apart_because_they_are_read_apart() -> None:
    """Ce qui est adossé se lit sur une planche d'élévation ; ce qui est au sol, sur le plan coté.

    Un décompte unique obligerait à rouvrir le plan pour savoir où chercher un meuble.
    """
    plan, catalog = _mobilier_libre()

    room = _takeoff_of(plan, catalog)["rooms"][0]

    assert sum(line["free_count"] for line in room["furniture"]) == 2
    assert sum(line["on_face_count"] for line in room["furniture"]) == 1
    assert sum(line["count"] for line in room["furniture"]) == 3


def test_the_dimensions_and_the_footprint_travel_with_the_count() -> None:
    """Un décompte sans gabarit ne se commande pas : deux lits de 140 et de 160 ne s'achètent
    pas ensemble, et c'est l'emprise au sol qui dit si le meuble tient dans la pièce."""
    plan, catalog = _mobilier_libre()

    lit = _lines_by_slug(_takeoff_of(plan, catalog)["rooms"][0])["lit"]

    assert (lit["width_cm"], lit["height_cm"], lit["depth_cm"]) == (140.0, 45.0, 200.0)
    # 140 x 200 cm = 2,8 m².
    assert lit["footprint_m2"] == 2.8


def test_identical_furniture_is_grouped_and_different_sizes_are_not() -> None:
    """Le regroupement porte sur la recette **et** le gabarit : fondre les deux perdrait la seule
    dimension qu'un fournisseur demande."""
    plan, catalog = _mobilier_libre()
    room = json.loads(json.dumps(plan))["rooms"][0]
    jumeau = dict(room["elements"][0], id=9001, pos_x_cm=200, pos_y_cm=150)
    plus_grand = dict(room["elements"][0], id=9002, pos_x_cm=200, pos_y_cm=150, width_cm=160)
    room["elements"] = [*room["elements"], jumeau, plus_grand]

    lignes = [
        line
        for line in _takeoff_of({**plan, "rooms": [room]}, catalog)["rooms"][0]["furniture"]
        if line["furniture_type_slug"] == "lit"
    ]

    assert [(line["width_cm"], line["count"]) for line in lignes] == [(140.0, 2), (160.0, 1)]


def test_an_opening_is_never_counted_as_a_piece_of_furniture() -> None:
    """La menuiserie est déjà comptée comme percement : la reprendre ici la compterait deux fois
    dans un même document."""
    fixture = _fixture("09_metre_mur_deux_ouvertures.json")

    room = _takeoff_of(fixture["input"], {})["rooms"][0]

    assert room["opening_count"] == 2
    assert "furniture" not in room


def test_a_recipe_missing_from_the_catalogue_is_not_invented() -> None:
    """Sans recette, le meuble n'a déjà ni forme 3D ni élévation : le métré ne le compte pas non
    plus. Ce n'est pas un silence du métré, c'est un catalogue incomplet (spec §10, A7)."""
    plan, _catalogue = _mobilier_libre()

    room = _takeoff_of(plan, {})["rooms"][0]

    assert "furniture" not in room


def test_the_project_totals_merge_the_furniture_of_every_room() -> None:
    plan, catalog = _mobilier_libre()
    source = json.loads(json.dumps(plan))
    seconde = json.loads(json.dumps(source["rooms"][0]))
    seconde["id"] = 112
    seconde["name"] = "Chambre 2"
    source["rooms"].append(seconde)

    totals = _takeoff_of(source, catalog)["totals"]

    assert {line["furniture_type_slug"]: line["count"] for line in totals["furniture"]} == {
        "lit": 2,
        "table": 2,
        "radiateur": 2,
    }


def test_the_furniture_schedule_carries_no_money_at_all() -> None:
    """Une recette de `FurnitureType` n'a pas de prix, et le barème de A2 ne connaît que des
    ouvrages au m², au ml et à l'unité de pose. Une fourniture chiffrée demanderait un amendement,
    pas un champ ajouté en silence (question ouverte n° 8)."""
    plan, catalog = _mobilier_libre()

    for line in _takeoff_of(plan, catalog)["rooms"][0]["furniture"]:
        assert not [key for key in line if "cent" in key or "price" in key or "prix" in key]


def test_a_room_without_furniture_carries_no_furniture_key() -> None:
    """L'absence vaut zéro et jamais « inconnu » : la présence de mobilier est toujours
    établissable depuis la scène, contrairement à une surface nette manquante."""
    fixture = _fixture("07_metre_piece_rectangulaire.json")

    measured = _takeoff_of(fixture["input"], {})

    assert "furniture" not in measured["rooms"][0]
    assert "furniture" not in measured["totals"]


def test_the_furniture_schedule_is_deterministic_and_serialisable() -> None:
    """Un métré part dans un PDF et dans une ligne de devis : il doit être stable et
    sérialisable tel quel."""
    plan, catalog = _mobilier_libre()
    scene = build_scene_graph(plan, catalog)

    first = json.dumps(build_takeoff(scene), sort_keys=True, allow_nan=False)
    second = json.dumps(build_takeoff(scene), sort_keys=True, allow_nan=False)

    assert first == second


# --- Aucun chiffrage ne lit l'aire de la ligne médiane --------------------------------------------

# Une valeur absurde et reconnaissable : si elle ressort quelque part, c'est qu'elle a été lue.
AIRE_EMPOISONNEE = 987_654_321.0


def _poisoned(scene: dict[str, Any]) -> dict[str, Any]:
    """Le même scene graph, dont toutes les aires de ligne médiane sont rendues absurdes."""
    copy: dict[str, Any] = json.loads(json.dumps(scene))
    for room in copy["rooms"]:
        room["floor_area_cm2"] = AIRE_EMPOISONNEE
    return copy


def test_the_takeoff_output_does_not_move_when_the_centre_line_area_is_poisoned() -> None:
    """Garde-fou exécutable : le jour où quelqu'un rebranche `floor_area_cm2` sur le métré, ce
    test tombe. Un simple test d'égalité des surfaces ne l'attraperait pas — il resterait vert tant
    que la valeur rebranchée alimenterait un champ qu'il ne regarde pas."""
    fixture = _fixture("07_metre_piece_rectangulaire.json")
    scene = build_scene_graph(fixture["input"])

    honest = json.dumps(build_takeoff(scene), sort_keys=True)
    poisoned = json.dumps(build_takeoff(_poisoned(scene)), sort_keys=True)

    assert honest == poisoned
    assert str(AIRE_EMPOISONNEE) not in poisoned


def test_the_quote_lines_do_not_move_either_when_it_is_poisoned() -> None:
    """Le métré n'est pas le dernier maillon : c'est le devis qui part chez le client."""
    fixture = _fixture("07_metre_piece_rectangulaire.json")
    scene = build_scene_graph(fixture["input"])
    references = {
        "peinture-murs": PriceReference(
            code="peinture-murs",
            label="Peinture murale",
            unit="m2",
            unit_price_cents=1_850,
            vat_rate_bp=1_000,
        )
    }
    options = PricingOptions(
        default_price_codes={"wall": "peinture-murs", "floor": "peinture-murs"}
    )

    honest = build_quote_lines(build_takeoff(scene), references, {}, options)
    poisoned = build_quote_lines(build_takeoff(_poisoned(scene)), references, {}, options)

    assert honest.lines == poisoned.lines
    assert honest.total_ttc_cents == poisoned.total_ttc_cents
    assert honest.total_ht_cents > 0


def test_the_floor_area_billed_is_the_net_one_and_the_gap_is_real() -> None:
    """Sur la pièce de référence l'écart est de 6,1 % ; il monte à 20 % avec des murs de 30 cm.

    C'est ce qui rend le garde-fou ci-dessus autre chose qu'une précaution théorique.
    """
    fixture = _fixture("07_metre_piece_rectangulaire.json")
    epais = json.loads(json.dumps(fixture["input"]))
    epais["rooms"][0]["wall_thickness_cm"] = 30

    scene = build_scene_graph(epais)["rooms"][0]
    measured = build_takeoff(build_scene_graph(epais))["rooms"][0]

    mediane_m2 = scene["floor_area_cm2"] / 10_000
    assert measured["floor_area_m2"] == scene["net_floor_area_cm2"] / 10_000
    assert mediane_m2 / measured["floor_area_m2"] > 1.19
