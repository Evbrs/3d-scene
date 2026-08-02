"""Schémas du catalogue de mobilier paramétrique (`docs/spec-complete.md` §4).

Un `FurnitureType` n'est pas un modèle 3D : c'est une **recette de composition**, une liste de
primitives en coordonnées relatives (fractions de 0 à 1 de la boîte englobante). L'instance
fournit les dimensions réelles, et le rendu remet les primitives à l'échelle.

La validation de la recette est stricte : une recette mal formée ne se voit qu'au moment du
rendu 3D, très loin du point d'insertion. Mieux vaut la refuser à l'écriture.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import FurnitureCategory, PartPrimitive

# Fraction de la boîte englobante. Légèrement débordant autorisé (jusqu'à ±1.5) : la spec §4.1
# donne l'exemple d'une façade de tiroir en `1.01` pour la faire dépasser du corps.
RelativeCoordinate = Annotated[float, Field(ge=-1.5, le=2.5)]
RelativeSize = Annotated[float, Field(gt=0, le=2.5)]
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]

# `"auto"` répartit automatiquement la primitive répétée sur l'axe (spec §4.1, exemple de la
# commode : `"rel_position": [0.5, "auto", 1.01]`).
RelativeAxis = RelativeCoordinate | Literal["auto"]


class Part(BaseModel):
    """Une primitive de la recette."""

    model_config = ConfigDict(extra="forbid")

    type: PartPrimitive
    rel_position: tuple[RelativeAxis, RelativeAxis, RelativeAxis]
    rel_size: tuple[RelativeSize, RelativeSize, RelativeSize]
    color_slot: str = Field(min_length=1, max_length=50)

    # Répétition le long d'un axe : le nombre de tiroirs devient un paramètre d'instance plutôt
    # qu'une nouvelle géométrie codée en dur (spec §4.1).
    repeat_x: Annotated[int, Field(ge=1, le=32)] = 1
    repeat_y: Annotated[int, Field(ge=1, le=32)] = 1
    repeat_z: Annotated[int, Field(ge=1, le=32)] = 1
    gap: Annotated[float, Field(ge=0, le=1)] = 0.0

    # Opération booléenne (spec §4.2) : une vasque est une boîte moins un creux, une baignoire
    # une boîte moins une boîte plus petite. C'est ce qui déclenche le recours au CSG.
    operation: Literal["add", "subtract"] = "add"

    @model_validator(mode="after")
    def _auto_only_on_a_repeated_axis(self) -> "Part":
        """`"auto"` n'a de sens que sur un axe effectivement répété."""
        repeats = (self.repeat_x, self.repeat_y, self.repeat_z)
        axes = zip(self.rel_position, repeats, strict=True)
        for axis_index, (position, repeat) in enumerate(axes):
            if position == "auto" and repeat <= 1:
                axis_name = "xyz"[axis_index]
                raise ValueError(
                    f"`auto` sur l'axe {axis_name} exige repeat_{axis_name} > 1 : sans répétition,"
                    " la position ne peut pas être déduite"
                )
        return self


class FurnitureTypeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=200)
    category: FurnitureCategory
    color_slots: list[str] = Field(min_length=1, max_length=12)
    parts: list[Part] = Field(min_length=1, max_length=64)
    default_width_cm: Annotated[float, Field(gt=0, le=1000)] = 100.0
    default_height_cm: Annotated[float, Field(gt=0, le=1000)] = 100.0
    default_depth_cm: Annotated[float, Field(gt=0, le=1000)] = 50.0

    @model_validator(mode="after")
    def _parts_reference_declared_slots(self) -> "FurnitureTypeBase":
        """Chaque primitive doit pointer sur un emplacement couleur déclaré.

        Sans cette vérification, une faute de frappe dans un `color_slot` produirait une pièce
        grise par défaut au rendu, sans aucune erreur — un défaut très coûteux à diagnostiquer.
        """
        declared = set(self.color_slots)
        if len(declared) != len(self.color_slots):
            raise ValueError("emplacements couleur en double")

        unknown = sorted({part.color_slot for part in self.parts} - declared)
        if unknown:
            raise ValueError(
                f"emplacements couleur non déclarés : {', '.join(unknown)} "
                f"(déclarés : {', '.join(sorted(declared))})"
            )
        return self


class FurnitureTypeCreate(FurnitureTypeBase):
    pass


class FurnitureTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: FurnitureCategory | None = None
    color_slots: list[str] | None = Field(default=None, min_length=1, max_length=12)
    parts: list[Part] | None = Field(default=None, min_length=1, max_length=64)
    default_width_cm: Annotated[float, Field(gt=0, le=1000)] | None = None
    default_height_cm: Annotated[float, Field(gt=0, le=1000)] | None = None
    default_depth_cm: Annotated[float, Field(gt=0, le=1000)] | None = None


class FurnitureTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    category: str
    color_slots: list[str]
    parts: list[dict[str, Any]]
    default_width_cm: float
    default_height_cm: float
    default_depth_cm: float


class FurnitureTypePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[FurnitureTypeRead]
