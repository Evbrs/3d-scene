/**
 * Raccourcis clavier et filtrage des cibles de saisie.
 *
 * Le filtre est le point sensible : l'écoute est posée sur `window` parce qu'un canevas Konva ne
 * prend pas le focus. Sans lui, taper « 250 » dans un champ de cote supprime la sélection au
 * passage de la touche Retour arrière, et personne ne fait le lien.
 */
import { describe, expect, it } from 'vitest'

import {
  SHORTCUTS,
  arrowStep,
  isTypingTarget,
  matchesCopy,
  matchesDelete,
  matchesDuplicate,
  matchesPaste,
  matchesRedo,
  matchesSelectAll,
  matchesUndo,
} from '@/editor/shortcuts'

describe('annuler / refaire', () => {
  it('reconnaît Ctrl+Z et ⌘+Z', () => {
    expect(matchesUndo({ key: 'z', ctrlKey: true })).toBe(true)
    expect(matchesUndo({ key: 'z', metaKey: true })).toBe(true)
    expect(matchesUndo({ key: 'Z', ctrlKey: true })).toBe(true)
  })

  it('ne confond pas annuler et refaire', () => {
    expect(matchesUndo({ key: 'z', ctrlKey: true, shiftKey: true })).toBe(false)
    expect(matchesRedo({ key: 'z', ctrlKey: true, shiftKey: true })).toBe(true)
  })

  it('accepte Ctrl+Y, venu du dessin technique sous Windows', () => {
    expect(matchesRedo({ key: 'y', ctrlKey: true })).toBe(true)
    expect(matchesRedo({ key: 'y' })).toBe(false)
  })

  it('ignore un Z sans modificateur', () => {
    expect(matchesUndo({ key: 'z' })).toBe(false)
  })
})

describe('presse-papier et suppression', () => {
  it('reconnaît copier, coller, dupliquer et tout sélectionner', () => {
    expect(matchesCopy({ key: 'c', metaKey: true })).toBe(true)
    expect(matchesPaste({ key: 'v', ctrlKey: true })).toBe(true)
    expect(matchesDuplicate({ key: 'd', ctrlKey: true })).toBe(true)
    expect(matchesSelectAll({ key: 'a', ctrlKey: true })).toBe(true)
  })

  it('supprime sur Suppr et Retour arrière, jamais avec un modificateur', () => {
    expect(matchesDelete({ key: 'Delete' })).toBe(true)
    expect(matchesDelete({ key: 'Backspace' })).toBe(true)
    // Ctrl+Retour arrière est « supprimer le mot précédent » : il ne doit pas vider le plan.
    expect(matchesDelete({ key: 'Backspace', ctrlKey: true })).toBe(false)
  })
})

describe('déplacement au clavier', () => {
  it('rend un vecteur unitaire par flèche', () => {
    expect(arrowStep({ key: 'ArrowLeft' })).toEqual({ dx: -1, dy: 0 })
    expect(arrowStep({ key: 'ArrowUp' })).toEqual({ dx: 0, dy: -1 })
    expect(arrowStep({ key: 'ArrowRight' })).toEqual({ dx: 1, dy: 0 })
    expect(arrowStep({ key: 'ArrowDown' })).toEqual({ dx: 0, dy: 1 })
  })

  it('ne rend rien sur une autre touche', () => {
    expect(arrowStep({ key: 'a' })).toBeNull()
  })
})

describe('cibles de saisie', () => {
  it.each([['INPUT'], ['TEXTAREA'], ['SELECT']])('écarte un %s', (tagName) => {
    expect(isTypingTarget({ tagName })).toBe(true)
  })

  it('écarte un contenu éditable', () => {
    expect(isTypingTarget({ tagName: 'DIV', isContentEditable: true })).toBe(true)
  })

  it('laisse passer le reste', () => {
    expect(isTypingTarget({ tagName: 'DIV' })).toBe(false)
    expect(isTypingTarget({ tagName: 'BUTTON' })).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
    expect(isTypingTarget(undefined)).toBe(false)
  })

  it('fonctionne sur un vrai nœud du document', () => {
    const champ = document.createElement('input')

    expect(isTypingTarget(champ)).toBe(true)
  })
})

describe('documentation', () => {
  it('couvre chaque raccourci implémenté', () => {
    const touches = SHORTCUTS.map((raccourci) => raccourci.touches).join(' | ')

    // Un raccourci non documenté n'existe pas pour l'utilisateur : cette liste **est** l'aide
    // affichée, et c'est ce qui l'empêche de diverger du code.
    for (const attendu of ['Ctrl/⌘ + Z', 'Ctrl/⌘ + Maj + Z', 'Suppr', '← ↑ → ↓', 'Échap']) {
      expect(touches).toContain(attendu)
    }
  })

  it('ne laisse aucune entrée sans libellé ni groupe', () => {
    for (const raccourci of SHORTCUTS) {
      expect(raccourci.libelle.length).toBeGreaterThan(3)
      expect(raccourci.groupe.length).toBeGreaterThan(0)
    }
  })
})
