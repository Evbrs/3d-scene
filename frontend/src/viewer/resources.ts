/**
 * Propriété et libération des ressources GPU d'une scène.
 *
 * Les géométries et les matériaux construits dans un `computed` vivaient hors de l'arbre TresJS :
 * son nettoyage ne les couvrait pas, et chaque rechargement de scène en abandonnait un jeu
 * complet sur la carte graphique. Un pool règle les deux problèmes à la fois : il mémoïse (deux
 * portes identiques ne paient qu'une géométrie) et il sait tout rendre d'un coup.
 *
 * Règle d'usage : **tout** ce que la construction alloue passe par le pool, et une scène ne
 * partage jamais son pool avec la suivante. Les géométries creusées par le CSG font exception —
 * leur cache leur survit délibérément (`viewer/csg.ts`), le pool ne doit pas les libérer.
 */

/** Ce qu'on sait faire d'une ressource : la rendre. */
export interface Disposable {
  dispose: () => void
}

export class ResourcePool {
  private readonly entries = new Map<string, Disposable>()
  private readonly loose: Disposable[] = []
  private hits = 0

  /**
   * Ressource mémoïsée par clé. La fabrique n'est appelée qu'au premier appel : c'est ce qui
   * mutualise les matériaux entre faces et les géométries entre meubles identiques.
   */
  acquire<T extends Disposable>(key: string, create: () => T): T {
    const known = this.entries.get(key)
    if (known) {
      this.hits += 1
      return known as T
    }
    const created = create()
    this.entries.set(key, created)
    return created
  }

  /** Ressource sans clé — une géométrie de mur, unique par construction. */
  own<T extends Disposable>(resource: T): T {
    this.loose.push(resource)
    return resource
  }

  /** Nombre de ressources distinctes détenues. */
  get size(): number {
    return this.entries.size + this.loose.length
  }

  /** Nombre de fois qu'une ressource a été servie depuis le cache plutôt que reconstruite. */
  get reuseCount(): number {
    return this.hits
  }

  /**
   * Libère tout et se vide.
   *
   * Idempotent : un composant démonté puis un watch en vol peuvent l'appeler deux fois, et
   * `dispose()` appelé deux fois sur une même géométrie Three.js émet un évènement de trop.
   */
  dispose(): void {
    this.entries.forEach((resource) => resource.dispose())
    this.entries.clear()
    this.loose.forEach((resource) => resource.dispose())
    this.loose.length = 0
    this.hits = 0
  }
}
