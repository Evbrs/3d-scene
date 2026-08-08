/**
 * Raccourcis clavier de l'éditeur, et leur documentation.
 *
 * La liste `SHORTCUTS` **est** l'aide affichée : une aide écrite à part diverge du code au
 * premier ajout, et un raccourci non documenté n'existe pas pour l'utilisateur. Les prédicats
 * sont des fonctions pures d'un objet ressemblant à un `KeyboardEvent`, donc testables sans DOM.
 *
 * Accessibilité (WCAG AAA) : chaque geste à la souris de l'éditeur a son entrée ici. Un canevas
 * n'ouvre aucun chemin clavier par lui-même ; ce sont ces touches, les listes latérales et les
 * champs numériques qui le font.
 */

/** Le sous-ensemble d'un évènement clavier dont dépendent les prédicats ci-dessous. */
export interface KeyStroke {
  key: string
  ctrlKey?: boolean
  metaKey?: boolean
  shiftKey?: boolean
  altKey?: boolean
}

export interface Shortcut {
  groupe: string
  touches: string
  libelle: string
}

/**
 * Le modificateur d'application.
 *
 * `metaKey` sur macOS, `ctrlKey` ailleurs — mais on accepte les deux plutôt que de renifler la
 * plateforme : un clavier externe branché sur une tablette envoie l'un ou l'autre, et se tromper
 * rend Ctrl+Z muet sans aucun message.
 */
function commande(event: KeyStroke): boolean {
  return event.ctrlKey === true || event.metaKey === true
}

export function matchesUndo(event: KeyStroke): boolean {
  return commande(event) && event.key.toLowerCase() === 'z' && event.shiftKey !== true
}

/** Ctrl+Maj+Z, et Ctrl+Y pour les habitudes venues du dessin technique sous Windows. */
export function matchesRedo(event: KeyStroke): boolean {
  if (!commande(event)) return false
  const key = event.key.toLowerCase()
  return (key === 'z' && event.shiftKey === true) || key === 'y'
}

export function matchesCopy(event: KeyStroke): boolean {
  return commande(event) && event.key.toLowerCase() === 'c'
}

export function matchesPaste(event: KeyStroke): boolean {
  return commande(event) && event.key.toLowerCase() === 'v'
}

export function matchesDuplicate(event: KeyStroke): boolean {
  return commande(event) && event.key.toLowerCase() === 'd'
}

export function matchesSelectAll(event: KeyStroke): boolean {
  return commande(event) && event.key.toLowerCase() === 'a'
}

export function matchesDelete(event: KeyStroke): boolean {
  return !commande(event) && (event.key === 'Delete' || event.key === 'Backspace')
}

/** Déplacement au clavier : le pendant du glisser, sans souris. Rend le vecteur, en pas de grille. */
export function arrowStep(event: KeyStroke): { dx: number; dy: number } | null {
  switch (event.key) {
    case 'ArrowLeft':
      return { dx: -1, dy: 0 }
    case 'ArrowRight':
      return { dx: 1, dy: 0 }
    case 'ArrowUp':
      return { dx: 0, dy: -1 }
    case 'ArrowDown':
      return { dx: 0, dy: 1 }
    default:
      return null
  }
}

const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

/**
 * Vrai si la frappe appartient à un champ de saisie.
 *
 * L'écoute est posée sur `window` — un canevas Konva ne prend pas le focus clavier. Sans ce
 * filtre, corriger le nom d'une pièce au clavier supprimait un sommet du tracé, et personne ne
 * faisait le lien. Le contrôle est fait sur le type réel du nœud, pas sur un sélecteur : un
 * `contenteditable` compte aussi.
 */
export function isTypingTarget(target: unknown): boolean {
  if (target === null || typeof target !== 'object') return false
  const element = target as { tagName?: unknown; isContentEditable?: unknown }
  if (element.isContentEditable === true) return true
  return typeof element.tagName === 'string' && TYPING_TAGS.has(element.tagName)
}

/** L'aide clavier, telle qu'affichée. Ordre de lecture : du plus courant au plus rare. */
export const SHORTCUTS: Shortcut[] = [
  { groupe: 'Édition', touches: 'Ctrl/⌘ + Z', libelle: 'Annuler le dernier geste' },
  { groupe: 'Édition', touches: 'Ctrl/⌘ + Maj + Z', libelle: 'Refaire' },
  { groupe: 'Édition', touches: 'Ctrl/⌘ + C', libelle: 'Copier la sélection' },
  { groupe: 'Édition', touches: 'Ctrl/⌘ + V', libelle: 'Coller dans la pièce courante' },
  { groupe: 'Édition', touches: 'Ctrl/⌘ + D', libelle: 'Dupliquer la sélection sur place' },
  { groupe: 'Édition', touches: 'Suppr', libelle: 'Supprimer la sélection' },
  { groupe: 'Sélection', touches: 'Ctrl/⌘ + A', libelle: 'Tout sélectionner dans la pièce' },
  { groupe: 'Sélection', touches: 'Maj + clic', libelle: 'Ajouter ou retirer de la sélection' },
  {
    groupe: 'Sélection',
    touches: 'Glisser sur le vide',
    libelle: "Rectangle d'encadrement",
  },
  { groupe: 'Sélection', touches: 'Échap', libelle: 'Vider la sélection' },
  { groupe: 'Placement', touches: '← ↑ → ↓', libelle: "Déplacer d'un pas de grille" },
  { groupe: 'Placement', touches: 'Maj + ← ↑ → ↓', libelle: 'Déplacer de dix pas' },
  { groupe: 'Placement', touches: 'R', libelle: 'Tourner de 15° (Maj : sens inverse)' },
  { groupe: 'Tracé', touches: 'Clic', libelle: 'Poser un sommet' },
  { groupe: 'Tracé', touches: 'Chiffres puis Entrée', libelle: 'Saisir la cote du mur en cours' },
  { groupe: 'Tracé', touches: 'Maj (maintenue)', libelle: 'Contraindre par 45°' },
  { groupe: 'Tracé', touches: 'Retour arrière', libelle: 'Reprendre le sommet précédent' },
  { groupe: 'Tracé', touches: 'Entrée', libelle: 'Fermer le contour' },
  { groupe: 'Tracé', touches: 'Échap', libelle: 'Abandonner le tracé' },
  { groupe: 'Vue', touches: 'Molette', libelle: 'Zoomer autour du curseur' },
  { groupe: 'Vue', touches: 'Deux doigts', libelle: 'Déplacer et pincer pour zoomer' },
  { groupe: 'Vue', touches: 'Glisser le fond', libelle: 'Déplacer la vue' },
]
