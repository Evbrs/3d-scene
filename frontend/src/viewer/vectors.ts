/**
 * Conversion des triplets du scene graph en `THREE.Vector3`.
 *
 * TresJS 5 type ses props de position/rotation en `Vector3` et n'accepte plus les tuples bruts.
 * Un helper unique évite de disperser des `new THREE.Vector3(...)` dans les gabarits.
 */
import { Vector3 } from 'three'

export function vec3(triplet: readonly number[]): Vector3 {
  return new Vector3(triplet[0] ?? 0, triplet[1] ?? 0, triplet[2] ?? 0)
}
