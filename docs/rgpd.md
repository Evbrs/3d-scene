# Conformité RGPD — registre, rétention et ce qui reste à faire

*Document d'exploitation. La version que l'utilisateur lit est la page
`/legal/confidentialite` (`frontend/src/views/LegalView.vue`) : les deux disent la même chose, et
si l'une change, l'autre change avec elle. Ce fichier-ci ajoute ce qui n'a pas sa place dans une
page publique — où le code applique la règle, et ce qui bloque encore une mise en production.*

Dernière mise à jour : 2026-08-08.

---

## 1. Rôles

| Donnée | Rôle de l'éditeur |
|---|---|
| Compte, organisation, chantiers | Responsable de traitement |
| Identité du client final saisie dans un devis | **Sous-traitant** de l'artisan |

La distinction n'est pas théorique : une demande d'effacement portant sur un client final doit
être adressée à l'artisan, pas à nous, et c'est à lui d'y donner suite depuis son espace.

---

## 2. Durées de conservation, et où elles sont appliquées

| Donnée | Durée | Appliqué par |
|---|---|---|
| Compte et chantiers | Vie du compte | `DELETE /api/auth/me` → `services/account.py::delete_account`, puis les cascades `ON DELETE` posées en base |
| Organisation sans plus aucun membre | Supprimée avec le dernier membre | `services/account.py::delete_account` (suppression explicite : la cascade de `membership` la laisserait en résidu) |
| Jeton de réinitialisation | 1 heure | `services/account.py::RESET_TOKEN_TTL`, plus `consumed_at` qui interdit le rejeu |
| Invitation | Jusqu'à acceptation ou expiration | `Invitation.expires_at` |
| Lien de partage | Jusqu'à révocation ou échéance | `SharedView`, `POST /api/projects/{id}/shared-views` |
| Devis et factures émis | 10 ans (art. L. 123-22 du code de commerce) | **Aucune purge automatique** — voir §5 |
| Compte inactif 3 ans | Signalement puis suppression | **Non implémenté** — voir §5 |
| Journaux applicatifs | 30 jours | Politique de l'hébergeur — **à confirmer au déploiement** |
| Sauvegardes | 35 jours glissants | Politique de l'hébergeur — **à confirmer au déploiement** |

L'obligation comptable de dix ans est la **seule** qui prime sur une demande d'effacement, et elle
ne porte que sur les documents commerciaux **émis** : un devis en brouillon n'est pas concerné.

---

## 3. Registre des sous-traitants

Aucun sous-traitant n'est contractualisé à ce jour : le service n'est pas déployé. Le tableau
ci-dessous est la liste des postes à pourvoir, et chacun doit être renseigné **avant** la première
inscription réelle. Les mêmes lignes figurent dans la page publique, sous forme de marqueurs.

| Poste | Prestataire | Données confiées | Localisation |
|---|---|---|---|
| Hébergement et base de données | `[HÉBERGEUR]` | L'ensemble des données du service | Union européenne |
| Encaissement des abonnements | `[PRESTATAIRE DE PAIEMENT]` | Identité de facturation, données de carte (jamais chez nous) | Union européenne |
| Courriels transactionnels | `[PRESTATAIRE D'ENVOI]` | Adresse du destinataire, contenu du message de service | Union européenne |
| Supervision des erreurs | `[PRESTATAIRE DE SUPERVISION]` | Traces d'exécution, expurgées des données de chantier | Union européenne |

Aucun transfert hors Union européenne n'est prévu. S'il en apparaît un, il exige des clauses
contractuelles types **et** une mise à jour de la page publique **avant** sa mise en œuvre.

---

## 4. Ce que le code garantit déjà

- **Minimisation** : le compte ne porte qu'une adresse e-mail et un mot de passe haché en
  Argon2id. Aucun nom, aucun téléphone, aucune date de naissance.
- **Pas de secret exportable** : `services/account.py::export_account` construit le bloc `compte`
  champ par champ, et `tests/test_rgpd.py` vérifie qu'aucun `hashed_password` ni `token_hash` ne
  se glisse dans le fichier rendu.
- **Pas de fuite entre locataires** : l'export est classé dans `TENANT_COLLECTIONS`
  (`tests/test_permissions_locataire.py`) et son test de non-fuite est dans `tests/test_rgpd.py`.
  Une route de ce genre ne porte aucun identifiant : rien ne la protège qu'un `WHERE` correct.
- **Jetons hachés** : ni le jeton d'invitation ni le jeton de réinitialisation n'existent en clair
  en base. Une copie de la base ne permet pas de prendre la main sur les comptes qu'elle contient.
- **Révocation globale** : `User.token_version` est recopié dans chaque JWT et confronté au compte
  à chaque requête (`app/api/deps.py`). Changer ou réinitialiser un mot de passe ferme toutes les
  sessions ouvertes.
- **Journaux sans IP brute** : `app/api/auth.py::_client_key` dérive la clé de limitation de débit
  de l'adresse sans la stocker ailleurs.
- **Consentement explicite** : la case d'acceptation des CGU à l'inscription est `required`, et
  elle porte les liens vers les CGU et la politique de confidentialité.

---

## 5. Ce qui manque avant une mise en production

Par ordre de gravité. Aucun de ces points n'est un défaut de code : ce sont des décisions et des
contrats qui appartiennent à l'exploitant.

1. **Aucun transport de courriel.** `POST /api/auth/password/forgot` fabrique bien le jeton, mais
   rien ne l'achemine : il n'est rendu dans la réponse **qu'en développement**. En production, la
   route répond 202 et personne ne reçoit rien. C'est le seul point qui rend aujourd'hui la
   réinitialisation inutilisable en ligne.
2. **Les quatre documents légaux n'ont pas été relus par un juriste**, et les valeurs de
   l'exploitant y sont des marqueurs entre crochets. Tant qu'ils y figurent, rien n'est opposable.
3. **Aucune purge des comptes inactifs** et **aucune purge des documents commerciaux au-delà de
   dix ans.** Les deux durées sont annoncées dans la politique publique ; les annoncer sans les
   appliquer est en soi un manquement. Il faut une tâche périodique — Celery est déjà en place.
4. **Les durées de rétention des journaux et des sauvegardes sont annoncées mais non vérifiées** :
   elles dépendent entièrement du contrat d'hébergement, qui n'existe pas encore.
5. **Aucune procédure de notification de violation** au-delà de la phrase de la politique. Le
   délai de soixante-douze heures suppose une astreinte et un canal, pas seulement une intention.
