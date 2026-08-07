# Point de reprise — chantier « meilleure app de la niche »

*Document de passation. Une session qui démarre ici doit pouvoir reprendre le travail sans
refaire l'audit. Mets-le à jour à la fin de chaque vague.*

Dernière mise à jour : 2026-08-08, fin de la vague 1.

---

## Le cadrage, en cinq lignes

Le propriétaire veut faire de cet éditeur de plan de rénovation **la meilleure application de sa
niche**. Il a donné carte blanche : stabiliser et pousser à fond l'existant, ajouter des
fonctionnalités « game changer », tenir un gros trafic simultané à coût serveur maîtrisé, durcir
la cybersécurité, et poser un modèle de monétisation. **Il ne veut rien avoir à valider.**

Deux contraintes explicites de sa part :

1. **Aucun catalogue de marque externe** (IKEA & consorts). Le mobilier reste paramétrique et
   générique, conformément à `spec-complete.md` §4.1. Ce n'est pas une limite, c'est l'avantage
   de coût du produit — voir `strategie-produit.md` §7.
2. **L'IA est acceptée.** Elle doit être algorithmique et locale (déterministe, testable par
   fixtures, sans clé ni appel sortant) — voir `strategie-produit.md` §3.8.

Lire dans l'ordre : `CLAUDE.md`, `docs/spec-complete.md` (le contrat), `docs/strategie-produit.md`
(le positionnement, le pricing et les fonctionnalités à construire), puis ce fichier.

---

## Où on en est

### Fait — vague 1 « stabilisation » (6 lots, livrés et verts sauf une exception ci-dessous)

| Lot | Contenu |
|---|---|
| **L1** | La production démarrait pas. Image nginx non privilégiée, passage en **same-origin** (`proxy_pass /api/`), en-têtes de sécurité inclus dans *chaque* location (aucune page HTML n'avait de CSP), service `migrate` one-shot, port 8000 refermé, bordure TLS, `X-Robots-Tag` + `robots.txt` sur les liens de partage, Dockerfile multi-étage, dépendances figées, CI qui démarre réellement la pile. |
| **L2** | Fuite du pool corrigée (`dispose()`), pool dimensionné, `run_in_threadpool` sur tout le CPU (Argon2id à 35 ms, scene graph jusqu'à 83 ms bloquaient la boucle d'évènements), corps de requête bornés (413), `ProxyHeadersMiddleware`, `TrustedHostMiddleware`, limiteur de débit **sur Redis**, journalisation JSON structurée avec `X-Request-Id`, `/health/live` et `/health/ready`, GZip avec liste blanche, `create-superuser`. |
| **L4** | `CheckConstraint` en base sur les 7 tables (`Room(wall_thickness_cm=-5)` était accepté : les `Field(gt=0)` sont **inertes** sur les modèles `table=True`), `order_by` sur les relations (leur absence faisait dessiner dans la mauvaise pièce), `passive_deletes`, `server_default` partout, JSON → JSONB, colonnes réelles sur `sharedview`, `token_version` sur `user`. |
| **L5** | Contrat d'écriture : les trois DELETE étaient en « dernière écriture gagne », le 409 ne renvoyait jamais `current_version`. Validation d'encombrement corrigée (elle comparait une hauteur verticale à une profondeur au sol), non-chevauchement des ouvertures, refus des polygones auto-sécants, cookie `httpOnly` pour le refresh token, partage filtré côté serveur (il livrait la géométrie complète du logement sans authentification). |
| **Géométrie** | Cylindre corrigé (il produisait un **cône** dès que largeur ≠ profondeur : 5 recettes fausses), mitrage des murs (fente verticale à chaque angle), normale et altitude du plafond, `variant_params` branché, menuiseries rendues, `net_floor_area_cm2`, fixtures oblique et en L calculées à la main. |
| **L6** | Rafraîchissement silencieux du jeton (au bout de 30 min d'édition, tout enregistrement échouait en 401 et le travail était perdu), conflits typés, fusion du revêtement (choisir une couleur effaçait matière et motif), touches filtrées, brouillon purgé au changement de pièce, sélecteur de pièce en 3D, découpage du bundle, responsive, **premiers tests de client HTTP et de store**. |

**État vérifié** : frontend **95/95 verts** (59 avant). Backend **~340 tests**, **1 échec connu**
décrit ci-dessous. `ruff`, `mypy`, `eslint`, `npm run build` verts avant l'interruption.

### La vague 1 n'a pas été refermée

L'agent d'assemblage (7ᵉ) a été **interrompu avant la fin**. Les six lots sont dans l'arbre mais
n'ont jamais été réconciliés entre eux. C'est la première chose à faire.

---

## À faire, dans cet ordre

### 0. Refermer la vague 1 — commence par là

**a) Un test échoue et c'est un vrai défaut, pas un test à corriger :**

```
tests/test_assemblage.py::test_the_shelf_count_of_an_instance_changes_its_geometry
assert await shelves({"nb_etageres": 3}) == 3   ->   obtenu 5
```

Ce qui est **déjà prouvé** (ne le revérifie pas, c'est du temps perdu) :

- le moteur géométrique est **correct en isolation** : `expand_recipe(...)` avec
  `{"nb_etageres": 3}` renvoie bien 3 étagères ;
- `resolve_variants` renvoie bien `{('etagere', 'y'): 3}` ;
- le catalogue déclare bien `variants` sur `bibliotheque`, et il survit à
  `FurnitureTypeCreate.model_validate(...).model_dump()` ;
- `ElementCreate` conserve bien l'entier 3 (il n'est pas coercé en `"3"`) ;
- `api/scene.py` recopie bien `"variants": list(row.variants)` dans le dictionnaire de catalogue ;
- l'ordre des arguments de `expand_recipe` au point d'appel est correct.

Le défaut est donc **entre l'écriture de l'élément par l'API et la lecture du scene graph**.
Pistes non encore explorées : la persistance réelle de `variant_params` en base
(`MutableDict`/JSONB), un validateur de cohérence ajouté en L5 qui viderait le champ, ou le cache
de scène. Instrumente le trajet réel plutôt que de relire le code.

**b) Puis** : `pytest -q`, `ruff check .`, `mypy .` côté backend ; `npm run test`, `npm run lint`,
`npm run build` côté frontend. `alembic upgrade head` → `downgrade -1` → `upgrade head` →
`alembic check`. Régénère l'instantané OpenAPI s'il a dérivé
(`backend/scripts/dump_openapi.py`).

**c)** Traite les points que les agents ont signalés comme nécessaires mais hors de leur périmètre
(champ `hors_perimetre` de leurs rapports).

**Interdit** : supprimer ou neutraliser un test pour le faire passer, et modifier une fixture de
`backend/tests/geometry/fixtures/` (règle `CLAUDE.md`).

### Vague 2 — multi-locataire, métré et devis

C'est la vague qui crée la valeur payante. Détail complet dans `strategie-produit.md` §3.1, §3.2
et §6.

- `organization` + `membership` (rôles owner/admin/editor/viewer) + invitations, et **les champs
  d'entreprise** : SIRET, forme juridique, capital, RCS, adresse, TVA, **assureur décennal,
  numéro de police et couverture** — sans eux aucun devis n'est valable.
  `owner_id` reste une trace de création et cesse d'autoriser quoi que ce soit.
  ⚠ Le test « un membre d'une autre organisation reçoit 404 » s'écrit sur **chaque** endpoint
  **avant** la réécriture des permissions, pas après.
- `app/geometry/quantities.py` : `build_takeoff(scene_graph)` — fonction **pure**, avec fixtures
  calculées à la main. Surface brute, surface nette (les `holes` sont déjà émis), linéaire de
  plinthe, volume, nombre d'unités d'après `unit_width_cm`/`unit_height_cm`.
  Utiliser `net_floor_area_cm2` et **jamais** `floor_area_cm2` pour chiffrer : ce dernier est
  l'aire de la ligne médiane des murs, surévaluée de 6 à 20 %.
- `price_book` / `price_item` par organisation, `quote` / `quote_line`.
  **Montants en centimes entiers**, jamais en flottants. `quote_line` **copie** libellé, prix et
  taux de TVA à l'émission et ne fait aucune jointure de lecture : en France un devis signé est un
  contrat. Numérotation séquentielle sans trou générée **en base**.
- Une règle de correspondance `Covering.material → price_item` : sans elle, un projet de 12 pièces
  demande 60 rattachements à la main et la fonctionnalité est belle en démonstration et pénible en
  production.
- **Factur-X** (PDF/A-3 + XML CII embarqué), générable entièrement en interne avec reportlab.
  Écrire noir sur blanc dans l'interface qu'on **n'est pas** une plateforme de dématérialisation
  agréée et qu'on ne transmet rien à l'administration.

### Vague 3 — livrables de chantier et éditeur professionnel

- **Élévations vectorielles cotées, une page A4 paysage par mur.** `outline` et `holes` sont déjà
  émis en centimètres au bon format : cette fonctionnalité ne dépend techniquement de **rien**.
  C'est l'artefact le plus démontrable du produit, et `services/export_pdf.py` imprime aujourd'hui
  une liste de texte à la place.
- Bouton d'export dans l'interface : les 4 routes d'export n'ont **aucun appelant** dans le
  frontend. Un worker Celery, un broker Redis et un volume sont facturés en production pour zéro
  valeur utilisateur.
- Calepinage : nombre d'unités, de coupes et taux de chute par motif de pose (~15 % en chevron
  contre ~8 % en pose droite). C'est le chiffre qui rend le devis crédible auprès d'un homme de
  métier.
- **Glisser-déposer et mobilier non adossé.** Bloqué par le modèle : `Element.face_id` est
  obligatoire, donc tout meuble est collé à une face — un lit, une table, un îlot sont impossibles.
  Rendre `face_id` nullable et ajouter un placement dans le repère de la pièce est un
  **amendement explicite de la spec §5**, à écrire dans `spec-complete.md` avant de coder.
- Annuler/refaire, saisie numérique des cotes, aimantation, import d'un fond de plan avec
  calibrage à deux clics (premier geste du métier, premier frein à l'adoption).
- Route de **lot** (`POST /projects/{id}/batch`) : déplacer 15 meubles impose aujourd'hui 15
  allers-retours strictement sériels, puisque chaque écriture incrémente la version.

### Vague 4 — l'IA locale

Trois moteurs, par valeur décroissante (`strategie-produit.md` §3.8) : **contrôle de conformité du
plan** (passage < 90 cm, débattement de porte qui percute, allège hors norme…), **calepinage et
chutes**, **aménagement automatique sous contraintes**. Déterministes, testés par fixtures comme
le reste de la géométrie.

### Vague 5 — offres, quotas, encaissement

`plan_catalog` (limites en JSONB, **en base et pas en dur** : une remise doit être une ligne SQL),
`subscription` avec les colonnes d'identifiants externes dès la première migration,
`usage_counter` incrémenté par un **unique `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`**,
`usage_event` append-only avec clé d'idempotence — et **les métriques produit dès maintenant**
(activation, délai jusqu'au premier devis) : reconstituer un historique est impossible.
Murs de paiement, essai déclenché au premier geste monétisé et non à l'inscription, déclassement
en lecture seule et jamais de suppression. Grille tarifaire et justification : `strategie-produit.md` §4.

Il manque aussi, et aucun lot ne les portait : pages légales (mentions, CGU, **CGV** — on ne vend
pas un abonnement sans), export de portabilité RGPD, réinitialisation de mot de passe avec ses
vues, page vitrine et page tarifs, titre dynamique et Open Graph sur le lien partagé, projet de
démonstration à l'inscription (l'état vide actuel est « Aucun projet pour le moment » devant un
canvas blanc).

---

## Méthode

Elle est décrite dans `docs/plan-generation-ia.md` §4 et dans `CLAUDE.md`, et elle a bien
fonctionné sur la vague 1 :

- des agents en parallèle, avec une **propriété exclusive des fichiers** annoncée dans le prompt —
  c'est ce qui rend le parallélisme sûr ;
- un agent d'assemblage en fin de vague, dont c'est le seul travail ;
- un ticket n'est fini que si **ses tests passent réellement**, sortie à l'appui ;
- sous-agent `spec-reviewer` en revue adversariale avant de clore ;
- `PROGRESS.md` mis à jour à chaque vague.

L'audit complet qui a produit cette feuille de route (9 dimensions, 199 constats, 16 lots) a été
réalisé le 2026-08-07. Ses conclusions sont reprises ici et dans `strategie-produit.md` : il n'y a
pas besoin de le refaire.
