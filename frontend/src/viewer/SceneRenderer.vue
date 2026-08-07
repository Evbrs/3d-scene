<script setup lang="ts">
/**
 * Traduction du scene graph en objets Three.js (ticket P7).
 *
 * Ce composant ne calcule **aucune** géométrie métier : il consomme le JSON produit par le
 * backend (`docs/spec-complete.md` §3.1). Les seules opérations ici sont la construction des
 * `THREE.Shape` à partir des contours reçus et l'application des états de visibilité.
 *
 * La scène est organisée en groupes par face, chacun taggé avec son étiquette dans `userData`
 * (§3.4) : c'est ce qui rend l'isolement et la transparence possibles sans reconstruire la
 * géométrie.
 */
import * as THREE from 'three'
import { computed } from 'vue'

import type {
  FurnitureNode,
  HorizontalNode,
  JoineryNode,
  SceneNode,
  SceneRoom,
  WallNode,
} from '@/api/types'
import type { FaceVisibility } from '@/viewer/visibility'
import { buildShape, primitiveGeometry } from '@/viewer/geometry'
import { vec3 } from '@/viewer/vectors'
import { isVisible, materialFor, opacityFor } from '@/viewer/visibility'

const props = defineProps<{
  room: SceneRoom
  visibility: Record<string, FaceVisibility>
}>()

const doubleSide = THREE.DoubleSide

function isWall(node: SceneNode): node is WallNode {
  return node.kind === 'wall'
}

function isHorizontal(node: SceneNode): node is HorizontalNode {
  return node.kind === 'floor' || node.kind === 'ceiling'
}

// Menuiseries et meubles se rendent à l'identique : une recette développée en primitives, posée
// puis tournée. Seule leur provenance diffère — d'où un seul groupe de rendu, et non deux blocs
// jumeaux. Les ignorer, comme c'était le cas, ne plantait pas : la porte manquait simplement.
function isPlacedObject(node: SceneNode): node is FurnitureNode | JoineryNode {
  return node.kind === 'furniture' || node.kind === 'joinery'
}

const walls = computed(() =>
  props.room.nodes.filter(isWall).map((node) => ({
    node,
    geometry: (() => {
      const geometry = new THREE.ExtrudeGeometry(buildShape(node.outline, node.holes), {
        depth: node.extrude_depth_cm,
        bevelEnabled: false,
      })
      geometry.translate(0, 0, node.extrude_offset_cm)
      return geometry
    })(),
  })),
)

const horizontals = computed(() =>
  props.room.nodes.filter(isHorizontal).map((node) => ({
    node,
    geometry: new THREE.ShapeGeometry(buildShape(node.outline, node.holes)),
  })),
)

const furniture = computed(() =>
  props.room.nodes.filter(isPlacedObject).map((node) => ({
    node,
    // Les primitives soustraites sont ignorées tant que le CSG n'est pas activé : les afficher
    // en plein donnerait un volume faux (une baignoire pleine au lieu de creuse).
    parts: node.primitives
      .filter((primitive) => primitive.operation === 'add')
      .map((primitive) => ({
        primitive,
        geometry: primitiveGeometry(primitive.size, primitive.type, primitive.axis),
      })),
  })),
)
</script>

<template>
  <TresGroup name="piece">
    <TresGroup
      v-for="wall in walls"
      :key="`mur-${wall.node.face_id}`"
      :name="`face-${wall.node.face_label}`"
      :visible="isVisible(visibility[wall.node.face_label])"
      :user-data="{ faceLabel: wall.node.face_label }"
    >
      <!-- `extrude_offset_cm` recule l'extrusion d'une demi-épaisseur : sans lui le mur est
           posé d'un seul côté de son axe, et les angles de la pièce ne se rejoignent pas. -->
      <TresMesh
        :position="vec3(wall.node.origin)"
        :rotation-y="wall.node.rotation_y"
        :geometry="wall.geometry"
      >
        <TresMeshStandardMaterial
          :color="materialFor(wall.node.covering.color, '#e4e4e4')"
          :transparent="opacityFor(visibility[wall.node.face_label]) < 1"
          :opacity="opacityFor(visibility[wall.node.face_label])"
          :side="doubleSide"
        />
      </TresMesh>
    </TresGroup>

    <TresGroup
      v-for="surface in horizontals"
      :key="`plan-${surface.node.face_id}`"
      :name="`face-${surface.node.face_label}`"
      :visible="isVisible(visibility[surface.node.face_label])"
      :user-data="{ faceLabel: surface.node.face_label }"
    >
      <TresMesh
        :position="vec3(surface.node.origin)"
        :rotation-x="surface.node.rotation_x"
        :geometry="surface.geometry"
      >
        <TresMeshStandardMaterial
          :color="
            materialFor(
              surface.node.covering.color,
              surface.node.kind === 'floor' ? '#b09371' : '#f2f2f2',
            )
          "
          :transparent="opacityFor(visibility[surface.node.face_label]) < 1"
          :opacity="opacityFor(visibility[surface.node.face_label])"
          :side="doubleSide"
        />
      </TresMesh>
    </TresGroup>

    <!-- Le mobilier suit la visibilité de la face qui le porte. -->
    <TresGroup
      v-for="item in furniture"
      :key="`meuble-${item.node.element_id}`"
      :name="`mobilier-${item.node.element_id}`"
      :position="vec3(item.node.position)"
      :rotation-y="item.node.rotation_y"
      :visible="isVisible(visibility[item.node.face_label])"
      :user-data="{ faceLabel: item.node.face_label, requiresCsg: item.node.requires_csg }"
    >
      <TresMesh
        v-for="(part, index) in item.parts"
        :key="index"
        :position="vec3(part.primitive.offset)"
        :geometry="part.geometry"
      >
        <TresMeshStandardMaterial
          :color="materialFor(part.primitive.color, '#9aa0a6')"
          :transparent="opacityFor(visibility[item.node.face_label]) < 1"
          :opacity="opacityFor(visibility[item.node.face_label])"
        />
      </TresMesh>
    </TresGroup>
  </TresGroup>
</template>
