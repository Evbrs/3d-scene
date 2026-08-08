/**
 * Capture PNG de la vue courante (`docs/spec-complete.md` §3.5).
 *
 * La spec relie explicitement les deux besoins : le même mécanisme, appliqué à chaque vue « par
 * face », produit les images de l'export détaillé par mur. D'où `capturePlan`, qui décrit la
 * séquence de prises de vue sans rien connaître du rendu.
 */

/**
 * Nom de fichier d'une capture.
 *
 * Les noms de pièce viennent de l'utilisateur : « Salle de bain (1er) » donnerait un fichier
 * refusé sur plusieurs systèmes, ou pire, un chemin. On les réduit à un identifiant sobre.
 */
export function captureFileName(roomName: string, viewName: string, extension = 'png'): string {
  const slug = (value: string): string =>
    value
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 48)
  const parts = [slug(roomName), slug(viewName)].filter((part) => part.length > 0)
  return `${parts.join('-') || 'vue'}.${extension}`
}

/** Une prise de vue à réaliser : quel point de vue, et quelle face mettre en avant. */
export interface CaptureShot {
  cameraName: string
  faceLabel: string | null
  fileName: string
}

interface ShotSource {
  name: string
  face_label: string | null
}

/**
 * Le plan de prises de vue de l'export par face : une élévation par mur.
 *
 * Les vues d'ensemble (dessus, isométrique, orbite) sont écartées : elles ne documentent aucun
 * mur en particulier, et le dossier PDF a déjà son plan coté.
 */
export function capturePlan(roomName: string, cameras: readonly ShotSource[]): CaptureShot[] {
  return cameras
    .filter((preset) => preset.face_label !== null)
    .map((preset) => ({
      cameraName: preset.name,
      faceLabel: preset.face_label,
      fileName: captureFileName(roomName, `face-${preset.face_label}`),
    }))
}

/**
 * Déclenche le téléchargement d'une image déjà encodée.
 *
 * Le lien est révoqué au tour suivant seulement : le révoquer dans la foulée du clic annule le
 * téléchargement avant qu'il ait commencé sur plusieurs navigateurs.
 */
export function downloadDataUrl(fileName: string, dataUrl: string): void {
  const link = document.createElement('a')
  link.download = fileName
  link.href = dataUrl
  link.click()
}

/**
 * Attend qu'une image soit réellement dessinée avant de la lire.
 *
 * `preserveDrawingBuffer` garde le tampon entre deux rendus, mais ne rend rien de lui-même : lire
 * le canevas juste après avoir changé de caméra renvoie l'image précédente. Deux trames suffisent
 * à laisser passer la mise à jour de la scène puis son rendu.
 */
export function nextFrames(count = 2): Promise<void> {
  return new Promise((resolve) => {
    let left = count
    const tick = (): void => {
      left -= 1
      if (left <= 0) resolve()
      else requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  })
}
