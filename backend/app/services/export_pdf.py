"""Export PDF d'un projet (`docs/spec-complete.md` §1 et §3.5, phase P9).

Le plan est dessiné en **vectoriel** plutôt qu'à partir d'une capture d'image : il reste net à
n'importe quel zoom et à l'impression, et le PDF ne dépend pas d'un navigateur.

Une page par pièce : le plan coté, puis le détail de chaque face (revêtement et éléments posés),
qui est exactement ce que demande §3.5 (« export PDF détaillé par mur »).
"""

import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

PAGE_SIZE = landscape(A4)
MARGIN = 15 * mm

# Palette sobre et contrastée : le PDF est souvent imprimé en noir et blanc.
WALL_COLOR = colors.HexColor("#1f2933")
DIMENSION_COLOR = colors.HexColor("#4a4a4a")
FILL_COLOR = colors.HexColor("#eef1f4")


def _plan_scale(
    polygon: list[list[float]], width: float, height: float
) -> tuple[float, float, float]:
    """Échelle et décalage pour faire tenir le plan dans la zone, en gardant les proportions."""
    xs = [vertex[0] for vertex in polygon]
    ys = [vertex[1] for vertex in polygon]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    scale = min(width / span_x, height / span_y)
    return scale, min(xs), min(ys)


def _draw_room_plan(
    pdf: pdfcanvas.Canvas, room: dict[str, Any], origin_x: float, origin_y: float,
    width: float, height: float,
) -> None:
    polygon = room["polygon"]
    if len(polygon) < 3:
        pdf.setFillColor(DIMENSION_COLOR)
        pdf.drawString(origin_x, origin_y + height / 2, "Aucun contour tracé pour cette pièce.")
        return

    scale, min_x, min_y = _plan_scale(polygon, width, height)

    def place(vertex: list[float]) -> tuple[float, float]:
        # L'axe y du plan descend (repère écran) ; celui du PDF monte. D'où l'inversion, sans
        # laquelle le plan imprimé serait le miroir vertical de l'éditeur.
        return (
            origin_x + (vertex[0] - min_x) * scale,
            origin_y + height - (vertex[1] - min_y) * scale,
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

    # Cotes et étiquettes de mur.
    walls = [face for face in room["faces"] if face["kind"] == "wall"]
    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(DIMENSION_COLOR)
    for face in walls:
        if face["start_x_cm"] is None:
            continue
        start = place([face["start_x_cm"], face["start_y_cm"]])
        end = place([face["end_x_cm"], face["end_y_cm"]])
        length = (
            (face["end_x_cm"] - face["start_x_cm"]) ** 2
            + (face["end_y_cm"] - face["start_y_cm"]) ** 2
        ) ** 0.5
        pdf.drawCentredString(
            (start[0] + end[0]) / 2,
            (start[1] + end[1]) / 2,
            f"{face['label']} · {round(length)} cm",
        )


def _draw_face_details(
    pdf: pdfcanvas.Canvas, room: dict[str, Any], origin_x: float, top_y: float
) -> None:
    pdf.setFont("Helvetica-Bold", 9)
    pdf.setFillColor(WALL_COLOR)
    pdf.drawString(origin_x, top_y, "Détail par face")

    cursor = top_y - 12
    pdf.setFont("Helvetica", 8)
    for face in room["faces"]:
        covering = face.get("covering") or {}
        parts = [face["label"], face["kind"]]
        if covering.get("material"):
            parts.append(str(covering["material"]))
        if covering.get("color"):
            parts.append(str(covering["color"]))
        pdf.setFillColor(WALL_COLOR)
        pdf.drawString(origin_x, cursor, " · ".join(parts))
        cursor -= 10

        for element in face.get("elements", []):
            pdf.setFillColor(DIMENSION_COLOR)
            pdf.drawString(
                origin_x + 8,
                cursor,
                f"- {element['kind']} : {round(element['width_cm'])} x "
                f"{round(element['height_cm'])} cm à {round(element['x_offset_cm'])} cm",
            )
            cursor -= 9
        cursor -= 3

        if cursor < MARGIN:
            # Plutôt que de déborder silencieusement de la page, on s'arrête en le signalant.
            pdf.drawString(origin_x, cursor, "… (suite non imprimée, page pleine)")
            return


def render_project_pdf(project: dict[str, Any], generated_at: datetime) -> bytes:
    """Génère le PDF complet d'un projet et le renvoie en mémoire.

    `generated_at` est injecté plutôt que lu depuis l'horloge : c'est ce qui rend la sortie
    reproductible et donc testable octet par octet.
    """
    buffer = io.BytesIO()
    pdf = pdfcanvas.Canvas(buffer, pagesize=PAGE_SIZE)
    pdf.setTitle(f"Plan de rénovation — {project['name']}")
    page_width, page_height = PAGE_SIZE

    rooms = project.get("rooms") or []
    if not rooms:
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(MARGIN, page_height - MARGIN, project["name"])
        pdf.setFont("Helvetica", 10)
        pdf.drawString(MARGIN, page_height - MARGIN - 20, "Ce projet ne contient aucune pièce.")
        pdf.showPage()

    for room in rooms:
        pdf.setFont("Helvetica-Bold", 14)
        pdf.setFillColor(WALL_COLOR)
        pdf.drawString(MARGIN, page_height - MARGIN, f"{project['name']} — {room['name']}")

        pdf.setFont("Helvetica", 9)
        pdf.setFillColor(DIMENSION_COLOR)
        area = _polygon_area(room["polygon"]) / 10_000
        pdf.drawString(
            MARGIN,
            page_height - MARGIN - 14,
            f"{area:.2f} m² · murs de {room['wall_thickness_cm']} cm · "
            f"plafond à {room['ceiling_height_cm']} cm · "
            f"généré le {generated_at.strftime('%d/%m/%Y')}",
        )

        plan_width = (page_width - 2 * MARGIN) * 0.55
        plan_height = page_height - 2 * MARGIN - 40
        _draw_room_plan(pdf, room, MARGIN, MARGIN, plan_width, plan_height)
        _draw_face_details(
            pdf, room, MARGIN + plan_width + 10 * mm, page_height - MARGIN - 40
        )

        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def _polygon_area(polygon: list[list[float]]) -> float:
    if len(polygon) < 3:
        return 0.0
    total = 0.0
    for index, vertex in enumerate(polygon):
        following = polygon[(index + 1) % len(polygon)]
        total += vertex[0] * following[1] - following[0] * vertex[1]
    return abs(total) / 2.0
