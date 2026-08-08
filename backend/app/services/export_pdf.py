"""Export PDF d'un projet (`docs/spec-complete.md` §1 et §3.5, phase P9).

Le document est dessiné en **vectoriel** plutôt qu'à partir d'une capture d'image : il reste net à
n'importe quel zoom et à l'impression, et le PDF ne dépend pas d'un navigateur.

Il suit l'ordre de lecture d'un dossier de chantier : une page de garde avec le récapitulatif des
pièces, puis, pour chaque pièce, le plan coté suivi d'**une page A4 paysage par mur** — l'élévation
cotée que l'artisan emporte sur le chantier (`docs/strategie-produit.md` §3.3). Les cotes y sont
exprimées en centimètres, et l'échelle est écrite sur chaque planche : un dessin dont l'échelle
n'est pas dite n'est pas un document de chantier, c'est une illustration.

Deux ancrages, deux lectures (spec §10, amendements A4 et A7) : ce qui est adossé à une face se lit
sur la planche d'élévation de ce mur, ce qui est **posé au sol** de la pièce n'est sur aucun mur et
ne se lit donc que sur le plan coté. Les deux sont comptés par le récapitulatif de pièce et par la
page de garde ; aucun des deux ne peut manquer au dossier.
"""

import io
import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm as POINTS_PER_CM
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from app.geometry.scene import OPENING_KINDS, net_floor_area

PAGE_SIZE = landscape(A4)
# Annotées explicitement : `reportlab` est déclaré sans stubs, donc ces longueurs arriveraient
# en `Any` et emporteraient avec elles le typage de toutes les positions calculées.
PAGE_WIDTH: float = PAGE_SIZE[0]
PAGE_HEIGHT: float = PAGE_SIZE[1]
MARGIN: float = 15 * mm

# Palette sobre et contrastée : le PDF est souvent imprimé en noir et blanc.
WALL_COLOR = colors.HexColor("#1f2933")
DIMENSION_COLOR = colors.HexColor("#4a4a4a")
FILL_COLOR = colors.HexColor("#eef1f4")
FURNITURE_COLOR = colors.HexColor("#7b8794")

# Série d'échelles normalisées du bâtiment. Une planche n'est jamais dessinée à une échelle
# bâtarde : sur le chantier la cote se reporte au double-décimètre, et « 1:37 » interdit ce geste.
SCALE_DENOMINATORS = (10, 20, 25, 50, 100, 200, 500)

DIMENSION_FONT_SIZE = 7.0
LABEL_FONT_SIZE = 6.0
# Longueur du trait oblique qui ferme une ligne de cote, convention du dessin de bâtiment.
TICK = 2.5

# Réserves de la planche d'élévation, mesurées depuis les bords utiles.
HEADER_HEIGHT: float = 24 * mm
FOOTER_HEIGHT: float = 22 * mm
# Place réservée aux lignes de cote, sous le mur et à sa gauche.
DIMENSION_GUTTER: float = 16 * mm

OPENING_LABELS = {
    "window": "Fenêtre",
    "door_hinged": "Porte battante",
    "door_sliding": "Porte coulissante",
}
FACE_KIND_LABELS = {"wall": "Mur", "floor": "Sol", "ceiling": "Plafond"}
# Les clés sont les valeurs de `LayingPattern` (`app/models/base.py`), et rien d'autre. « brick »
# y figurait et n'a jamais été une valeur de l'énumération : une pose décalée s'imprimait donc en
# « staggered » sur le dossier de chantier, faute de traduction.
PATTERN_LABELS = {
    "straight": "pose droite",
    "staggered": "pose à coupe de pierre",
    "diagonal": "pose en diagonale",
    "herringbone": "bâton rompu",
    "chevron": "chevron",
}

WATERMARK_TEXT = "APERÇU"


# --- Modèle des élévations (fonctions pures, testables sans PDF) --------------------------------


@dataclass(frozen=True)
class Opening:
    """Une ouverture ramenée dans le repère de l'élévation : origine au départ du mur, au sol."""

    kind: str
    label: str
    x_cm: float
    width_cm: float
    height_cm: float
    # Hauteur d'allège : distance du sol fini au bas de l'ouverture. Nulle pour une porte.
    sill_cm: float


@dataclass(frozen=True)
class Fixture:
    """Un meuble adossé au mur. Il n'est pas coté : il sert à situer le reste par rapport à lui."""

    label: str
    x_cm: float
    bottom_cm: float
    width_cm: float
    height_cm: float


@dataclass(frozen=True)
class FloorFixture:
    """Un meuble posé au sol de la pièce, dans le repère du plan (spec §10, amendements A4 et A7).

    Il n'est adossé à aucune face : aucune planche d'élévation ne le montre, et le plan coté est
    donc le seul endroit du dossier où il existe. `corners` porte l'emprise **après rotation** —
    un lit tourné à 90° n'occupe pas le même rectangle que le même lit droit.
    """

    label: str
    center: tuple[float, float]
    width_cm: float
    depth_cm: float
    rotation_deg: float
    corners: list[tuple[float, float]]


@dataclass(frozen=True)
class WallElevation:
    """Tout ce qu'il faut pour dessiner une planche, sans jamais relire le plan."""

    room_name: str
    face_label: str
    length_cm: float
    height_cm: float
    outline: list[list[float]]
    holes: list[list[list[float]]]
    openings: list[Opening]
    fixtures: list[Fixture]
    covering: dict[str, Any]


def _rect(x_min: float, y_min: float, x_max: float, y_max: float) -> list[list[float]]:
    """Rectangle au format du scene graph : quatre sommets, sens direct, arrondis au même pas."""
    return [
        [round(x_min, 4), round(y_min, 4)],
        [round(x_max, 4), round(y_min, 4)],
        [round(x_max, 4), round(y_max, 4)],
        [round(x_min, 4), round(y_max, 4)],
    ]


def _clip_to_wall(
    element: dict[str, Any], length_cm: float, height_cm: float
) -> tuple[float, float, float, float] | None:
    """Emprise de l'élément bornée au rectangle du mur, ou `None` s'il n'en reste rien.

    Même bornage que `app/geometry/scene.py` et pour la même raison : une donnée qui déborde ne
    produit pas un mur troué mais un dessin dégénéré. Il n'est pas emprunté à ce module parce que
    l'élévation a besoin de l'élément **derrière** le trou — sa nature et son allège — là où le
    scene graph n'émet qu'un rectangle anonyme.
    """
    x_min = max(0.0, float(element["x_offset_cm"]))
    y_min = max(0.0, float(element["y_offset_cm"]))
    x_max = min(length_cm, float(element["x_offset_cm"]) + float(element["width_cm"]))
    y_max = min(height_cm, float(element["y_offset_cm"]) + float(element["height_cm"]))
    if x_max <= x_min or y_max <= y_min:
        return None
    return x_min, y_min, x_max, y_max


def room_elevations(room: dict[str, Any]) -> list[WallElevation]:
    """Une élévation par mur de la pièce, dans l'ordre des faces.

    Les faces sans coordonnées sont ignorées : elles décrivent un mur que l'utilisateur n'a pas
    encore tracé, et une planche vide n'apprendrait rien à personne.
    """
    height_cm = float(room["ceiling_height_cm"])
    elevations: list[WallElevation] = []

    for face in room.get("faces") or []:
        if face["kind"] != "wall":
            continue
        corners = (
            face.get("start_x_cm"),
            face.get("start_y_cm"),
            face.get("end_x_cm"),
            face.get("end_y_cm"),
        )
        if any(value is None for value in corners):
            continue

        length_cm = math.hypot(
            float(face["end_x_cm"]) - float(face["start_x_cm"]),
            float(face["end_y_cm"]) - float(face["start_y_cm"]),
        )

        openings: list[Opening] = []
        fixtures: list[Fixture] = []
        holes: list[list[list[float]]] = []
        for element in face.get("elements") or []:
            clipped = _clip_to_wall(element, length_cm, height_cm)
            if clipped is None:
                continue
            x_min, y_min, x_max, y_max = clipped
            if element["kind"] in OPENING_KINDS:
                openings.append(
                    Opening(
                        kind=element["kind"],
                        label=OPENING_LABELS.get(element["kind"], "Ouverture"),
                        x_cm=x_min,
                        width_cm=x_max - x_min,
                        height_cm=y_max - y_min,
                        sill_cm=y_min,
                    )
                )
                holes.append(_rect(x_min, y_min, x_max, y_max))
            else:
                fixtures.append(
                    Fixture(
                        # Le nom du catalogue quand l'appelant l'a joint (`tasks/exports.py`),
                        # « Meuble » sinon : le modèle reste utilisable sur un plan brut, sans
                        # base de données, comme le reste de ce module.
                        label=str(element.get("furniture_name") or "Meuble"),
                        x_cm=x_min,
                        bottom_cm=y_min,
                        width_cm=x_max - x_min,
                        height_cm=y_max - y_min,
                    )
                )

        elevations.append(
            WallElevation(
                room_name=str(room["name"]),
                face_label=str(face["label"]),
                length_cm=length_cm,
                height_cm=height_cm,
                outline=_rect(0.0, 0.0, length_cm, height_cm),
                holes=holes,
                # Les cotes se lisent de gauche à droite : l'ordre de saisie des ouvertures n'a
                # aucune raison d'être celui du mur, et une chaîne de cotes désordonnée se croise.
                openings=sorted(openings, key=lambda opening: opening.x_cm),
                fixtures=fixtures,
                covering=face.get("covering") or {},
            )
        )

    return elevations


def wall_elevations(project: dict[str, Any]) -> list[WallElevation]:
    """Toutes les élévations du projet, dans l'ordre des pièces puis des faces."""
    return [
        elevation
        for room in project.get("rooms") or []
        for elevation in room_elevations(room)
    ]


def _free_footprint(element: dict[str, Any]) -> list[tuple[float, float]]:
    """Les quatre coins de l'emprise au sol d'un meuble libre, rotation comprise.

    Même convention que `app/services/faces.py::free_element_footprint` et que le scene graph :
    une rotation `R_y(a)` envoie la largeur sur `(cos a, -sin a)` et la profondeur sur
    `(sin a, cos a)` une fois relues dans le plan. Le calcul est refait ici plutôt qu'emprunté
    parce que ce module travaille sur des dictionnaires simples, sans base de données — c'est ce
    qui le rend testable sur un plan brut. Un test confronte les deux à la fixture de référence
    `11_mobilier_libre.json` : les faire diverger, ce serait dessiner un meuble tourné avec sa
    largeur et sa profondeur échangées.
    """
    angle = math.radians(float(element.get("rotation_deg") or 0.0))
    cosine, sine = math.cos(angle), math.sin(angle)
    half_width = float(element["width_cm"]) / 2.0
    half_depth = float(element["depth_cm"]) / 2.0
    center_x = float(element.get("pos_x_cm") or 0.0)
    center_y = float(element.get("pos_y_cm") or 0.0)
    return [
        (
            center_x + along * half_width * cosine + across * half_depth * sine,
            center_y - along * half_width * sine + across * half_depth * cosine,
        )
        for along, across in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    ]


def room_floor_fixtures(room: dict[str, Any]) -> list[FloorFixture]:
    """Le mobilier posé au sol de la pièce — celui qu'aucune élévation ne montre.

    Il se lit dans `room["elements"]`, la clé que `app/api/scene.py::project_to_plain_dict`
    réserve au mobilier libre, et **jamais** dans `room["faces"][*]["elements"]` : confondre les
    deux listes ferait compter deux fois le mobilier d'une pièce.

    Un meuble sans position est ignoré : la contrainte `ck_element_exactly_one_anchor` interdit ce
    cas en base, mais ce module est aussi alimenté par des plans écrits à la main, et lui inventer
    l'origine du plan le poserait au hasard, souvent hors de la pièce.
    """
    fixtures: list[FloorFixture] = []
    for element in room.get("elements") or []:
        if element.get("pos_x_cm") is None or element.get("pos_y_cm") is None:
            continue
        fixtures.append(
            FloorFixture(
                # Le nom du catalogue quand l'appelant l'a joint, « Meuble » sinon — même repli
                # que sur les élévations.
                label=str(element.get("furniture_name") or "Meuble"),
                center=(float(element["pos_x_cm"]), float(element["pos_y_cm"])),
                width_cm=float(element["width_cm"]),
                depth_cm=float(element["depth_cm"]),
                rotation_deg=float(element.get("rotation_deg") or 0.0),
                corners=_free_footprint(element),
            )
        )
    return fixtures


def furniture_count(room: dict[str, Any]) -> int:
    """Nombre de meubles de la pièce, les deux ancrages réunis (spec §10, amendement A7).

    Le récapitulatif de pièce et la page de garde le sous-comptaient : ils ne parcouraient que les
    éléments adossés à une face, si bien qu'un logement entièrement meublé au sol s'annonçait vide.
    """
    on_faces = sum(
        1
        for face in room.get("faces") or []
        for element in face.get("elements") or []
        if element["kind"] not in OPENING_KINDS
    )
    return on_faces + len(room.get("elements") or [])


def _normalised_scale(
    width_cm: float, height_cm: float, box_width: float, box_height: float
) -> int:
    """Dénominateur de la plus grande échelle normalisée qui tienne dans la zone de dessin."""
    needed = max(
        width_cm * POINTS_PER_CM / box_width if box_width > 0 else 0.0,
        height_cm * POINTS_PER_CM / box_height if box_height > 0 else 0.0,
    )
    for denominator in SCALE_DENOMINATORS:
        if denominator >= needed:
            return denominator
    # Au-delà de la série : on arrondit à la centaine supérieure. Une échelle inhabituelle mais
    # ronde et exacte vaut mieux qu'un dessin qui déborde de la feuille.
    return math.ceil(needed / 100.0) * 100


def _floor_area_m2(room: dict[str, Any]) -> float:
    """Surface de sol imprimée : l'aire **nette**, au nu intérieur des murs.

    Ce document et le métré (`GET /projects/{id}/takeoff`) parlent au même artisan de la même
    pièce : ils ne peuvent pas annoncer deux surfaces. L'aire du contour saisi — la ligne médiane
    des murs — surévalue le sol de 6 à 20 %, et c'est elle qui figurait ici. La fonction est donc
    empruntée à `geometry.scene`, qui la calcule déjà pour le scene graph, plutôt que réécrite :
    deux implémentations d'une même mesure finissent toujours par diverger.
    """
    return net_floor_area(room.get("polygon") or [], float(room["wall_thickness_cm"])) / 10_000


def _covering_label(covering: dict[str, Any]) -> str:
    """Libellé lisible d'un revêtement, ou un tiret quand la face n'en porte aucun."""
    parts: list[str] = []
    if covering.get("material"):
        parts.append(str(covering["material"]))
    if covering.get("color"):
        parts.append(str(covering["color"]))
    unit_width = covering.get("unit_width_cm")
    unit_height = covering.get("unit_height_cm")
    if unit_width and unit_height:
        parts.append(f"unité {round(float(unit_width))} x {round(float(unit_height))} cm")
    if covering.get("pattern"):
        parts.append(PATTERN_LABELS.get(str(covering["pattern"]), str(covering["pattern"])))
    return " · ".join(parts) if parts else "—"


# --- Primitives de dessin -----------------------------------------------------------------------


def _tick(pdf: pdfcanvas.Canvas, x: float, y: float) -> None:
    pdf.line(x - TICK, y - TICK, x + TICK, y + TICK)


def _fits(pdf: pdfcanvas.Canvas, text: str, size: float, available: float) -> bool:
    return bool(pdf.stringWidth(text, "Helvetica", size) + 4 <= available)


def _shortened(pdf: pdfcanvas.Canvas, text: str, size: float, available: float) -> str:
    """Texte ramené à la largeur de sa colonne, coupé sur la place réelle et non sur un compte
    de caractères : « faïence beige 30x60 » et « iiiii » n'occupent pas la même largeur."""
    if _fits(pdf, text, size, available):
        return text
    shortened = text
    while shortened and not _fits(pdf, f"{shortened}…", size, available):
        shortened = shortened[:-1]
    return f"{shortened.rstrip()}…"


def _horizontal_dimension(
    pdf: pdfcanvas.Canvas, x_start: float, x_end: float, y: float, text: str
) -> None:
    """Ligne de cote horizontale, valeur au-dessus du trait.

    La valeur est tue quand elle ne tient pas dans son segment : deux cotes qui se chevauchent
    rendent illisible toute la chaîne, et font douter de celles qui étaient justes.
    """
    pdf.setStrokeColor(DIMENSION_COLOR)
    pdf.setFillColor(DIMENSION_COLOR)
    pdf.setLineWidth(0.4)
    pdf.line(x_start, y, x_end, y)
    _tick(pdf, x_start, y)
    _tick(pdf, x_end, y)
    pdf.setFont("Helvetica", DIMENSION_FONT_SIZE)
    if _fits(pdf, text, DIMENSION_FONT_SIZE, abs(x_end - x_start)):
        pdf.drawCentredString((x_start + x_end) / 2, y + 3, text)


def _vertical_dimension(
    pdf: pdfcanvas.Canvas, x: float, y_start: float, y_end: float, text: str
) -> None:
    """Ligne de cote verticale, valeur écrite le long du trait, à sa gauche."""
    pdf.setStrokeColor(DIMENSION_COLOR)
    pdf.setFillColor(DIMENSION_COLOR)
    pdf.setLineWidth(0.4)
    pdf.line(x, y_start, x, y_end)
    _tick(pdf, x, y_start)
    _tick(pdf, x, y_end)
    if not _fits(pdf, text, DIMENSION_FONT_SIZE, abs(y_end - y_start)):
        return
    pdf.saveState()
    pdf.setFont("Helvetica", DIMENSION_FONT_SIZE)
    pdf.translate(x - 2.0, (y_start + y_end) / 2)
    pdf.rotate(90)
    pdf.drawCentredString(0.0, 0.0, text)
    pdf.restoreState()


def _draw_watermark(pdf: pdfcanvas.Canvas) -> None:
    """Filigrane en diagonale des exports du palier gratuit.

    Posé en dernier, donc au-dessus du dessin : appliqué avant, il passerait sous les aplats des
    murs et disparaîtrait précisément des zones qui comptent.
    """
    pdf.saveState()
    pdf.setFillColor(WALL_COLOR)
    pdf.setFillAlpha(0.10)
    pdf.translate(PAGE_WIDTH / 2, PAGE_HEIGHT / 2)
    pdf.rotate(math.degrees(math.atan2(PAGE_HEIGHT, PAGE_WIDTH)))
    pdf.setFont("Helvetica-Bold", 84)
    pdf.drawCentredString(0.0, 0.0, WATERMARK_TEXT)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(0.0, -26.0, "Document non contractuel")
    pdf.restoreState()


def _end_page(pdf: pdfcanvas.Canvas, *, watermark: bool) -> None:
    """Seul point de sortie d'une page : aucune ne peut donc être produite sans son filigrane."""
    if watermark:
        _draw_watermark(pdf)
    pdf.showPage()


def _draw_title_block(pdf: pdfcanvas.Canvas, lines: list[str]) -> None:
    """Cartouche en bas à droite : de quoi identifier la planche une fois détachée du dossier."""
    width = 78 * mm
    height = 18 * mm
    x = PAGE_WIDTH - MARGIN - width
    y = MARGIN

    pdf.setStrokeColor(WALL_COLOR)
    pdf.setLineWidth(0.7)
    pdf.rect(x, y, width, height, stroke=1, fill=0)

    cursor = y + height - 11
    pdf.setFillColor(WALL_COLOR)
    for index, line in enumerate(lines):
        pdf.setFont("Helvetica-Bold" if index == 0 else "Helvetica", 8 if index == 0 else 7)
        pdf.drawString(x + 6, cursor, line)
        cursor -= 10


def _draw_elevation_legend(pdf: pdfcanvas.Canvas) -> None:
    """Légende des trois traits de la planche. Sans elle, le trait fin est illisible."""
    swatch_width = 16.0
    swatch_height = 8.0
    y = MARGIN + FOOTER_HEIGHT - 16
    x = MARGIN

    entries = [
        (FILL_COLOR, WALL_COLOR, 1.2, False, "Mur"),
        (colors.white, WALL_COLOR, 0.9, False, "Ouverture"),
        (None, FURNITURE_COLOR, 0.5, True, "Mobilier"),
    ]
    for fill, stroke, line_width, dashed, label in entries:
        pdf.saveState()
        pdf.setStrokeColor(stroke)
        pdf.setLineWidth(line_width)
        if dashed:
            pdf.setDash(2, 2)
        if fill is not None:
            pdf.setFillColor(fill)
        pdf.rect(x, y, swatch_width, swatch_height, stroke=1, fill=1 if fill is not None else 0)
        pdf.restoreState()

        pdf.setFillColor(DIMENSION_COLOR)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(x + swatch_width + 4, y + 1.5, label)
        x += swatch_width + 8 + pdf.stringWidth(label, "Helvetica", 7) + 14

    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(DIMENSION_COLOR)
    pdf.drawString(
        MARGIN,
        y - 11,
        "Cotes en centimètres. Allège = hauteur du sol fini au bas de l'ouverture.",
    )


# --- Planche d'élévation ------------------------------------------------------------------------


def _draw_openings(
    pdf: pdfcanvas.Canvas,
    elevation: WallElevation,
    origin_x: float,
    origin_y: float,
    scale: float,
) -> None:
    """Un rectangle blanc par ouverture, avec sa hauteur et son allège cotées à l'intérieur."""
    for opening in elevation.openings:
        x = origin_x + opening.x_cm * scale
        y = origin_y + opening.sill_cm * scale
        width = opening.width_cm * scale
        height = opening.height_cm * scale

        pdf.setFillColor(colors.white)
        pdf.setStrokeColor(WALL_COLOR)
        pdf.setLineWidth(0.9)
        pdf.rect(x, y, width, height, stroke=1, fill=1)

        # Chaîne verticale collée au montant gauche : l'allège depuis le sol, puis la hauteur de
        # l'ouverture. C'est l'ordre dans lequel un poseur les reporte sur le mur.
        dimension_x = x + min(12.0, width / 3.0)
        if opening.sill_cm > 0:
            _vertical_dimension(pdf, dimension_x, origin_y, y, f"allège {round(opening.sill_cm)}")
        _vertical_dimension(pdf, dimension_x, y, y + height, f"H {round(opening.height_cm)}")

        _draw_opening_label(pdf, opening, x, y, width, height)


def _draw_opening_label(
    pdf: pdfcanvas.Canvas,
    opening: Opening,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    """Nature et cotes de l'ouverture, au centre du rectangle.

    Doublonne volontairement les lignes de cote : à petite échelle celles-ci perdent leur valeur
    faute de place, et l'allège est justement la cote qu'on ne peut pas deviner du dessin.
    """
    lines = [
        opening.label,
        f"{round(opening.width_cm)} x {round(opening.height_cm)} cm",
        f"allège {round(opening.sill_cm)} cm",
    ]
    printable = [line for line in lines if _fits(pdf, line, LABEL_FONT_SIZE, width)]
    if not printable or height < len(printable) * (LABEL_FONT_SIZE + 1.5):
        return

    pdf.setFillColor(WALL_COLOR)
    step = LABEL_FONT_SIZE + 1.5
    cursor = y + height / 2 + (len(printable) - 1) * step / 2 - LABEL_FONT_SIZE / 2
    for index, line in enumerate(printable):
        pdf.setFont("Helvetica-Bold" if index == 0 else "Helvetica", LABEL_FONT_SIZE)
        pdf.drawCentredString(x + width / 2, cursor, line)
        cursor -= step


def _draw_fixtures(
    pdf: pdfcanvas.Canvas,
    elevation: WallElevation,
    origin_x: float,
    origin_y: float,
    scale: float,
) -> None:
    """Le mobilier, en trait fin par-dessus le mur : il situe les meubles face aux ouvertures."""
    for fixture in elevation.fixtures:
        x = origin_x + fixture.x_cm * scale
        y = origin_y + fixture.bottom_cm * scale
        width = fixture.width_cm * scale
        height = fixture.height_cm * scale

        pdf.saveState()
        pdf.setStrokeColor(FURNITURE_COLOR)
        pdf.setLineWidth(0.5)
        pdf.setDash(2, 2)
        pdf.rect(x, y, width, height, stroke=1, fill=0)
        pdf.restoreState()

        # Le nom d'abord, les dimensions dessous : sur une planche qui porte quatre meubles, les
        # seules cotes ne disent pas lequel va où. Le nom est abandonné en premier quand la place
        # manque — perdre l'encombrement serait perdre la cote, ce qui est plus grave.
        caption = f"{round(fixture.width_cm)} x {round(fixture.height_cm)}"
        lines = [fixture.label, caption] if _fits(
            pdf, fixture.label, LABEL_FONT_SIZE, width
        ) else [caption]
        needed = len(lines) * (LABEL_FONT_SIZE + 1) + 3
        if _fits(pdf, caption, LABEL_FONT_SIZE, width) and height >= needed:
            pdf.setFillColor(FURNITURE_COLOR)
            pdf.setFont("Helvetica", LABEL_FONT_SIZE)
            cursor = y + height / 2 + (len(lines) - 1) * (LABEL_FONT_SIZE + 1) / 2
            for line in lines:
                pdf.drawCentredString(x + width / 2, cursor - LABEL_FONT_SIZE / 2, line)
                cursor -= LABEL_FONT_SIZE + 1


def _draw_length_chain(
    pdf: pdfcanvas.Canvas,
    elevation: WallElevation,
    origin_x: float,
    origin_y: float,
    scale: float,
) -> None:
    """Chaîne de cotes horizontale : trumeaux et ouvertures, puis la longueur totale en dessous.

    Convention du dessin de bâtiment : les cotes partielles au plus près de l'objet, la cote
    d'ensemble en dernière ligne. C'est cette chaîne-là que l'artisan reporte au mètre.
    """
    stops = [0.0]
    for opening in elevation.openings:
        stops.extend([opening.x_cm, opening.x_cm + opening.width_cm])
    stops.append(elevation.length_cm)

    if elevation.openings:
        chain_y = origin_y - 14
        for start, end in itertools.pairwise(stops):
            if end - start <= 0:
                continue
            _horizontal_dimension(
                pdf,
                origin_x + start * scale,
                origin_x + end * scale,
                chain_y,
                str(round(end - start)),
            )

    _horizontal_dimension(
        pdf,
        origin_x,
        origin_x + elevation.length_cm * scale,
        origin_y - 30,
        f"{round(elevation.length_cm)} cm",
    )


def _draw_wall_elevation(
    pdf: pdfcanvas.Canvas,
    elevation: WallElevation,
    project_name: str,
    generated_at: datetime,
    sheet: str,
) -> None:
    """Une planche A4 paysage pour un mur : le rectangle du mur, ses ouvertures et ses cotes."""
    box_left = MARGIN + DIMENSION_GUTTER
    box_bottom = MARGIN + FOOTER_HEIGHT + DIMENSION_GUTTER
    box_width = PAGE_WIDTH - box_left - MARGIN
    box_height = PAGE_HEIGHT - MARGIN - HEADER_HEIGHT - box_bottom

    denominator = _normalised_scale(
        elevation.length_cm, elevation.height_cm, box_width, box_height
    )
    scale = POINTS_PER_CM / denominator
    # Le dessin est centré dans sa zone : à échelle normalisée un mur bas laisse presque la moitié
    # de la feuille vide, et le lecteur croit à une page mal imprimée. Ses lignes de cote ne
    # peuvent pas pour autant descendre dans le pied de page, la gouttière étant réservée sous la
    # zone et le centrage ne faisant que remonter le dessin.
    origin_x = box_left + max(box_width - elevation.length_cm * scale, 0.0) / 2
    origin_y = box_bottom + max(box_height - elevation.height_cm * scale, 0.0) / 2

    pdf.setFillColor(WALL_COLOR)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(
        MARGIN,
        PAGE_HEIGHT - MARGIN - 6,
        f"Élévation {elevation.face_label} — {elevation.room_name}",
    )
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(DIMENSION_COLOR)
    pdf.drawString(
        MARGIN,
        PAGE_HEIGHT - MARGIN - 22,
        f"{project_name} · {len(elevation.openings)} ouverture(s) · "
        f"{len(elevation.fixtures)} meuble(s) · revêtement : {_covering_label(elevation.covering)}",
    )

    pdf.setFillColor(FILL_COLOR)
    pdf.setStrokeColor(WALL_COLOR)
    pdf.setLineWidth(1.2)
    pdf.rect(
        origin_x,
        origin_y,
        elevation.length_cm * scale,
        elevation.height_cm * scale,
        stroke=1,
        fill=1,
    )

    _draw_openings(pdf, elevation, origin_x, origin_y, scale)
    _draw_fixtures(pdf, elevation, origin_x, origin_y, scale)
    _draw_length_chain(pdf, elevation, origin_x, origin_y, scale)
    _vertical_dimension(
        pdf,
        origin_x - 14,
        origin_y,
        origin_y + elevation.height_cm * scale,
        f"{round(elevation.height_cm)} cm",
    )

    pdf.setFont("Helvetica", 8)
    pdf.setFillColor(DIMENSION_COLOR)
    pdf.drawCentredString(
        origin_x + elevation.length_cm * scale / 2,
        origin_y - 44,
        f"Échelle 1:{denominator} — cotes en centimètres",
    )

    _draw_elevation_legend(pdf)
    _draw_title_block(
        pdf,
        [
            project_name,
            f"{elevation.room_name} — face {elevation.face_label}",
            f"Échelle 1:{denominator} · A4 paysage",
            f"{generated_at.strftime('%d/%m/%Y')} · planche {sheet}",
        ],
    )


# --- Plan de la pièce ---------------------------------------------------------------------------


def _draw_floor_fixtures(
    pdf: pdfcanvas.Canvas,
    fixtures: list[FloorFixture],
    place: Callable[[Sequence[float]], tuple[float, float]],
) -> None:
    """Le mobilier posé au sol, en trait fin sur le plan coté (spec §10, amendement A7).

    Dessiné à partir de ses quatre coins **après rotation**, et non d'un rectangle droit centré :
    un lit tourné à 90° occupe l'autre sens de la pièce, et c'est précisément ce que l'artisan
    vient vérifier sur un plan — le passage qui reste autour.
    """
    for fixture in fixtures:
        corners = [place(corner) for corner in fixture.corners]

        path = pdf.beginPath()
        path.moveTo(*corners[0])
        for corner in corners[1:]:
            path.lineTo(*corner)
        path.close()

        pdf.saveState()
        pdf.setStrokeColor(FURNITURE_COLOR)
        pdf.setLineWidth(0.5)
        pdf.setDash(2, 2)
        pdf.drawPath(path, stroke=1, fill=0)
        pdf.restoreState()

        # L'étiquette s'écrit à l'horizontale, au centre de l'emprise : la place disponible est
        # donc l'étendue horizontale du quadrilatère dessiné, et non le côté du meuble. Sur un
        # rectangle, la corde horizontale passant par le centre vaut exactement cette étendue,
        # rotation comprise.
        xs = [corner[0] for corner in corners]
        ys = [corner[1] for corner in corners]
        available = max(xs) - min(xs)
        centre_x = sum(xs) / 4
        centre_y = sum(ys) / 4
        caption = f"{round(fixture.width_cm)} x {round(fixture.depth_cm)}"
        # Le nom est abandonné en premier quand la place manque : perdre l'encombrement serait
        # perdre la cote, ce qui est plus grave. Même arbitrage que sur les élévations.
        lines = (
            [fixture.label, caption]
            if _fits(pdf, fixture.label, LABEL_FONT_SIZE, available)
            else [caption]
        )
        needed = len(lines) * (LABEL_FONT_SIZE + 1) + 3
        if not _fits(pdf, caption, LABEL_FONT_SIZE, available) or max(ys) - min(ys) < needed:
            continue

        pdf.setFillColor(FURNITURE_COLOR)
        pdf.setFont("Helvetica", LABEL_FONT_SIZE)
        cursor = centre_y + (len(lines) - 1) * (LABEL_FONT_SIZE + 1) / 2 - LABEL_FONT_SIZE / 2
        for line in lines:
            pdf.drawCentredString(centre_x, cursor, line)
            cursor -= LABEL_FONT_SIZE + 1


def _draw_room_plan(
    pdf: pdfcanvas.Canvas, room: dict[str, Any], fixtures: list[FloorFixture],
    origin_x: float, origin_y: float, width: float, height: float,
) -> int | None:
    """Plan coté de la pièce. Renvoie le dénominateur de l'échelle employée, ou `None` si vide."""
    polygon = room.get("polygon") or []
    if len(polygon) < 3:
        pdf.setFillColor(DIMENSION_COLOR)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(origin_x, origin_y + height / 2, "Aucun contour tracé pour cette pièce.")
        return None

    xs = [vertex[0] for vertex in polygon]
    ys = [vertex[1] for vertex in polygon]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    denominator = _normalised_scale(span_x, span_y, width, height)
    scale = POINTS_PER_CM / denominator
    min_x, min_y = min(xs), min(ys)
    drawn_width = span_x * scale
    drawn_height = span_y * scale
    left = origin_x + max(width - drawn_width, 0.0) / 2
    bottom = origin_y + max(height - drawn_height, 0.0) / 2

    def place(vertex: Sequence[float]) -> tuple[float, float]:
        # L'axe y du plan descend (repère écran) ; celui du PDF monte. D'où l'inversion, sans
        # laquelle le plan imprimé serait le miroir vertical de l'éditeur.
        return (
            left + (vertex[0] - min_x) * scale,
            bottom + drawn_height - (vertex[1] - min_y) * scale,
        )

    path = pdf.beginPath()
    first = place(polygon[0])
    path.moveTo(*first)
    for vertex in polygon[1:]:
        path.lineTo(*place(vertex))
    path.close()

    pdf.setFillColor(FILL_COLOR)
    pdf.setStrokeColor(WALL_COLOR)
    pdf.setLineWidth(1.2)
    pdf.drawPath(path, stroke=1, fill=1)

    # Posé après l'aplat du sol et avant les étiquettes de mur : dessiné avant, il passerait sous
    # l'aplat et disparaîtrait ; dessiné après, il barrerait les cotes des murs.
    _draw_floor_fixtures(pdf, fixtures, place)

    centre = (
        sum(place(vertex)[0] for vertex in polygon) / len(polygon),
        sum(place(vertex)[1] for vertex in polygon) / len(polygon),
    )

    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(DIMENSION_COLOR)
    for face in room.get("faces") or []:
        if face["kind"] != "wall" or face.get("start_x_cm") is None:
            continue
        start = place([face["start_x_cm"], face["start_y_cm"]])
        end = place([face["end_x_cm"], face["end_y_cm"]])
        length = math.hypot(
            float(face["end_x_cm"]) - float(face["start_x_cm"]),
            float(face["end_y_cm"]) - float(face["start_y_cm"]),
        )
        middle_x = (start[0] + end[0]) / 2
        middle_y = (start[1] + end[1]) / 2
        caption = f"{face['label']} · {round(length)} cm"
        # L'étiquette est décalée vers l'intérieur de la pièce : posée sur le milieu du segment,
        # elle est barrée par le trait du mur et devient illisible à l'impression. Le décalage
        # tient compte de la demi-largeur du texte, sans quoi un mur vertical le recoupe quand
        # même — l'étiquette y est centrée horizontalement sur le trait.
        toward_x = centre[0] - middle_x
        toward_y = centre[1] - middle_y
        span = math.hypot(toward_x, toward_y) or 1.0
        clearance = 8 + abs(toward_x / span) * pdf.stringWidth(caption, "Helvetica", 7) / 2
        pdf.drawCentredString(
            middle_x + toward_x / span * clearance,
            middle_y + toward_y / span * clearance - 2.5,
            caption,
        )

    return denominator


def _draw_room_summary(
    pdf: pdfcanvas.Canvas,
    room: dict[str, Any],
    fixtures: list[FloorFixture],
    sheets: dict[str, str],
    plan_sheet: str,
    origin_x: float,
    top_y: float,
    available_height: float,
) -> None:
    """Récapitulatif par ancrage : revêtement, éléments posés et numéro de planche.

    Une ligne par face, puis une ligne pour ce qui est **posé au sol** (spec §10, amendement A7).
    Sans elle le tableau sous-comptait le mobilier d'une pièce entière : il ne parcourait que les
    éléments adossés à une face, et un logement meublé au sol s'y annonçait vide.
    """
    pdf.setFont("Helvetica-Bold", 9)
    pdf.setFillColor(WALL_COLOR)
    pdf.drawString(origin_x, top_y, "Détail par face")

    columns = (origin_x, origin_x + 70, origin_x + 190, origin_x + 232)
    cursor = top_y - 14
    pdf.setFont("Helvetica-Bold", 7)
    pdf.setFillColor(DIMENSION_COLOR)
    for label, column in zip(("Face", "Revêtement", "Posés", "Planche"), columns, strict=True):
        pdf.drawString(column, cursor, label)
    cursor -= 4
    pdf.setStrokeColor(DIMENSION_COLOR)
    pdf.setLineWidth(0.4)
    pdf.line(origin_x, cursor, columns[-1] + 44, cursor)
    cursor -= 10

    # Une ligne est réservée au mobilier posé au sol : c'est la seule que le dossier ne rattrape
    # nulle part ailleurs, et un tableau de faces qui déborde ne doit pas l'évincer.
    floor = top_y - available_height + (10 if fixtures else 0)
    for face in room.get("faces") or []:
        if cursor < floor:
            pdf.setFont("Helvetica-Oblique", 7)
            pdf.drawString(origin_x, cursor, "Faces suivantes : voir leur planche d'élévation.")
            cursor -= 10
            break
        pdf.setFont("Helvetica", 7)
        pdf.setFillColor(WALL_COLOR)
        kind = FACE_KIND_LABELS.get(face["kind"], face["kind"])
        pdf.drawString(columns[0], cursor, f"{face['label']} · {kind}")
        pdf.setFillColor(DIMENSION_COLOR)
        covering = _covering_label(face.get("covering") or {})
        pdf.drawString(
            columns[1], cursor, _shortened(pdf, covering, 7, columns[2] - columns[1] - 8)
        )
        pdf.drawString(columns[2], cursor, str(len(face.get("elements") or [])))
        pdf.drawString(columns[3], cursor, sheets.get(face["label"], "—"))
        cursor -= 10

    if not fixtures:
        return
    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(WALL_COLOR)
    pdf.drawString(columns[0], cursor, "— · Posé au sol")
    pdf.setFillColor(DIMENSION_COLOR)
    names = ", ".join(sorted({fixture.label for fixture in fixtures}))
    pdf.drawString(columns[1], cursor, _shortened(pdf, names, 7, columns[2] - columns[1] - 8))
    pdf.drawString(columns[2], cursor, str(len(fixtures)))
    # Le plan et non une élévation : un meuble libre n'est sur aucun mur, et c'est précisément ce
    # que cette colonne doit dire à qui cherche où le voir.
    pdf.drawString(columns[3], cursor, plan_sheet)


def _draw_room_page(
    pdf: pdfcanvas.Canvas,
    project_name: str,
    room: dict[str, Any],
    sheets: dict[str, str],
    generated_at: datetime,
    sheet: str,
) -> None:
    pdf.setFont("Helvetica-Bold", 15)
    pdf.setFillColor(WALL_COLOR)
    pdf.drawString(MARGIN, PAGE_HEIGHT - MARGIN - 6, f"Plan — {room['name']}")

    fixtures = room_floor_fixtures(room)

    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(DIMENSION_COLOR)
    area = _floor_area_m2(room)
    header = (
        f"{project_name} · {area:.2f} m² · murs de {room['wall_thickness_cm']} cm · "
        f"plafond à {room['ceiling_height_cm']} cm"
    )
    if fixtures:
        header += f" · {len(fixtures)} meuble(s) au sol"
    pdf.drawString(MARGIN, PAGE_HEIGHT - MARGIN - 22, header)

    plan_width = (PAGE_WIDTH - 2 * MARGIN) * 0.55
    plan_bottom = MARGIN + FOOTER_HEIGHT
    plan_height = PAGE_HEIGHT - MARGIN - HEADER_HEIGHT - plan_bottom
    denominator = _draw_room_plan(
        pdf, room, fixtures, MARGIN, plan_bottom, plan_width, plan_height
    )

    summary_top = PAGE_HEIGHT - MARGIN - HEADER_HEIGHT
    _draw_room_summary(
        pdf,
        room,
        fixtures,
        sheets,
        sheet,
        MARGIN + plan_width + 10 * mm,
        summary_top,
        summary_top - plan_bottom,
    )

    if denominator is not None:
        pdf.setFont("Helvetica", 8)
        pdf.setFillColor(DIMENSION_COLOR)
        caption = f"Échelle 1:{denominator} — cotes en centimètres"
        if fixtures:
            # Le trait fin du mobilier n'est expliqué nulle part sur cette page : la planche
            # d'élévation a sa légende, le plan n'en a pas.
            caption += " — mobilier posé au sol en trait fin"
        pdf.drawCentredString(MARGIN + plan_width / 2, MARGIN + FOOTER_HEIGHT - 16, caption)

    _draw_title_block(
        pdf,
        [
            project_name,
            f"{room['name']} — plan",
            f"Échelle 1:{denominator} · A4 paysage" if denominator else "Contour non tracé",
            f"{generated_at.strftime('%d/%m/%Y')} · planche {sheet}",
        ],
    )


# --- Page de garde ------------------------------------------------------------------------------


def _draw_cover_header(pdf: pdfcanvas.Canvas, project_name: str, generated_at: datetime) -> float:
    pdf.setFont("Helvetica-Bold", 22)
    pdf.setFillColor(WALL_COLOR)
    pdf.drawString(MARGIN, PAGE_HEIGHT - MARGIN - 12, project_name)

    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(DIMENSION_COLOR)
    pdf.drawString(
        MARGIN,
        PAGE_HEIGHT - MARGIN - 32,
        "Dossier de plans et d'élévations cotées · "
        f"généré le {generated_at.strftime('%d/%m/%Y')}",
    )
    return PAGE_HEIGHT - MARGIN - 60


COVER_COLUMNS = (0.0, 300.0, 400.0, 470.0, 560.0, 640.0, 740.0)
COVER_HEADINGS = (
    "Pièce",
    "Surface au sol",
    "Sous plafond",
    "Murs",
    "Ouvertures",
    # Les deux ancrages réunis (spec §10, amendement A7) : cette colonne ne comptait que ce qui
    # est adossé à une face, et une pièce entièrement meublée au sol s'y annonçait vide.
    "Meubles",
    "Planches",
)


def _draw_cover_table_header(pdf: pdfcanvas.Canvas, cursor: float) -> float:
    pdf.setFont("Helvetica-Bold", 8)
    pdf.setFillColor(DIMENSION_COLOR)
    for heading, offset in zip(COVER_HEADINGS, COVER_COLUMNS, strict=True):
        if offset == 0.0:
            pdf.drawString(MARGIN, cursor, heading)
        else:
            pdf.drawRightString(MARGIN + offset, cursor, heading)
    cursor -= 5
    pdf.setStrokeColor(DIMENSION_COLOR)
    pdf.setLineWidth(0.5)
    pdf.line(MARGIN, cursor, MARGIN + COVER_COLUMNS[-1], cursor)
    return cursor - 13


def _draw_cover(
    pdf: pdfcanvas.Canvas,
    project_name: str,
    schedule: list[tuple[dict[str, Any], list[WallElevation], int, int]],
    generated_at: datetime,
    *,
    watermark: bool,
) -> None:
    """Page de garde : nom du projet, date et récapitulatif des pièces.

    Le tableau se poursuit sur une page supplémentaire plutôt que de s'interrompre : un
    récapitulatif tronqué au milieu d'un logement est pire que pas de récapitulatif du tout.
    """
    cursor = _draw_cover_header(pdf, project_name, generated_at)

    if not schedule:
        pdf.setFont("Helvetica", 10)
        pdf.setFillColor(WALL_COLOR)
        pdf.drawString(MARGIN, cursor, "Ce projet ne contient aucune pièce.")
        _end_page(pdf, watermark=watermark)
        return

    cursor = _draw_cover_table_header(pdf, cursor)
    total_area = 0.0
    total_openings = 0
    total_furniture = 0

    for room, elevations, first_sheet, last_sheet in schedule:
        if cursor < MARGIN + FOOTER_HEIGHT:
            _end_page(pdf, watermark=watermark)
            cursor = _draw_cover_table_header(pdf, PAGE_HEIGHT - MARGIN - 12)

        area = _floor_area_m2(room)
        openings = sum(len(elevation.openings) for elevation in elevations)
        furniture = furniture_count(room)
        total_area += area
        total_openings += openings
        total_furniture += furniture

        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(WALL_COLOR)
        pdf.drawString(MARGIN, cursor, str(room["name"]))
        pdf.setFillColor(DIMENSION_COLOR)
        pdf.drawRightString(MARGIN + COVER_COLUMNS[1], cursor, f"{area:.2f} m²")
        pdf.drawRightString(
            MARGIN + COVER_COLUMNS[2], cursor, f"{round(float(room['ceiling_height_cm']))} cm"
        )
        pdf.drawRightString(MARGIN + COVER_COLUMNS[3], cursor, str(len(elevations)))
        pdf.drawRightString(MARGIN + COVER_COLUMNS[4], cursor, str(openings))
        pdf.drawRightString(MARGIN + COVER_COLUMNS[5], cursor, str(furniture))
        pdf.drawRightString(
            MARGIN + COVER_COLUMNS[6],
            cursor,
            str(first_sheet) if first_sheet == last_sheet else f"{first_sheet} à {last_sheet}",
        )
        cursor -= 13

    cursor -= 4
    pdf.setStrokeColor(DIMENSION_COLOR)
    pdf.setLineWidth(0.5)
    pdf.line(MARGIN, cursor, MARGIN + COVER_COLUMNS[-1], cursor)
    cursor -= 13
    pdf.setFont("Helvetica-Bold", 9)
    pdf.setFillColor(WALL_COLOR)
    pdf.drawString(MARGIN, cursor, f"{len(schedule)} pièce(s)")
    pdf.drawRightString(MARGIN + COVER_COLUMNS[1], cursor, f"{total_area:.2f} m²")
    pdf.drawRightString(MARGIN + COVER_COLUMNS[4], cursor, str(total_openings))
    pdf.drawRightString(MARGIN + COVER_COLUMNS[5], cursor, str(total_furniture))

    pdf.setFont("Helvetica-Oblique", 8)
    pdf.setFillColor(DIMENSION_COLOR)
    pdf.drawString(
        MARGIN,
        MARGIN + 12,
        "Surfaces nettes, mesurées au nu intérieur des murs — celles du métré et du devis. "
        "Cotes en centimètres.",
    )
    _end_page(pdf, watermark=watermark)


# --- Assemblage du document ---------------------------------------------------------------------


def render_project_pdf(
    project: dict[str, Any], generated_at: datetime, *, watermark: bool = False
) -> bytes:
    """Génère le PDF complet d'un projet et le renvoie en mémoire.

    `generated_at` est injecté plutôt que lu depuis l'horloge : c'est ce qui rend la sortie
    reproductible et donc testable octet par octet.

    `watermark` est un argument nommé, décidé par l'appelant **serveur** d'après les droits du
    compte, et n'est jamais lu depuis la requête (`docs/strategie-produit.md` §4) : un filigrane
    que le client peut désactiver ne protège rien. Il vaut `False` par défaut, de sorte qu'aucun
    appelant existant ne l'obtienne par accident — c'est l'offre qui doit le demander.
    """
    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=PAGE_SIZE)
    pdf.setTitle(f"Plan de rénovation — {project['name']}")
    pdf.setSubject("Plan coté et élévations par mur")

    rooms = project.get("rooms") or []

    # Les planches sont numérotées avant d'être dessinées : la page de garde annonce à quelle
    # planche se trouve chaque pièce, et elle est imprimée en premier. Elle ne se compte pas
    # elle-même, sinon sa propre pagination décalerait tous les numéros qu'elle annonce.
    schedule: list[tuple[dict[str, Any], list[WallElevation], int, int]] = []
    number = 1
    for room in rooms:
        elevations = room_elevations(room)
        schedule.append((room, elevations, number, number + len(elevations)))
        number += 1 + len(elevations)
    total_sheets = number - 1

    _draw_cover(pdf, str(project["name"]), schedule, generated_at, watermark=watermark)

    for room, elevations, first_sheet, _last_sheet in schedule:
        sheets = {
            elevation.face_label: f"{first_sheet + 1 + index}/{total_sheets}"
            for index, elevation in enumerate(elevations)
        }
        _draw_room_page(
            pdf,
            str(project["name"]),
            room,
            sheets,
            generated_at,
            f"{first_sheet}/{total_sheets}",
        )
        _end_page(pdf, watermark=watermark)

        for index, elevation in enumerate(elevations):
            _draw_wall_elevation(
                pdf,
                elevation,
                str(project["name"]),
                generated_at,
                f"{first_sheet + 1 + index}/{total_sheets}",
            )
            _end_page(pdf, watermark=watermark)

    pdf.save()
    return buffer.getvalue()
