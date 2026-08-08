/**
 * Le panneau d'inspection.
 *
 * Ce qui est vérifié ici n'est pas du pixel mais du **comportement** : l'ordre rendu est celui du
 * serveur, une anomalie est atteignable au clavier, le clic remonte de quoi recentrer le plan, et
 * ce qui n'a pas pu être contrôlé reste visible. C'est exactement la liste des façons dont un
 * panneau d'alertes devient inutile sans que rien ne casse.
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { Anomaly, InspectionReport } from '@/api/types'
import InspectorPanel from '@/views/InspectorPanel.vue'

function anomaly(overrides: Partial<Anomaly> = {}): Anomaly {
  return {
    rule_id: 'circulation.passage_etroit',
    title: 'Passage trop étroit',
    severity: 'bloquant',
    message:
      'Passage libre de 40 cm entre « meuble-bas » et « ilot » : il manque 50 cm pour atteindre ' +
      "les 90 cm d'une circulation courante.",
    room_id: 130,
    room_name: 'Cuisine',
    face_labels: [],
    element_ids: [13050, 13051],
    focus: [200, 85],
    measured_cm: 40,
    threshold_cm: 90,
    ...overrides,
  }
}

function report(overrides: Partial<InspectionReport> = {}): InspectionReport {
  const anomalies = overrides.anomalies ?? [anomaly()]
  return {
    project_id: 13,
    thresholds: { passage_min_cm: 90, accessible: false, accessible_passage_min_cm: 120 },
    rooms: [{ room_id: 130, name: 'Cuisine', counts: { bloquant: 1 } }],
    anomalies,
    counts: {
      bloquant: anomalies.filter((item) => item.severity === 'bloquant').length,
      avertissement: anomalies.filter((item) => item.severity === 'avertissement').length,
      conseil: anomalies.filter((item) => item.severity === 'conseil').length,
    },
    warnings: [],
    ...overrides,
  }
}

describe('panneau d’inspection', () => {
  it('annonce un plan conforme plutôt que de laisser un vide', () => {
    const panel = mount(InspectorPanel, {
      props: { report: report({ anomalies: [] }) },
    })

    expect(panel.text()).toContain('Aucune anomalie détectée')
  })

  it('affiche le message du serveur sans le recomposer', () => {
    // Le « de combien » est calculé une seule fois, côté moteur. Recomposer une phrase à partir
    // de `measured_cm` et `threshold_cm` créerait une seconde formulation à maintenir.
    const panel = mount(InspectorPanel, { props: { report: report() } })

    expect(panel.text()).toContain('il manque 50 cm')
    expect(panel.text()).toContain('Passage trop étroit')
  })

  it('respecte l’ordre rendu par le serveur', () => {
    const panel = mount(InspectorPanel, {
      props: {
        report: report({
          anomalies: [
            anomaly({ title: 'Débattement de porte impossible', severity: 'bloquant' }),
            anomaly({ title: 'Allège de fenêtre sous le seuil', severity: 'avertissement' }),
            anomaly({ title: 'Sens d’ouverture imposé', severity: 'conseil' }),
          ],
        }),
      },
    })

    const titres = panel.findAll('.titre').map((node) => node.text())
    expect(titres).toEqual([
      'Débattement de porte impossible',
      'Allège de fenêtre sous le seuil',
      'Sens d’ouverture imposé',
    ])
  })

  it('rend chaque anomalie cliquable et émet de quoi recentrer le plan', async () => {
    const panel = mount(InspectorPanel, { props: { report: report() } })

    await panel.get('button.anomalie').trigger('click')

    const emitted = panel.emitted('recentrer')
    expect(emitted).toHaveLength(1)
    const [recu] = emitted![0] as [Anomaly]
    expect(recu.focus).toEqual([200, 85])
    expect(recu.element_ids).toEqual([13050, 13051])
    expect(recu.room_id).toBe(130)
  })

  it('désactive le clic d’une anomalie qui ne désigne aucun endroit', async () => {
    // « Pièce sans ouverture » n'a ni point ni élément : le bouton reste, mais inerte. Le faire
    // disparaître changerait la nature d'une ligne d'un rapport à l'autre.
    const panel = mount(InspectorPanel, {
      props: {
        report: report({
          anomalies: [
            anomaly({
              rule_id: 'piece.sans_ouverture',
              title: 'Pièce sans ouverture',
              focus: null,
              element_ids: [],
            }),
          ],
        }),
      },
    })

    const bouton = panel.get('button.anomalie')
    expect(bouton.attributes('disabled')).toBeDefined()
    await bouton.trigger('click')
    expect(panel.emitted('recentrer')).toBeUndefined()
  })

  it('chaque anomalie est un vrai bouton, donc atteignable au clavier', () => {
    // Une div avec un `@click` est invisible pour un lecteur d'écran et hors de la tabulation.
    const panel = mount(InspectorPanel, { props: { report: report() } })

    const boutons = panel.findAll('li .anomalie')
    expect(boutons).toHaveLength(1)
    expect(boutons[0]!.element.tagName).toBe('BUTTON')
    expect(boutons[0]!.attributes('type')).toBe('button')
  })

  it('écrit la sévérité en toutes lettres et pas seulement en couleur', () => {
    // WCAG 1.4.1 : la couleur ne doit jamais porter l'information seule.
    const panel = mount(InspectorPanel, { props: { report: report() } })

    expect(panel.get('.etiquette').text()).toBe('Bloquant')
  })

  it('filtre par sévérité sans jamais masquer par défaut', async () => {
    const panel = mount(InspectorPanel, {
      props: {
        report: report({
          anomalies: [
            anomaly({ severity: 'bloquant', title: 'Un bloquant' }),
            anomaly({ severity: 'conseil', title: 'Un conseil' }),
          ],
        }),
      },
    })

    expect(panel.findAll('li .anomalie')).toHaveLength(2)

    const cases = panel.findAll('.filtre input[type="checkbox"]')
    expect(cases.every((node) => (node.element as HTMLInputElement).checked)).toBe(true)

    await cases[0]!.setValue(false)
    const restant = panel.findAll('li .anomalie')
    expect(restant).toHaveLength(1)
    expect(restant[0]!.text()).toContain('Un conseil')
  })

  it('montre ce qui n’a pas pu être contrôlé', () => {
    // Un rapport vide accompagné d'un avertissement ne veut pas dire « conforme » : si la réserve
    // n'apparaît nulle part, le silence passe pour une garantie.
    const panel = mount(InspectorPanel, {
      props: {
        report: report({
          anomalies: [],
          warnings: ['pièce « Chambre » : les murs ne se referment pas en un contour.'],
        }),
      },
    })

    expect(panel.text()).toContain('1 point(s) non contrôlé(s)')
    expect(panel.text()).toContain('les murs ne se referment pas')
  })

  it('annonce le barème accessible quand il a été appliqué', () => {
    const panel = mount(InspectorPanel, {
      props: {
        report: report({
          thresholds: { passage_min_cm: 90, accessible: true, accessible_passage_min_cm: 120 },
        }),
      },
    })

    expect(panel.text()).toContain('logement accessible')
    expect(panel.text()).toContain('120 cm')
  })

  it('distingue le chargement, l’erreur et le rapport absent', async () => {
    const panel = mount(InspectorPanel, { props: { report: null } })
    expect(panel.text()).toContain("n'a pas encore été analysé")

    await panel.setProps({ loading: true })
    expect(panel.get('[role="status"]').text()).toContain('Analyse du plan en cours')

    await panel.setProps({ loading: false, error: 'Projet introuvable' })
    expect(panel.get('[role="alert"]').text()).toBe('Projet introuvable')
  })

  it('demande une relecture au composant qui le porte', async () => {
    const panel = mount(InspectorPanel, { props: { report: report() } })

    await panel.get('header button').trigger('click')

    expect(panel.emitted('rafraichir')).toHaveLength(1)
  })

  it('ne propose le filtre par pièce que s’il y a plusieurs pièces', async () => {
    const panel = mount(InspectorPanel, { props: { report: report() } })
    expect(panel.find('select').exists()).toBe(false)

    await panel.setProps({
      report: report({
        rooms: [
          { room_id: 130, name: 'Cuisine', counts: {} },
          { room_id: 131, name: 'Salon', counts: {} },
        ],
        anomalies: [anomaly({ room_id: 130 }), anomaly({ room_id: 131, room_name: 'Salon' })],
      }),
    })

    expect(panel.findAll('li .anomalie')).toHaveLength(2)

    // `:value` porte un nombre, pas une chaîne : Vue conserve la valeur d'origine de l'option,
    // et le filtre compare bien deux identifiants et non un identifiant à son écriture décimale.
    await panel.get('select').setValue('131')
    const restant = panel.findAll('li .anomalie')
    expect(restant).toHaveLength(1)
    expect(restant[0]!.text()).toContain('Salon')
  })
})
