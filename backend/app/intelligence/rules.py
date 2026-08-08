"""Contrôle de conformité du plan : le moteur de règles (`docs/strategie-produit.md` §3.8).

Il relit le scene graph et signale **ce qu'un homme de métier verrait**. C'est du calcul
géométrique pur sur des données déjà présentes, donc du même bois que le métré : une fonction
pure, sans base de données ni appel sortant, figée par des fixtures calculées à la main.

Quatre exigences pèsent sur chaque anomalie produite, et elles sont ce qui distingue un rapport
utile d'une liste d'alertes qu'on finit par ignorer :

1. **un identifiant stable** (`circulation.passage_etroit`), pour qu'une interface puisse masquer
   une famille de règles, et pour qu'un ticket de support désigne autre chose qu'une phrase ;
2. **une sévérité** — `bloquant`, `avertissement`, `conseil`. Tout mettre au même niveau revient à
   ne rien hiérarchiser : on réserve `bloquant` à ce qui rend le plan irréalisable ou dangereux ;
3. **un message qui dit QUOI et DE COMBIEN.** « Passage trop étroit » ne sert à rien : il faut la
   mesure, le seuil et l'écart, sans quoi l'utilisateur doit refaire le calcul lui-même ;
4. **les entités concernées** — pièce, faces, éléments, et un point du plan — pour que le panneau
   soit cliquable et recentre l'éditeur sur le problème.

Aucun seuil n'est écrit ici : ils vivent tous dans `ergonomy.Thresholds`, avec leur source, et le
rapport les republie pour qu'un lecteur sache sur quoi on s'est prononcé.

**Limite assumée, à ne pas découvrir en production.** Un passage est cherché entre deux obstacles
pris deux à deux, et on ne retient l'interstice que s'il débouche de part et d'autre sur un pas de
dégagement (`Thresholds.passage_probe_cm`). Ce contrôle écarte le joint entre deux meubles alignés
contre un même mur — le cas de loin le plus fréquent — mais pas l'interstice entre deux meubles
posés dos à dos **en plein milieu** d'une pièce, qui sera annoncé comme un passage alors que
personne n'a jamais eu l'intention d'y passer. Le jour où ça gêne, la réponse n'est pas d'ajouter
un cas particulier mais de tester la connexité de l'espace libre, ce qui est un autre lot.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.intelligence.ergonomy import (
    DEFAULT_THRESHOLDS,
    DOOR_KINDS,
    FURNITURE,
    WINDOW,
    Obstacle,
    Opening,
    Point,
    RoomShell,
    Sector,
    Thresholds,
    build_shell,
    footprint_overflow,
    node_footprint,
    normalise,
    obstacles_of,
    openings_of,
    overlap_depth,
    passages,
    polygon_hits_sector,
    sectors_intersect,
    walls_of,
)


class Severity(StrEnum):
    """Trois niveaux, et un seul mérite d'arrêter un chantier."""

    BLOCKING = "bloquant"
    WARNING = "avertissement"
    ADVICE = "conseil"


SEVERITY_RANK: dict[Severity, int] = {
    Severity.BLOCKING: 0,
    Severity.WARNING: 1,
    Severity.ADVICE: 2,
}

# Identifiants stables. Ils sont du contrat : une interface les emploie pour filtrer, un support
# les cite dans un ticket. Renommer une règle est un changement cassant, pas un détail de style.
RULE_PASSAGE = "circulation.passage_etroit"
RULE_ACCESSIBLE_CORRIDOR = "circulation.couloir_non_accessible"
RULE_DOOR_SWING = "porte.debattement_impossible"
RULE_DOOR_HAND = "porte.sens_d_ouverture_impose"
RULE_DOOR_SWINGS_COLLIDE = "porte.debattements_qui_se_percutent"
RULE_DOOR_WIDTH = "porte.largeur_insuffisante"
RULE_CEILING = "piece.hauteur_sous_plafond_insuffisante"
RULE_SILL = "fenetre.allege_sous_le_seuil"
RULE_CORNER = "ouverture.trop_pres_d_un_angle"
RULE_OVERLAP = "mobilier.chevauchement"
RULE_THROUGH_WALL = "mobilier.traverse_un_mur"
RULE_NO_OPENING = "piece.sans_ouverture"
RULE_WET_ROOM = "piece.humide_sans_point_d_eau"

# Intitulé court de chaque règle, pour le regroupement dans le panneau d'inspection. Il vit ici
# et non côté frontend : deux libellés pour une même règle finiraient par diverger.
RULE_TITLES: dict[str, str] = {
    RULE_PASSAGE: "Passage trop étroit",
    RULE_ACCESSIBLE_CORRIDOR: "Passage non accessible",
    RULE_DOOR_SWING: "Débattement de porte impossible",
    RULE_DOOR_HAND: "Sens d'ouverture imposé",
    RULE_DOOR_SWINGS_COLLIDE: "Deux portes se percutent",
    RULE_DOOR_WIDTH: "Largeur de porte insuffisante",
    RULE_CEILING: "Hauteur sous plafond insuffisante",
    RULE_SILL: "Allège de fenêtre sous le seuil",
    RULE_CORNER: "Ouverture trop près d'un angle",
    RULE_OVERLAP: "Meubles qui se chevauchent",
    RULE_THROUGH_WALL: "Meuble qui traverse un mur",
    RULE_NO_OPENING: "Pièce sans ouverture",
    RULE_WET_ROOM: "Pièce humide sans point d'eau",
}

FOCUS_DIGITS = 1
MEASURE_DIGITS = 1


@dataclass(frozen=True)
class Anomaly:
    """Une anomalie, telle que le panneau d'inspection la consomme."""

    rule_id: str
    severity: Severity
    message: str
    room_id: int | None = None
    room_name: str | None = None
    face_labels: tuple[str, ...] = ()
    element_ids: tuple[int, ...] = ()
    focus: Point | None = None
    measured_cm: float | None = None
    threshold_cm: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": RULE_TITLES.get(self.rule_id, self.rule_id),
            "severity": self.severity.value,
            "message": self.message,
            "room_id": self.room_id,
            "room_name": self.room_name,
            "face_labels": list(self.face_labels),
            "element_ids": list(self.element_ids),
            "focus": None
            if self.focus is None
            else [
                round(self.focus[0], FOCUS_DIGITS) + 0.0,
                round(self.focus[1], FOCUS_DIGITS) + 0.0,
            ],
            "measured_cm": None
            if self.measured_cm is None
            else round(self.measured_cm, MEASURE_DIGITS) + 0.0,
            "threshold_cm": None
            if self.threshold_cm is None
            else round(self.threshold_cm, MEASURE_DIGITS) + 0.0,
        }


def _length(value_cm: float) -> str:
    """Une longueur telle qu'on la lit sur un chantier : au centimètre, virgule à la française.

    En dessous de 10 cm on garde une décimale — annoncer « 0 cm » pour un débordement de 4 mm
    ferait passer une vraie anomalie pour un bruit d'arrondi.
    """
    if abs(value_cm) >= 10.0:
        return f"{value_cm:.0f} cm"
    return f"{value_cm:.1f}".replace(".", ",") + " cm"


# --- Circulation -----------------------------------------------------------------------------


def _passage_anomalies(
    room: dict[str, Any], shell: RoomShell, obstacles: list[Obstacle], thresholds: Thresholds
) -> list[Anomaly]:
    minimum = thresholds.effective_passage_min_cm()
    anomalies: list[Anomaly] = []
    for passage in passages(shell, obstacles, thresholds, below_cm=minimum):
        severity = (
            Severity.BLOCKING
            if passage.gap_cm < thresholds.passage_blocking_cm
            else Severity.WARNING
        )
        anomalies.append(
            Anomaly(
                rule_id=RULE_PASSAGE,
                severity=severity,
                message=(
                    f"Passage libre de {_length(passage.gap_cm)} entre {_name(passage.first)} et "
                    f"{_name(passage.second)} : il manque {_length(minimum - passage.gap_cm)} "
                    f"pour atteindre les {_length(minimum)} d'une circulation courante."
                ),
                room_id=room.get("id"),
                room_name=room.get("name"),
                face_labels=_faces_of(passage.first, passage.second),
                element_ids=_elements_of(passage.first, passage.second),
                focus=passage.middle,
                measured_cm=passage.gap_cm,
                threshold_cm=minimum,
            )
        )
    return anomalies


def _accessible_corridor_anomalies(
    room: dict[str, Any], shell: RoomShell, obstacles: list[Obstacle], thresholds: Thresholds
) -> list[Anomaly]:
    """Le palier accessible, et lui seul, applique les 120 cm de couloir.

    Ne se déclenche que si `Thresholds.accessible` est armé : imposer 120 cm à un chantier
    ordinaire noierait le rapport sous des avertissements que personne n'a demandés, et rendrait
    les vraies anomalies invisibles.
    """
    if not thresholds.accessible:
        return []
    return [
        Anomaly(
            rule_id=RULE_ACCESSIBLE_CORRIDOR,
            severity=Severity.ADVICE,
            message=(
                f"Passage de {_length(passage.gap_cm)} entre {_name(passage.first)} et "
                f"{_name(passage.second)} : suffisant en usage courant, mais un couloir "
                f"accessible demande {_length(thresholds.accessible_passage_min_cm)}."
            ),
            room_id=room.get("id"),
            room_name=room.get("name"),
            face_labels=_faces_of(passage.first, passage.second),
            element_ids=_elements_of(passage.first, passage.second),
            focus=passage.middle,
            measured_cm=passage.gap_cm,
            threshold_cm=thresholds.accessible_passage_min_cm,
        )
        for passage in passages(
            shell, obstacles, thresholds, below_cm=thresholds.accessible_passage_min_cm
        )
        if passage.gap_cm >= thresholds.passage_min_cm
    ]


def _name(obstacle: Obstacle) -> str:
    return obstacle.label if obstacle.is_wall else f"« {obstacle.label} »"


def _faces_of(*obstacles: Obstacle) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            obstacle.face_label for obstacle in obstacles if obstacle.face_label is not None
        )
    )


def _elements_of(*obstacles: Obstacle) -> tuple[int, ...]:
    return tuple(
        dict.fromkeys(
            obstacle.element_id for obstacle in obstacles if obstacle.element_id is not None
        )
    )


# --- Débattements de porte -------------------------------------------------------------------


def _blockers(sector: Sector, opening: Opening, obstacles: list[Obstacle]) -> list[str]:
    """Ce que cet arc d'ouverture percute : meubles, puis murs autres que le sien.

    Le mur qui porte la porte est écarté : le vantail s'ouvre **le long de lui**, son premier
    rayon est confondu avec son nu intérieur, et le compter comme obstacle déclarerait toute porte
    impossible. Les autres murs sont bien dans la liste, et il n'y a rien à ajouter pour « l'arc
    sort de la pièce » : le contour est fermé, donc en sortir c'est franchir l'un d'eux.
    """
    return [
        _name(obstacle)
        for obstacle in obstacles
        if not (obstacle.is_wall and obstacle.face_label == opening.wall.label)
        and polygon_hits_sector(sector, obstacle.footprint)
    ]


def _door_anomalies(
    room: dict[str, Any], openings: list[Opening], obstacles: list[Obstacle]
) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    feasible: list[tuple[Opening, list[Sector]]] = []

    for opening in openings:
        swings = opening.swings()
        if not swings:
            continue
        blocked = [(sector, _blockers(sector, opening, obstacles)) for sector in swings]
        free = [sector for sector, hits in blocked if not hits]
        feasible.append((opening, free))

        if not free:
            worst = min(blocked, key=lambda entry: len(entry[1]))
            anomalies.append(
                Anomaly(
                    rule_id=RULE_DOOR_SWING,
                    severity=Severity.BLOCKING,
                    message=(
                        f"Mur {opening.wall.label} : le débattement de la porte "
                        f"({_length(opening.width_cm)} de vantail) percute "
                        f"{_and(sorted({hit for _, hits in blocked for hit in hits}))} "
                        "quelle que soit la main de la porte."
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                    face_labels=(opening.wall.label,),
                    element_ids=() if opening.element_id is None else (opening.element_id,),
                    focus=worst[0].midpoint(),
                    measured_cm=opening.width_cm,
                )
            )
        elif len(free) == 1:
            obstructed = next(hits for sector, hits in blocked if hits)
            anomalies.append(
                Anomaly(
                    rule_id=RULE_DOOR_HAND,
                    severity=Severity.ADVICE,
                    message=(
                        f"Mur {opening.wall.label} : un seul sens d'ouverture est libre. Ferrée de "
                        f"l'autre côté, la porte percuterait {_and(sorted(set(obstructed)))} — le "
                        "plan impose donc la main de la porte, il faut la noter sur la commande."
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                    face_labels=(opening.wall.label,),
                    element_ids=() if opening.element_id is None else (opening.element_id,),
                    focus=free[0].midpoint(),
                    measured_cm=opening.width_cm,
                )
            )

    anomalies.extend(_colliding_swings(room, feasible))
    return anomalies


def _colliding_swings(
    room: dict[str, Any], feasible: list[tuple[Opening, list[Sector]]]
) -> list[Anomaly]:
    """Deux portes qui se percutent **quel que soit** leur ferrage.

    On n'énumère que les ferrages encore possibles après le contrôle précédent, et on ne signale
    que si toutes les combinaisons se percutent : tant qu'il en reste une, le plan est réalisable
    et l'annoncer serait une fausse alerte.
    """
    anomalies: list[Anomaly] = []
    for index, (first, first_free) in enumerate(feasible):
        for second, second_free in feasible[index + 1 :]:
            if not first_free or not second_free:
                continue
            pairs = [
                (left, right)
                for left in first_free
                for right in second_free
                if sectors_intersect(left, right)
            ]
            if len(pairs) < len(first_free) * len(second_free):
                continue
            anomalies.append(
                Anomaly(
                    rule_id=RULE_DOOR_SWINGS_COLLIDE,
                    severity=Severity.BLOCKING,
                    message=(
                        f"Les débattements des portes des murs {first.wall.label} et "
                        f"{second.wall.label} se percutent quel que soit le ferrage retenu : "
                        "une des deux doit devenir coulissante ou changer de place."
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                    face_labels=(first.wall.label, second.wall.label),
                    element_ids=tuple(
                        identifier
                        for identifier in (first.element_id, second.element_id)
                        if identifier is not None
                    ),
                    focus=pairs[0][0].midpoint(),
                )
            )
    return anomalies


def _and(names: list[str]) -> str:
    if not names:
        return "un obstacle"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " et " + names[-1]


# --- Ouvertures ------------------------------------------------------------------------------


def _opening_anomalies(
    room: dict[str, Any], openings: list[Opening], thresholds: Thresholds
) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    for opening in openings:
        faces = (opening.wall.label,)
        elements = () if opening.element_id is None else (opening.element_id,)

        if opening.kind in DOOR_KINDS and opening.width_cm < thresholds.door_clear_width_min_cm:
            blocking = opening.width_cm < thresholds.door_width_blocking_cm
            anomalies.append(
                Anomaly(
                    rule_id=RULE_DOOR_WIDTH,
                    severity=Severity.BLOCKING if blocking else Severity.WARNING,
                    message=(
                        f"Mur {opening.wall.label} : porte de {_length(opening.width_cm)}. "
                        + (
                            "Aucun bloc-porte courant n'est fabriqué en dessous de "
                            f"{_length(thresholds.door_width_blocking_cm)}."
                            if blocking
                            else "Le passage utile d'un logement accessible demande "
                            f"{_length(thresholds.door_clear_width_min_cm)}, il manque "
                            f"{_length(thresholds.door_clear_width_min_cm - opening.width_cm)}."
                        )
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                    face_labels=faces,
                    element_ids=elements,
                    focus=opening.centre,
                    measured_cm=opening.width_cm,
                    threshold_cm=thresholds.door_clear_width_min_cm,
                )
            )

        if opening.kind == WINDOW and opening.sill_cm < thresholds.window_sill_min_cm:
            anomalies.append(
                Anomaly(
                    rule_id=RULE_SILL,
                    severity=Severity.WARNING,
                    message=(
                        f"Mur {opening.wall.label} : allège de fenêtre à "
                        f"{_length(opening.sill_cm)} du sol, soit "
                        f"{_length(thresholds.window_sill_min_cm - opening.sill_cm)} sous le seuil "
                        f"de {_length(thresholds.window_sill_min_cm)}. Une protection contre les "
                        "chutes est exigée dès que la hauteur de chute dépasse 1 m."
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                    face_labels=faces,
                    element_ids=elements,
                    focus=opening.centre,
                    measured_cm=opening.sill_cm,
                    threshold_cm=thresholds.window_sill_min_cm,
                )
            )

        thickness = opening.wall.thickness_cm
        required = max(thickness, thresholds.opening_corner_margin_cm)
        for margin, side in (
            (opening.u_min_cm, "au départ"),
            (opening.wall.length_cm - opening.u_max_cm, "en fin"),
        ):
            if margin >= required:
                continue
            anomalies.append(
                Anomaly(
                    rule_id=RULE_CORNER,
                    severity=Severity.BLOCKING if margin < thickness else Severity.WARNING,
                    message=(
                        f"Mur {opening.wall.label} : le percement s'arrête à "
                        f"{_length(margin)} de l'angle {side} du mur. "
                        + (
                            f"Le mur fait {_length(thickness)} d'épaisseur : le tableau ne tient "
                            "pas, il n'y a plus de matière pour le retour."
                            if margin < thickness
                            else f"Il faut {_length(required)} pour poser le dormant et faire le "
                            "retour du tableau."
                        )
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                    face_labels=faces,
                    element_ids=elements,
                    focus=opening.centre,
                    measured_cm=margin,
                    threshold_cm=required,
                )
            )
    return anomalies


# --- Mobilier --------------------------------------------------------------------------------


def _vertical_span(node: dict[str, Any]) -> tuple[float, float]:
    centre = float(node["position"][1])
    half = float(node["size_cm"][1]) / 2.0
    return (centre - half, centre + half)


def _furniture_anomalies(
    room: dict[str, Any], shell: RoomShell | None, thresholds: Thresholds
) -> list[Anomaly]:
    nodes = [node for node in room.get("nodes") or [] if node["kind"] == FURNITURE]
    footprints = [node_footprint(node) for node in nodes]
    anomalies: list[Anomaly] = []

    for index, node in enumerate(nodes):
        low, high = _vertical_span(node)
        for other_index in range(index + 1, len(nodes)):
            other = nodes[other_index]
            other_low, other_high = _vertical_span(other)
            # Le recouvrement doit être **volumique** : une applique à 1,80 m et un canapé au sol
            # partagent la même emprise au plan sans jamais se toucher. Ne comparer que les
            # emprises ferait de tout meuble adossé un conflit avec ce qui est accroché au-dessus.
            if min(high, other_high) - max(low, other_low) <= thresholds.contact_tolerance_cm:
                continue
            depth = overlap_depth(footprints[index], footprints[other_index])
            if depth <= thresholds.contact_tolerance_cm:
                continue
            anomalies.append(
                Anomaly(
                    rule_id=RULE_OVERLAP,
                    severity=Severity.BLOCKING,
                    message=(
                        f"« {node.get('furniture_type_slug')} » et "
                        f"« {other.get('furniture_type_slug')} » se chevauchent sur "
                        f"{_length(depth)} : les deux ne peuvent pas être posés là."
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                    face_labels=tuple(
                        dict.fromkeys(
                            label
                            for label in (node.get("face_label"), other.get("face_label"))
                            if label is not None
                        )
                    ),
                    element_ids=tuple(
                        identifier
                        for identifier in (node.get("element_id"), other.get("element_id"))
                        if identifier is not None
                    ),
                    focus=_centroid(footprints[index]),
                    measured_cm=depth,
                )
            )

        if shell is None:
            continue
        overflow = footprint_overflow(
            shell.polygon, footprints[index], thresholds.contact_tolerance_cm
        )
        if overflow is not None:
            anomalies.append(
                Anomaly(
                    rule_id=RULE_THROUGH_WALL,
                    severity=Severity.BLOCKING,
                    message=(
                        f"« {node.get('furniture_type_slug')} » déborde de "
                        f"{_length(overflow)} hors du nu intérieur : il traverse un mur."
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                    face_labels=()
                    if node.get("face_label") is None
                    else (str(node["face_label"]),),
                    element_ids=()
                    if node.get("element_id") is None
                    else (int(node["element_id"]),),
                    focus=_centroid(footprints[index]),
                    measured_cm=overflow,
                )
            )
    return anomalies


def _centroid(footprint: list[Point]) -> Point:
    return (
        sum(point[0] for point in footprint) / len(footprint),
        sum(point[1] for point in footprint) / len(footprint),
    )


# --- Pièce -----------------------------------------------------------------------------------


def _room_anomalies(
    room: dict[str, Any], openings: list[Opening], thresholds: Thresholds
) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    height = float(room["ceiling_height_cm"])
    if height < thresholds.ceiling_height_min_cm:
        anomalies.append(
            Anomaly(
                rule_id=RULE_CEILING,
                severity=Severity.BLOCKING,
                message=(
                    f"Hauteur sous plafond de {_length(height)} : il manque "
                    f"{_length(thresholds.ceiling_height_min_cm - height)} pour atteindre les "
                    f"{_length(thresholds.ceiling_height_min_cm)} d'un logement décent."
                ),
                room_id=room.get("id"),
                room_name=room.get("name"),
                measured_cm=height,
                threshold_cm=thresholds.ceiling_height_min_cm,
            )
        )

    if not openings:
        anomalies.append(
            Anomaly(
                rule_id=RULE_NO_OPENING,
                severity=Severity.BLOCKING,
                message=(
                    "Aucune ouverture n'est percée dans cette pièce : elle n'est ni accessible "
                    "ni éclairée."
                ),
                room_id=room.get("id"),
                room_name=room.get("name"),
            )
        )

    name = normalise(str(room.get("name") or ""))
    if any(keyword in name for keyword in thresholds.wet_room_keywords):
        slugs = {
            node.get("furniture_type_slug")
            for node in room.get("nodes") or []
            if node["kind"] == FURNITURE
        }
        if not slugs & set(thresholds.water_point_slugs):
            anomalies.append(
                Anomaly(
                    rule_id=RULE_WET_ROOM,
                    severity=Severity.WARNING,
                    message=(
                        f"« {room.get('name')} » est déclarée humide par son nom mais ne porte "
                        "aucun point d'eau (vasque, baignoire, bac de douche, WC) : le plan ne "
                        "permet ni de chiffrer la plomberie ni de vérifier les dégagements."
                    ),
                    room_id=room.get("id"),
                    room_name=room.get("name"),
                )
            )
    return anomalies


# --- Point d'entrée --------------------------------------------------------------------------


def inspect_room(room: dict[str, Any], thresholds: Thresholds) -> tuple[list[Anomaly], list[str]]:
    """Anomalies d'une seule pièce, et ce qu'on n'a pas su contrôler."""
    warnings: list[str] = []
    walls = walls_of(room)
    shell = build_shell(room)
    openings = openings_of(room, walls)

    anomalies = _room_anomalies(room, openings, thresholds)
    anomalies.extend(_opening_anomalies(room, openings, thresholds))
    anomalies.extend(_furniture_anomalies(room, shell, thresholds))

    if shell is None:
        warnings.append(
            f"pièce « {room.get('name')} » (id {room.get('id')}) : les murs ne se referment pas "
            "en un contour au nu intérieur — circulation, débattements de porte et franchissements "
            "de mur non contrôlés. Aucune règle géométrique n'est prononcée sur un contour deviné."
        )
        return anomalies, warnings

    obstacles = obstacles_of(room, shell, thresholds)
    anomalies.extend(_passage_anomalies(room, shell, obstacles, thresholds))
    anomalies.extend(_accessible_corridor_anomalies(room, shell, obstacles, thresholds))
    anomalies.extend(_door_anomalies(room, openings, obstacles))
    return anomalies, warnings


def inspect_scene(
    scene_graph: dict[str, Any], thresholds: Thresholds | None = None
) -> dict[str, Any]:
    """Contrôle de conformité complet d'un scene graph.

    Forme du résultat, décrite ici parce que c'est le contrat que consomment l'API et le panneau
    d'inspection :

    ```
    project_id
    thresholds  les seuils appliqués, republiés — le rapport dit sur quoi il s'est prononcé
    rooms[]     room_id, name, counts {bloquant, avertissement, conseil}
    anomalies[] rule_id, title, severity, message, room_id, room_name,
                face_labels[], element_ids[], focus [x, y] | null,
                measured_cm | null, threshold_cm | null
    counts      les mêmes compteurs sur tout le projet
    warnings[]  ce qui n'a pas pu être contrôlé, et pourquoi
    ```

    Les anomalies sont rendues **à plat** et triées par sévérité puis par pièce : c'est l'ordre
    dans lequel un artisan veut les traiter, et un panneau n'a alors rien à retrier. Le détail par
    pièce se limite aux compteurs, pour ne pas transporter deux fois la même liste.

    Lève `ValueError` si le scene graph n'est pas en centimètres, comme `build_takeoff` : toutes
    les comparaisons de seuils en dépendent, et se prononcer sur des millimètres pris pour des
    centimètres serait pire que refuser de répondre.
    """
    units = scene_graph.get("units", "cm")
    if units != "cm":
        raise ValueError(f"inspection : scene graph en « {units} », attendu en centimètres")

    applied = thresholds or DEFAULT_THRESHOLDS
    rooms: list[dict[str, Any]] = []
    # Le rang de la pièce entre dans la clé de tri : deux anomalies de même sévérité sortent alors
    # dans l'ordre des pièces du plan, et non dans celui — arbitraire — des messages.
    indexed: list[tuple[int, Anomaly]] = []
    warnings: list[str] = []

    for order, room in enumerate(scene_graph.get("rooms") or []):
        anomalies, room_warnings = inspect_room(room, applied)
        warnings.extend(room_warnings)
        indexed.extend((order, anomaly) for anomaly in anomalies)
        rooms.append(
            {
                "room_id": room.get("id"),
                "name": room.get("name"),
                "counts": _counts(anomalies),
            }
        )

    ordered = [
        anomaly
        for _, anomaly in sorted(
            indexed,
            key=lambda entry: (
                SEVERITY_RANK[entry[1].severity],
                entry[0],
                entry[1].rule_id,
                entry[1].message,
            ),
        )
    ]

    return {
        "project_id": scene_graph.get("project_id"),
        "thresholds": applied.to_dict(),
        "rooms": rooms,
        "anomalies": [anomaly.to_dict() for anomaly in ordered],
        "counts": _counts(ordered),
        "warnings": warnings,
    }


def _counts(anomalies: list[Anomaly]) -> dict[str, int]:
    return {
        severity.value: sum(1 for anomaly in anomalies if anomaly.severity is severity)
        for severity in Severity
    }
