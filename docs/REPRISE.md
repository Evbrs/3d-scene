# Point de reprise — chantier « meilleure app de la niche »

*Document de passation. Une session qui démarre ici doit pouvoir reprendre le travail sans
refaire l'audit. Mets-le à jour à la fin de chaque vague.*

Dernière mise à jour : 2026-08-08, fin de la vague 3 (assemblage compris).

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

### Fait — vague 1 « stabilisation » (6 lots, livrés, refermés et verts)

| Lot | Contenu |
|---|---|
| **L1** | La production démarrait pas. Image nginx non privilégiée, passage en **same-origin** (`proxy_pass /api/`), en-têtes de sécurité inclus dans *chaque* location (aucune page HTML n'avait de CSP), service `migrate` one-shot, port 8000 refermé, bordure TLS, `X-Robots-Tag` + `robots.txt` sur les liens de partage, Dockerfile multi-étage, dépendances figées, CI qui démarre réellement la pile. |
| **L2** | Fuite du pool corrigée (`dispose()`), pool dimensionné, `run_in_threadpool` sur tout le CPU (Argon2id à 35 ms, scene graph jusqu'à 83 ms bloquaient la boucle d'évènements), corps de requête bornés (413), `ProxyHeadersMiddleware`, `TrustedHostMiddleware`, limiteur de débit **sur Redis**, journalisation JSON structurée avec `X-Request-Id`, `/health/live` et `/health/ready`, GZip avec liste blanche, `create-superuser`. |
| **L4** | `CheckConstraint` en base sur les 7 tables (`Room(wall_thickness_cm=-5)` était accepté : les `Field(gt=0)` sont **inertes** sur les modèles `table=True`), `order_by` sur les relations (leur absence faisait dessiner dans la mauvaise pièce), `passive_deletes`, `server_default` partout, JSON → JSONB, colonnes réelles sur `sharedview`, `token_version` sur `user`. |
| **L5** | Contrat d'écriture : les trois DELETE étaient en « dernière écriture gagne », le 409 ne renvoyait jamais `current_version`. Validation d'encombrement corrigée (elle comparait une hauteur verticale à une profondeur au sol), non-chevauchement des ouvertures, refus des polygones auto-sécants, cookie `httpOnly` pour le refresh token, partage filtré côté serveur (il livrait la géométrie complète du logement sans authentification). |
| **Géométrie** | Cylindre corrigé (il produisait un **cône** dès que largeur ≠ profondeur : 5 recettes fausses), mitrage des murs (fente verticale à chaque angle), normale et altitude du plafond, `variant_params` branché, menuiseries rendues, `net_floor_area_cm2`, fixtures oblique et en L calculées à la main. |
| **L6** | Rafraîchissement silencieux du jeton (au bout de 30 min d'édition, tout enregistrement échouait en 401 et le travail était perdu), conflits typés, fusion du revêtement (choisir une couleur effaçait matière et motif), touches filtrées, brouillon purgé au changement de pièce, sélecteur de pièce en 3D, découpage du bundle, responsive, **premiers tests de client HTTP et de store**. |

### Fait — vague 2 « la valeur payante » (4 lots + assemblage, tous verts)

| Lot | Contenu |
|---|---|
| **V2-L1** | `organization` / `membership` / `invitation` avec l'identité légale complète (SIRET, forme juridique, capital en **centimes entiers**, RCS, TVA, **décennale**). Quatre rôles ordonnés `viewer < editor < admin < owner`. `app/api/permissions.py` réécrit : l'appartenance **acceptée** autorise, `Project.owner_id` n'autorise plus rien. Migration avec rétro-remplissage. 10 routes. |
| **V2-L2** | `build_takeoff(scene_graph)` — fonction pure, 4 fixtures calculées à la main. Surfaces nettes, linéaires au nu intérieur, plinthe amputée des percements qui touchent le sol, calepinage par motif avec entières / coupes / chutes. |
| **V2-L3** | Élévations vectorielles cotées : une planche A4 paysage par mur, chaînes de cotes, allèges, échelle normalisée **écrite**, cartouche, page de garde. Les 4 routes d'export ont enfin un appelant côté frontend. |
| **V2-L4** | Barème, devis et facture **Factur-X** (PDF/A-3 + XML CII BASIC WL), produits sans aucune dépendance ajoutée. Numérotation sans trou générée en base et attribuée à l'émission seulement. 19 routes. |

Les décisions de contrat prises par cette vague sont écrites dans `spec-complete.md` **§10
(amendements A1 à A3)** — la spec ne décrivait plus la réalité, deux agents l'avaient signalé.

### Fait — vague 3 « éditeur professionnel » (3 lots + assemblage, tous verts)

| Lot | Contenu |
|---|---|
| **V3-L1** | **Mobilier libre** : `Element.face_id` devient nullable, `room_id` / `pos_x_cm` / `pos_y_cm` apparaissent. Un lit, une table, un îlot étaient *impossibles* — la limitation venait du modèle, aucun travail d'éditeur ne pouvait la lever. Trois `CheckConstraint` en base (les `Field` sont inertes sur `table=True`), propriété `Element.anchor_room` comme point de passage unique, validation d'encombrement testant les **quatre coins après rotation** plus les franchissements de côté (les seuls coins ne suffisent pas sur une pièce en L). Fond de plan calibré sur `Room` (6 colonnes, `background_url` validée côté serveur, OWASP A03). Route de **lot** `POST /projects/{id}/batch` : 7 opérations en union discriminée, une transaction, **une** incrémentation de version, tout ou rien, bornée à 100. |
| **V3-L2** | **Fidélité de la 3D** : CSG branché via `three-bvh-csg` (chargé paresseusement, uniquement si la scène contient un nœud `requires_csg`), étanchéité vérifiée avant évaluation avec repli sur la primitive pleine. Éclairage ACES + environnement PMREM procédural, ombres portées. Textures de revêtement procédurales par motif de pose. Isolement **multi-faces**, coupe horizontale, masquage des murs qui font écran. Mode logement complet. `ResourcePool` qui possède et libère tout — les fuites GPU étaient réelles. Sélecteur de couleur par `color_slot`, capture PNG par mur. |
| **V3-L3** | **Éditeur 2D utilisable en relevé** : glisser-déposer depuis une palette groupée par catégorie (recherche insensible aux accents), l'ancrage se décidant **à la dépose** et l'adossement gagnant quand les deux sont possibles. Annuler/refaire exprimé en **appels serveur inverses** et non en instantanés locaux (un instantané écraserait ce qu'un collègue vient d'enregistrer). Magnétisme à trois priorités avec guides, saisie numérique des cotes, longueurs de mur éditables. Sélection multiple, copier/coller, gestes groupés passant tous par la route de lot. Fond de plan et calibrage à deux clics à point fixe. |

**Trois défauts trouvés par les agents pendant leur propre revue, et corrigés** : le test
d'intersection refusait un meuble poussé **pile contre** le mur (le geste le plus courant du
métier) ; un sommet en cours de déplacement figurait dans ses propres candidats d'accroche et
devenait incorrigible ; un seuil d'adossement fixe à 45 cm rendait la pose libre impossible dans
un couloir de 90 cm.

**Corrigé à l'assemblage** : `admin.py::_project_id_of` remontait au projet par `Element.face_id`
seul. Nul sur un meuble libre, `session.get(Face, None)` rend `None` **sans lever** — corriger un
meuble libre depuis le back-office ne purgeait donc pas le cache de scène, et l'ancienne scène
continuait d'être servie. Deux agents l'avaient signalé sans pouvoir y toucher. Le test qui le
couvre a été vérifié en le rejouant contre le code fautif : il échoue bien.

**État vérifié après assemblage de la vague 3** : backend **787 tests** (786 sur SQLite + 1 propre
à PostgreSQL), frontend **368/368** sur 23 fichiers, `ruff`, `mypy --strict`, `eslint`,
`npm run build` verts. **Une seule tête Alembic** (`e7b3c05f1a62`), `upgrade` → `downgrade` →
`upgrade` rejoué sans résidu, `alembic check` sans dérive. Instantané OpenAPI à jour
(**47 chemins**), régénéré et diffé à vide. `test_permissions_locataire.py` : **49 routes**
confrontées à un intrus, plus 3 collections et 9 routes globales classées.

Les décisions de contrat de cette vague sont écrites dans `spec-complete.md` **§10 (amendements
A4, A5, A6)**, rédigées **avant** le code comme le demande `CLAUDE.md`.

**Le cloisonnement entre locataires est la seule chose à ne jamais casser.** Il est tenu par
`backend/tests/test_permissions_locataire.py` : 47 routes confrontées à un membre d'une autre
organisation (404, jamais 403), **plus deux garde-fous de complétude** qui lisent le schéma
OpenAPI publié. Le second — celui qui classe les routes de *liste*, qui ne portent aucun
identifiant — a révélé que `GET /api/quotes` n'avait pas de test de non-fuite. Si tu ajoutes une
route, la suite te forcera à la classer : c'est voulu, ne contourne pas le message d'échec.

### Signalé par la vague 2, délibérément **pas** traité

Ce sont des changements de contrat ou de périmètre : la règle `CLAUDE.md` demande de les signaler,
pas de les improviser en cours de ticket. Ils sont repris dans `spec-complete.md` §10, section
« questions ouvertes ».

1. **`LayingPattern` n'a pas de valeur `diagonal`** : le métré porte son taux de chute (12 %) mais
   aucune saisie ne peut le produire, `Covering.pattern` étant typé sur l'énumération. Une ligne
   d'énumération, une migration, un amendement de §1.
2. **Aucun choix de l'organisation à la création d'un projet.** La règle appliquée est
   déterministe (l'appartenance acceptée la plus ancienne) mais un compte membre de deux
   entreprises ne peut pas désigner la cible. `ProjectCreate` n'a pas le champ.
3. **Aucune vue de back-office** pour les 9 nouveaux modèles. Délibéré côté facturation : un
   `quote_counter` ou une `quote_line` modifiables à la main annulent les deux garanties légales
   du lot (numérotation sans trou, ligne figée). Si elles sont ajoutées, en lecture seule.
4. **Le filigrane d'aperçu n'a aucun appelant** : `render_project_pdf(..., watermark=True)` existe,
   est testé et n'est jamais déclenché — il n'y a pas de modèle d'offre en base avant la vague 5.
   Il est *keyword-only* et décidé par le serveur : ne l'exposez jamais en paramètre de requête.
5. **Rien n'achemine les invitations** : la route rend le jeton, aucun service de mail n'existe
   dans le dépôt. `quote.warnings` n'est pas non plus recalculé si le plan change après coup — le
   document dit ce qu'on savait à l'instant où il a été établi, et aucune route ne le rafraîchit.
6. **La conformité PDF/A-3 n'a pas été confrontée à veraPDF** (aucun validateur hors ligne). Les
   exigences structurelles connues sont satisfaites et testées une par une, mais aucun verdict
   d'outil n'a été observé : ne le présentez pas comme certifié.

### Signalé par la vague 3, délibérément **pas** traité

Questions ouvertes n° 5 et 6 de `spec-complete.md` §10, plus ce que l'assemblage a constaté.

7. **Aucune vérification visuelle nulle part.** Les trois lots ont été écrits sans navigateur dans
   l'environnement. Le tone mapping, les ombres, l'environnement PMREM, les textures de
   revêtement, le glisser-déposer, les guides d'aimantation, le pincement à deux doigts et le fond
   de plan calibré **n'ont jamais été vus**. Les tests couvrent la logique et le montage, pas les
   pixels ni les évènements pointeur réels de Konva. **C'est le premier point à confronter à un
   écran**, avant d'ajouter quoi que ce soit.
8. **Le changement d'ancrage n'existe pas** (question ouverte n° 5) : décrocher une applique du mur
   pour la poser au sol impose de supprimer puis recréer. Délibéré — les deux repères n'ont pas la
   même signification — mais à reprendre si le geste devient courant.
9. **Un meuble libre n'apparaît pas dans le dossier d'élévations** (question ouverte n° 6, énoncé
   corrigé à l'assemblage). `export_pdf.py` parcourt les éléments par face ; la colonne « nombre
   d'éléments » du récapitulatif de pièce les sous-compte donc. L'éditeur sait maintenant en poser
   en masse, ce qui rend l'écart bien plus visible qu'à son ouverture. **Le métré n'est pas
   concerné** : il chiffre des surfaces et du calepinage, et n'a jamais itémisé de mobilier.
10. **Aucune route de téléversement du fond de plan.** Deux chemins existent côté éditeur : une
    adresse validée et enregistrée, ou un fichier local via `URL.createObjectURL` — **aperçu de
    session seulement**, annoncé comme tel, perdu au rechargement. Le calibrage fait dessus est
    enregistré, lui : l'échelle et le décalage deviennent orphelins si l'image ne revient pas.
11. **La validation de `background_url` est écrite deux fois** (`RoomBase._validate_background_url`
    en Python, `calibration.isBackgroundUrlAllowed` en TS). Délibéré : défense en profondeur OWASP
    plus retour immédiat. Si la règle serveur change, **changer les deux**.
12. **`vite.config.ts` n'applique pas `templateCompilerOptions` de TresJS.** Les balises `Tres*`
    passent donc par `resolveComponent`, ce qui fonctionne — c'était déjà le cas avant la vague 3 —
    mais produit un avertissement Vue en développement. Le correctif est d'une ligne ; il a été
    laissé de côté faute de pouvoir en observer l'effet sans navigateur, et parce qu'une régression
    à cet endroit casserait tout le rendu 3D d'un coup.
13. **Aucune mesure de performance sur un plan chargé.** Le rendu Konva reste en `v-for` sans mise
    en cache de nœuds ; sur une pièce à 200 meubles, l'aperçu de déplacement recalcule toutes les
    emprises à chaque image. Non mesuré, donc affirmé dans aucun sens.

---

## À faire, dans cet ordre

### D'abord : ouvrir l'application dans un navigateur

Ce n'est pas une vague, c'est un préalable. Trois lots de rendu et d'interaction ont été livrés
sans qu'un seul pixel soit observé (point 7 ci-dessus). Confronter la 3D et l'éditeur 2D à un écran
coûte une heure et peut invalider des choix qu'aucun test ne discrimine.

### Vague 4 — l'interface commerciale

*Le glisser-déposer, le mobilier libre, l'annuler/refaire, l'aimantation, la cotation, le fond de
plan calibré et la route de lot, qui figuraient ici, ont été livrés par la vague 3.*

- **Interface des devis et du barème : rien n'existe.** Les 31 opérations ajoutées par les lots
  V2-L1 et V2-L4 n'ont **aucun appelant** dans `frontend/src/api/client.ts`. Le produit sait établir un devis
  Factur-X et ne sait pas le montrer. C'est, de loin, le premier poste de valeur restant.
- **Le mobilier libre dans le dossier d'élévations** (point 9) : le plan coté de la pièce doit
  montrer ce qui est posé au sol, et le récapitulatif cesser de le sous-compter.

### Vague 5 — l'IA locale

Trois moteurs, par valeur décroissante (`strategie-produit.md` §3.8) : **contrôle de conformité du
plan** (passage < 90 cm, débattement de porte qui percute, allège hors norme…), **calepinage et
chutes**, **aménagement automatique sous contraintes**. Déterministes, testés par fixtures comme
le reste de la géométrie.

Le mobilier libre de la vague 3 est ce qui rend le premier moteur réellement utile : un passage de
moins de 90 cm se mesure entre des meubles posés au sol, pas entre des appliques murales.

### Vague 6 — offres, quotas, encaissement

`plan_catalog` (limites en JSONB, **en base et pas en dur** : une remise doit être une ligne SQL),
`subscription` avec les colonnes d'identifiants externes dès la première migration,
`usage_counter` incrémenté par un **unique `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`**,
`usage_event` append-only avec clé d'idempotence — et **les métriques produit dès maintenant**
(activation, délai jusqu'au premier devis) : reconstituer un historique est impossible.
Murs de paiement, essai déclenché au premier geste monétisé et non à l'inscription, déclassement
en lecture seule et jamais de suppression. Grille tarifaire et justification : `strategie-produit.md` §4.
C'est aussi la vague qui donnera enfin un appelant à `render_project_pdf(..., watermark=True)`.

Il manque aussi, et aucun lot ne les portait : pages légales (mentions, CGU, **CGV** — on ne vend
pas un abonnement sans), export de portabilité RGPD, réinitialisation de mot de passe avec ses
vues, page vitrine et page tarifs, titre dynamique et Open Graph sur le lien partagé, projet de
démonstration à l'inscription (l'état vide actuel est « Aucun projet pour le moment » devant un
canvas blanc).

---

## Méthode

Elle est décrite dans `docs/plan-generation-ia.md` §4 et dans `CLAUDE.md`, et elle a bien
fonctionné sur les vagues 1 à 3 :

- des agents en parallèle, avec une **propriété exclusive des fichiers** annoncée dans le prompt —
  c'est ce qui rend le parallélisme sûr ;
- un agent d'assemblage en fin de vague, dont c'est le seul travail ;
- un ticket n'est fini que si **ses tests passent réellement**, sortie à l'appui ;
- sous-agent `spec-reviewer` en revue adversariale avant de clore ;
- `PROGRESS.md` mis à jour à chaque vague.

Deux réglages appris sur la vague 3, qui a livré trois lots en parallèle sans conflit :

- **un fichier généré ne se fusionne pas.** `openapi-snapshot.json` a été régénéré par un agent
  puis revérifié à l'assemblage. La règle : les agents ne le touchent pas, l'assemblage le
  régénère une fois, en dernier, et diffe à vide.
- **ce qu'un agent voit sans pouvoir y toucher doit remonter dans son rapport, pas dans le code.**
  Le défaut de `admin.py` a été signalé par deux agents et corrigé par le troisième, sans qu'aucun
  ne sorte de son périmètre. C'est le mécanisme qui a le mieux marché — mais il n'a de valeur que
  si l'assemblage traite réellement la liste : un signalement non traité est un défaut connu et
  laissé en place.

L'audit complet qui a produit cette feuille de route (9 dimensions, 199 constats, 16 lots) a été
réalisé le 2026-08-07. Ses conclusions sont reprises ici et dans `strategie-produit.md` : il n'y a
pas besoin de le refaire.
