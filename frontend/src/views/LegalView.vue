<script setup lang="ts">
/**
 * Socle légal : mentions, CGU, CGV et politique de confidentialité.
 *
 * On ne vend pas un abonnement sans conditions générales de vente opposables, et on n'informe pas
 * un utilisateur de ses droits (RGPD art. 13) avec une page « en cours de rédaction ». Ces quatre
 * documents sont donc des **gabarits sérieux** : la structure, les rubriques obligatoires et les
 * renvois sont en place, les éléments propres à l'exploitant sont des marqueurs explicites.
 *
 * Deux décisions à ne pas défaire :
 *
 * 1. **Les valeurs manquantes sont écrites en clair entre crochets** plutôt que remplies avec un
 *    exemple plausible. Une raison sociale inventée dans des mentions légales est pire que rien :
 *    elle a l'air valide, donc personne ne la corrige. Le dépôt est public, et aucune entreprise
 *    n'est encore constituée derrière ce service.
 * 2. **L'avertissement de relecture juridique est en tête de chaque document**, pas en note de
 *    bas de page. Il disparaîtra le jour où un juriste aura relu — ce sera une suppression d'une
 *    ligne, visible en revue.
 *
 * Les quatre documents partagent une seule vue : ils ont la même mise en page, et trois écrans
 * identiques à maintenir en parallèle divergent toujours.
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { DOCUMENTS_LEGAUX, type DocumentLegal } from '@/router'

const props = defineProps<{ document: string }>()

interface Section {
  titre: string
  paragraphes: string[]
}

interface DocumentJuridique {
  titre: string
  chapeau: string
  miseAJour: string
  sections: Section[]
}

/** Date de rédaction des gabarits. Écrite une fois : quatre dates divergentes se remarquent. */
const MISE_A_JOUR = '8 août 2026'

const EXPLOITANT = '[RAISON SOCIALE DE L’ÉDITEUR]'

const DOCUMENTS: Record<DocumentLegal, DocumentJuridique> = {
  mentions: {
    titre: 'Mentions légales',
    chapeau:
      'Informations relatives à l’éditeur du service et à son hébergeur, exigées par '
      + 'l’article 6 III de la loi pour la confiance dans l’économie numérique.',
    miseAJour: MISE_A_JOUR,
    sections: [
      {
        titre: 'Éditeur du service',
        paragraphes: [
          `Dénomination sociale : ${EXPLOITANT}.`,
          'Forme juridique et capital social : [FORME JURIDIQUE], au capital de [CAPITAL] €.',
          'Siège social : [ADRESSE COMPLÈTE].',
          'Immatriculation : RCS [VILLE] sous le numéro [SIREN], SIRET [SIRET].',
          'Numéro de TVA intracommunautaire : [N° TVA].',
          'Directeur de la publication : [NOM DU DIRECTEUR DE LA PUBLICATION].',
          'Contact : [ADRESSE DE CONTACT] — [TÉLÉPHONE].',
        ],
      },
      {
        titre: 'Hébergement',
        paragraphes: [
          'Le service est hébergé par [HÉBERGEUR], [ADRESSE DE L’HÉBERGEUR], [PAYS].',
          'Les données sont stockées au sein de l’Union européenne. Tout changement de région '
          + 'd’hébergement fait l’objet d’une mise à jour de la politique de confidentialité.',
        ],
      },
      {
        titre: 'Propriété intellectuelle',
        paragraphes: [
          'Le logiciel, son interface, sa documentation et sa bibliothèque de mobilier '
          + 'paramétrique sont la propriété de l’éditeur. Aucun catalogue de marque tierce n’est '
          + 'intégré au service : le mobilier proposé est générique et paramétrique.',
          'Les plans, relevés, devis et documents produits par un utilisateur au moyen du service '
          + 'lui appartiennent. L’éditeur n’en revendique aucun droit et ne les exploite pas à '
          + 'd’autres fins que la fourniture du service.',
        ],
      },
      {
        titre: 'Signalement',
        paragraphes: [
          'Tout contenu manifestement illicite accessible par un lien de partage peut être '
          + 'signalé à [ADRESSE DE SIGNALEMENT]. Le lien concerné est désactivé après examen.',
        ],
      },
    ],
  },

  cgu: {
    titre: 'Conditions générales d’utilisation',
    chapeau:
      'Règles d’accès et d’usage du service. Elles s’appliquent à tout compte, y compris sur '
      + 'l’offre gratuite.',
    miseAJour: MISE_A_JOUR,
    sections: [
      {
        titre: '1. Objet et acceptation',
        paragraphes: [
          'Le service permet de relever un local, d’en produire un plan en deux dimensions, une '
          + 'visualisation en trois dimensions, un métré et un devis chiffré.',
          'La création d’un compte vaut acceptation des présentes conditions et de la politique '
          + 'de confidentialité. Une case à cocher explicite est présentée à l’inscription : '
          + 'aucune acceptation n’est déduite de la simple navigation.',
        ],
      },
      {
        titre: '2. Compte et sécurité',
        paragraphes: [
          'Le compte est personnel. Le mot de passe comporte au moins douze caractères et n’est '
          + 'jamais stocké en clair.',
          'Le titulaire est responsable des actions menées depuis son compte. En cas de doute, le '
          + 'changement de mot de passe ferme immédiatement toutes les sessions ouvertes, sur '
          + 'tous les appareils.',
          'Les droits d’accès à un chantier découlent de l’appartenance à l’organisation qui le '
          + 'porte, et non de la personne qui l’a créé. Retirer une personne de l’organisation lui '
          + 'retire l’accès.',
        ],
      },
      {
        titre: '3. Usage attendu',
        paragraphes: [
          'Sont interdits : la tentative d’accès aux données d’une autre organisation, la '
          + 'recherche de vulnérabilité sans autorisation écrite, l’envoi automatisé de requêtes '
          + 'au-delà des limites publiées, et la revente de l’accès au service.',
          'Un lien de partage donne accès en lecture à une vue d’un chantier, sans compte. Il est '
          + 'de la responsabilité de l’utilisateur de ne le transmettre qu’aux destinataires '
          + 'voulus, et de le révoquer lorsqu’il n’a plus lieu d’être.',
        ],
      },
      {
        titre: '4. Disponibilité et évolutions',
        paragraphes: [
          'Le service est fourni en l’état, avec un objectif de disponibilité et sans garantie de '
          + 'fonctionnement ininterrompu. Les interventions de maintenance programmées sont '
          + 'annoncées lorsqu’elles sont susceptibles d’interrompre l’accès.',
          'Les fonctionnalités évoluent. Une évolution retirant une fonctionnalité d’une offre '
          + 'payante est annoncée au moins trente jours à l’avance.',
        ],
      },
      {
        titre: '5. Résiliation',
        paragraphes: [
          'Le titulaire peut fermer son compte à tout moment depuis la page « Mon compte ». La '
          + 'fermeture efface le compte et les chantiers qu’il a créés.',
          'Lorsque le titulaire est le dernier propriétaire d’une organisation comptant d’autres '
          + 'membres, la fermeture est refusée tant qu’un autre propriétaire n’a pas été désigné : '
          + 'partir emporterait les chantiers de ses collègues.',
          'L’éditeur peut suspendre un compte en cas de manquement grave aux présentes '
          + 'conditions, après information du titulaire sauf urgence de sécurité.',
        ],
      },
      {
        titre: '6. Responsabilité',
        paragraphes: [
          'Les métrés, quantités, taux de chute et montants produits par le service sont des '
          + 'aides à l’établissement d’un devis. Ils reposent sur les données saisies par '
          + 'l’utilisateur et ne se substituent ni à un relevé contradictoire, ni au jugement '
          + 'professionnel de l’artisan, qui reste seul responsable des documents qu’il émet.',
          'Le service produit des fichiers au format réglementaire Factur-X. Il n’est pas une '
          + 'plateforme de dématérialisation agréée et ne transmet rien à l’administration '
          + 'fiscale : la transmission reste à la charge de l’utilisateur.',
        ],
      },
      {
        titre: '7. Droit applicable',
        paragraphes: [
          'Les présentes conditions sont soumises au droit français. À défaut de résolution '
          + 'amiable, le litige est porté devant les juridictions compétentes.',
        ],
      },
    ],
  },

  cgv: {
    titre: 'Conditions générales de vente',
    chapeau:
      'Conditions applicables aux abonnements payants. Elles complètent les conditions '
      + 'générales d’utilisation, qui restent applicables.',
    miseAJour: MISE_A_JOUR,
    sections: [
      {
        titre: '1. Offres et prix',
        paragraphes: [
          'Les offres, leurs limites et leurs tarifs sont ceux affichés sur la page des tarifs au '
          + 'jour de la souscription. Les prix sont indiqués hors taxes ; la TVA applicable est '
          + 'ajoutée sur la facture.',
          'Une offre gratuite permet d’utiliser le service dans des limites publiées. Le '
          + 'dépassement d’une limite ne supprime jamais de donnée : les chantiers excédentaires '
          + 'passent en lecture seule et redeviennent modifiables dès le retour dans les limites.',
        ],
      },
      {
        titre: '2. Souscription, durée et reconduction',
        paragraphes: [
          'L’abonnement est souscrit pour la période choisie, mensuelle ou annuelle, et reconduit '
          + 'tacitement pour une période identique à défaut de résiliation.',
          'La période d’essai, lorsqu’elle est proposée, est déclenchée au premier usage d’une '
          + 'fonctionnalité payante et non à la création du compte. Elle ne requiert aucun moyen '
          + 'de paiement et s’achève sans reconduction automatique vers une offre payante.',
        ],
      },
      {
        titre: '3. Paiement',
        paragraphes: [
          'Le paiement est exigible à la souscription puis à chaque échéance. Il est encaissé par '
          + '[PRESTATAIRE DE PAIEMENT], qui traite seul les données de carte : elles ne '
          + 'transitent pas par le service et n’y sont jamais stockées.',
          'En cas d’échec de paiement, l’accès aux fonctionnalités payantes est suspendu après '
          + 'relance. Les données restent accessibles en lecture.',
          'Conformément à l’article L. 441-10 du code de commerce, tout retard de paiement entre '
          + 'professionnels donne lieu à des pénalités au taux de [TAUX] et à une indemnité '
          + 'forfaitaire de recouvrement de 40 €.',
        ],
      },
      {
        titre: '4. Résiliation et remboursement',
        paragraphes: [
          'La résiliation prend effet à la fin de la période en cours. Aucun remboursement '
          + 'prorata temporis n’est dû pour une période entamée, sauf indisponibilité prolongée '
          + 'imputable à l’éditeur.',
          'Après résiliation, le compte bascule sur l’offre gratuite. Les données restent '
          + 'accessibles et exportables ; elles ne sont pas supprimées du fait de la résiliation.',
        ],
      },
      {
        titre: '5. Droit de rétractation',
        paragraphes: [
          'Le service s’adresse à des professionnels agissant dans le cadre de leur activité : le '
          + 'droit de rétractation des articles L. 221-18 et suivants du code de la consommation '
          + 'ne s’applique pas de plein droit.',
          'Lorsqu’un souscripteur relève des dispositions applicables aux consommateurs, ou d’une '
          + 'entreprise de cinq salariés au plus souscrivant hors de son champ d’activité '
          + 'principal, il dispose de quatorze jours pour se rétracter en écrivant à '
          + '[ADRESSE DE CONTACT].',
        ],
      },
      {
        titre: '6. Réversibilité',
        paragraphes: [
          'Les données du compte sont exportables à tout moment, en JSON, depuis la page « Mon '
          + 'compte ». L’export est complet et ne dépend d’aucune offre : la réversibilité n’est '
          + 'pas une fonctionnalité payante.',
        ],
      },
      {
        titre: '7. Médiation',
        paragraphes: [
          'En cas de litige avec un consommateur, celui-ci peut recourir gratuitement au médiateur '
          + 'de la consommation [NOM DU MÉDIATEUR], [COORDONNÉES DU MÉDIATEUR].',
        ],
      },
    ],
  },

  confidentialite: {
    titre: 'Politique de confidentialité',
    chapeau:
      'Quelles données sont traitées, pourquoi, combien de temps, par qui, et comment exercer '
      + 'ses droits. Information au titre des articles 13 et 14 du RGPD.',
    miseAJour: MISE_A_JOUR,
    sections: [
      {
        titre: '1. Responsable de traitement',
        paragraphes: [
          `${EXPLOITANT}, [ADRESSE COMPLÈTE]. Contact pour toute question relative aux données `
          + 'personnelles : [ADRESSE DE CONTACT RGPD].',
          'L’éditeur agit en qualité de responsable de traitement pour les données de compte, et '
          + 'en qualité de sous-traitant pour les données de clientèle que l’artisan saisit dans '
          + 'ses devis.',
        ],
      },
      {
        titre: '2. Données traitées et finalités',
        paragraphes: [
          'Compte : adresse e-mail et mot de passe haché. Finalité : ouvrir et sécuriser l’accès. '
          + 'Base légale : exécution du contrat.',
          'Organisation : identité de l’entreprise, dont SIRET, forme juridique, capital, RCS, '
          + 'numéro de TVA et assurance décennale. Finalité : produire des devis et des factures '
          + 'valables. Base légale : obligation légale et exécution du contrat.',
          'Chantiers : plans, pièces, revêtements et mobilier saisis. Finalité : fournir le '
          + 'service. Base légale : exécution du contrat.',
          'Devis et factures : identité et coordonnées du client final, adresse du chantier, '
          + 'montants. Finalité : établir les documents commerciaux. Base légale : exécution du '
          + 'contrat et obligation comptable.',
          'Journaux techniques : identifiant de requête, date, route appelée, code de réponse. '
          + 'Les adresses IP ne sont pas conservées en clair dans les journaux applicatifs. '
          + 'Finalité : sécurité et diagnostic. Base légale : intérêt légitime.',
        ],
      },
      {
        titre: '3. Ce qui n’est pas fait',
        paragraphes: [
          'Aucun traceur publicitaire, aucun pistage inter-sites, aucune revente ni aucun partage '
          + 'de données à des fins commerciales.',
          'Aucun cookie n’est déposé à des fins de mesure d’audience sans consentement préalable. '
          + 'Le seul cookie posé par défaut porte la session : il est strictement nécessaire au '
          + 'fonctionnement du service, `HttpOnly`, `Secure` et restreint aux routes '
          + 'd’authentification.',
          'Les fonctions d’assistance du produit sont algorithmiques et s’exécutent sur nos '
          + 'serveurs : aucune donnée de chantier n’est transmise à un service d’intelligence '
          + 'artificielle tiers.',
        ],
      },
      {
        titre: '4. Durées de conservation',
        paragraphes: [
          'Compte et chantiers : conservés pendant toute la vie du compte, puis supprimés à sa '
          + 'fermeture.',
          'Compte inactif : un compte sans aucune connexion pendant trois ans est signalé par '
          + 'courriel, puis supprimé faute de réponse sous un mois.',
          'Devis et factures émis : conservés dix ans à compter de la clôture de l’exercice, '
          + 'conformément à l’article L. 123-22 du code de commerce. Cette obligation prime sur '
          + 'la demande d’effacement, pour ces documents seulement.',
          'Jetons de réinitialisation de mot de passe : une heure, puis inutilisables. La trace '
          + 'de l’usage est conservée pour interdire le rejeu.',
          'Invitations : jusqu’à leur acceptation ou leur expiration, puis conservées comme trace '
          + 'de l’ouverture d’un accès.',
          'Liens de partage : jusqu’à leur révocation ou l’échéance choisie à leur création.',
          'Journaux techniques : trente jours.',
          'Sauvegardes chiffrées : trente-cinq jours de rétention glissante. Une suppression '
          + 'demandée est répercutée sur les sauvegardes à l’expiration de ce délai.',
        ],
      },
      {
        titre: '5. Registre des sous-traitants',
        paragraphes: [
          'Hébergement et base de données : [HÉBERGEUR], Union européenne. Traite l’ensemble des '
          + 'données du service pour les stocker et les servir.',
          'Encaissement des abonnements : [PRESTATAIRE DE PAIEMENT], Union européenne. Traite '
          + 'l’identité de facturation et les données de paiement, qui ne transitent pas par nos '
          + 'serveurs.',
          'Envoi de courriels transactionnels : [PRESTATAIRE D’ENVOI]. Traite l’adresse e-mail du '
          + 'destinataire et le contenu du message, pour les seuls messages de service '
          + '(réinitialisation de mot de passe, invitation).',
          'Supervision des erreurs : [PRESTATAIRE DE SUPERVISION], Union européenne. Traite les '
          + 'traces d’exécution, expurgées des données de chantier.',
          'Aucun transfert hors de l’Union européenne n’est en place. Si un sous-traitant venait '
          + 'à en imposer un, il serait encadré par les clauses contractuelles types de la '
          + 'Commission européenne et mentionné ici avant sa mise en œuvre.',
        ],
      },
      {
        titre: '6. Vos droits',
        paragraphes: [
          'Accès et portabilité : la page « Mon compte » produit un export JSON complet et '
          + 'immédiat de toutes les données du compte et des organisations dont il est membre.',
          'Rectification : les données de compte et d’entreprise sont modifiables directement '
          + 'dans le service.',
          'Effacement : la fermeture du compte, depuis « Mon compte », supprime le compte et ses '
          + 'chantiers. Les documents comptables déjà émis sont conservés au titre de '
          + 'l’obligation légale rappelée au point 4.',
          'Opposition et limitation : écrire à [ADRESSE DE CONTACT RGPD]. Une réponse est apportée '
          + 'dans un délai d’un mois.',
          'Réclamation : toute personne peut saisir la Commission nationale de l’informatique et '
          + 'des libertés (CNIL), 3 place de Fontenoy, 75007 Paris, www.cnil.fr.',
        ],
      },
      {
        titre: '7. Sécurité',
        paragraphes: [
          'Chiffrement des échanges en transit, mots de passe hachés avec Argon2id, cloisonnement '
          + 'des données par organisation vérifié automatiquement à chaque livraison, en-têtes de '
          + 'sécurité et limitation de débit sur les routes d’authentification.',
          'En cas de violation de données susceptible d’engendrer un risque pour les personnes, '
          + 'la CNIL est notifiée dans les soixante-douze heures et les personnes concernées sont '
          + 'informées lorsque le risque est élevé.',
        ],
      },
    ],
  },
}

const ONGLETS: { cle: DocumentLegal; libelle: string }[] = [
  { cle: 'mentions', libelle: 'Mentions légales' },
  { cle: 'cgu', libelle: 'CGU' },
  { cle: 'cgv', libelle: 'CGV' },
  { cle: 'confidentialite', libelle: 'Confidentialité' },
]

function estConnu(valeur: string): valeur is DocumentLegal {
  return (DOCUMENTS_LEGAUX as readonly string[]).includes(valeur)
}

/** Un document inconnu retombe sur les mentions légales plutôt que sur un écran vide. */
const cle = computed<DocumentLegal>(() => (estConnu(props.document) ? props.document : 'mentions'))
const contenu = computed(() => DOCUMENTS[cle.value])
</script>

<template>
  <article class="legal">
    <nav
      class="onglets"
      aria-label="Documents légaux"
    >
      <RouterLink
        v-for="onglet in ONGLETS"
        :key="onglet.cle"
        :to="`/legal/${onglet.cle}`"
        :aria-current="onglet.cle === cle ? 'page' : undefined"
      >
        {{ onglet.libelle }}
      </RouterLink>
    </nav>

    <h1>{{ contenu.titre }}</h1>
    <p class="chapeau">
      {{ contenu.chapeau }}
    </p>

    <p
      class="avertissement"
      role="note"
    >
      <strong>Gabarit à faire relire par un juriste avant toute mise en production.</strong>
      La structure et les rubriques obligatoires sont en place, mais ce texte n'a pas été validé
      par un professionnel du droit. Les valeurs entre crochets doivent être renseignées par
      l'exploitant : tant qu'elles y figurent, le document n'est pas opposable.
    </p>

    <section
      v-for="section in contenu.sections"
      :key="section.titre"
    >
      <h2>{{ section.titre }}</h2>
      <p
        v-for="(paragraphe, rang) in section.paragraphes"
        :key="rang"
      >
        {{ paragraphe }}
      </p>
    </section>

    <p class="date">
      Dernière mise à jour : {{ contenu.miseAJour }}.
    </p>

    <p>
      <RouterLink to="/connexion">
        ← Retour à la connexion
      </RouterLink>
    </p>
  </article>
</template>

<style scoped>
.legal {
  max-width: 46rem;
  margin: 0 auto 3rem;
}

.onglets {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin-bottom: 1.5rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--bordure);
}

.onglets a[aria-current='page'] {
  font-weight: 700;
  text-decoration: none;
  border-bottom: 3px solid var(--accent);
}

.chapeau {
  color: var(--texte-doux);
  font-size: 1.05rem;
}

.avertissement {
  margin: 1.25rem 0 2rem;
  padding: 0.85rem 1rem;
  border-left: 4px solid #8a5a00;
  border-radius: 0.25rem;
  /* Contraste vérifié à 7:1 sur ce fond : l'avertissement est la ligne à ne jamais rater. */
  background: #fdf3e0;
  color: #4a3000;
}

h2 {
  margin-top: 2rem;
  font-size: 1.15rem;
}

.legal p {
  /* Une politique de confidentialité se lit en diagonale : l'espace entre paragraphes est ce qui
     rend une rubrique repérable sans la lire. */
  margin: 0.75rem 0;
}

.date {
  margin-top: 2.5rem;
  color: var(--texte-doux);
  font-size: 0.9rem;
}
</style>
