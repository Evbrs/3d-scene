# Point de reprise — chantier « meilleure app de la niche »

*Document de passation. Une session qui démarre ici doit pouvoir reprendre le travail sans
refaire l'audit. Mets-le à jour à la fin de chaque vague.*

Dernière mise à jour : 2026-08-08, fin des vagues 4 et 5 (assemblage compris).

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
`backend/tests/test_permissions_locataire.py` : **49 routes** confrontées à un membre d'une autre
organisation (404, jamais 403), **plus deux garde-fous de complétude** qui lisent le schéma
OpenAPI publié. Le second — celui qui classe les routes de *liste*, qui ne portent aucun
identifiant — a révélé que `GET /api/quotes` n'avait pas de test de non-fuite, puis a imposé le
classement de `GET /api/auth/me/export` à la vague 5. Si tu ajoutes une route, la suite te forcera
à la classer : c'est voulu, ne contourne pas le message d'échec. Seule exception assumée :
`GET /api/plans` est **publique** et n'appartient à aucun locataire — une grille de prix s'affiche
avant l'inscription.

### Fait — vagues 4 et 5, livrées ensemble (4 lots + assemblage, tous verts)

Les deux vagues ont été menées en parallèle par quatre agents, puis assemblées. Ce qui figurait
ci-dessous comme « à faire » pour les vagues 4, 5 et 6 est en grande partie livré.

| Lot | Contenu |
|---|---|
| **V45-L1** | **Combler les manques de la vague 3.** Le métré compte le mobilier à l'unité (par recette *et* par gabarit : deux lits de 140 et 160 ne s'achètent pas ensemble), le plan coté le dessine en trait fin pointillé avec son emprise, la page de garde et les récapitulatifs cessent de le sous-compter. `LayingPattern.DIAGONAL` ajoutée — et **aucune migration n'était nécessaire**, contrairement à ce que la question ouverte n° 1 annonçait : le motif vit dans le blob JSON `Face.covering`. Garde-fou **exécutable** sur `floor_area_cm2` : la valeur est empoisonnée dans le scene graph et la sortie doit être rigoureusement identique. Défaut trouvé en chemin : `PATTERN_LABELS` contenait une clé `brick` qui n'a jamais été une valeur de l'énumération et n'avait pas `staggered` — une pose à coupe de pierre s'imprimait « staggered » en toutes lettres sur un document de chantier. |
| **V45-L2** | **Socle légal, RGPD et cycle de vie du compte.** Table `UserToken` (hachage seul, ligne consommée conservée). `PATCH /auth/password`, `POST /auth/password/forgot` (202 constant), `/reset`, `DELETE /auth/me`, `GET /auth/me/export`, `POST /auth/demo-project`. **`User.token_version` était inerte depuis la vague 1** : aucun jeton ne le portait, aucune dépendance ne le lisait — il est maintenant dans chaque JWT et confronté à chaque requête, donc changer de mot de passe ferme réellement toutes les sessions. Quatre documents légaux (gabarits, avertissement de relecture en tête), `docs/rgpd.md`, projet de démonstration entièrement chiffrable, gabarits de pièces, titre d'onglet dynamique. |
| **V45-L3** | **Le moteur d'intelligence du plan**, algorithmique et local : 13 règles de conformité à identifiant stable, seuils sourcés et republiés dans le rapport, calepinage avancé (sens de pose, position de la première rangée, plinthes avec réemploi des chutes), aménagement automatique sous contraintes dures avec score explicite en quatre termes. Trois routes, quatre fixtures calculées à la main, et **deux croisements** qui valent plus que le reste : le calepinage par défaut retombe exactement sur le décompte du métré, et une implantation proposée repasse sous le contrôle de conformité sans un seul bloquant. |
| **V45-L4** | **Offres, quotas et compteurs d'usage** (la vague 6 anticipée). `plan_catalog` / `subscription` / `usage_counter` / `usage_event`, grille §4 semée sans destruction, incrément atomique, idempotence par clé. Trois murs : devis, export sans filigrane, deuxième chantier. Essai Pro 14 jours sans carte déclenché **au premier geste monétisé**. Déclassement par `Project.archived_at` : rien n'est supprimé, la lecture et l'export restent ouverts, seule l'écriture est refusée. `render_project_pdf(..., watermark=True)` a enfin ses appelants, sur les deux chemins. Pages `/tarifs` et `/abonnement` alimentées par la base. |

**Corrigé à l'assemblage** — quatre défauts qu'aucun agent ne pouvait atteindre seul :

1. **`tasks/exports.py::_load_project` ne nommait le mobilier que par face.** Un meuble **libre**
   (A4) n'y recevait jamais son nom de catalogue : sur un export réel, un lit s'imprimait
   « Meuble » sur le plan coté — la seule planche du dossier qui le dessine (A7), donc sans
   rattrapage possible ailleurs. Le test qui le couvre a été rejoué contre le code fautif : il
   échoue bien.
2. **`diagonal` existait côté serveur et était insaisissable côté client.** L'union
   `LayingPattern` de `frontend/src/api/types.ts` ne la portait pas, et `viewer/textures.ts`
   retombait silencieusement sur la pose droite — repli propre, mais visuellement faux.
3. **Le panneau d'inspection n'était branché sur rien.** Types déplacés dans `api/types.ts`, trois
   appels ajoutés au client HTTP, panneau monté dans l'éditeur 2D avec `centerOn` exposé par
   `PlanCanvas` pour le recentrage.
4. **`ai_runs` était posée, affichée sur la page compte et jamais incrémentée** — un compteur qui
   affiche toujours zéro est un compteur qui ment. Comptée **par version de plan** et non par
   clic, les trois moteurs étant déterministes.

**État vérifié après assemblage** : backend **1113 tests** (1112 sur SQLite + 1 propre à
PostgreSQL, 1 ignoré), frontend **428/428** sur 31 fichiers, `ruff`, `mypy --strict`, `eslint`,
`npm run build` verts. **Une seule tête Alembic** (`f3d47c8a1b56`), `upgrade` → `downgrade base` →
`upgrade` rejoué sans résidu, `alembic check` sans dérive. Instantané OpenAPI à jour
(**58 chemins, 79 opérations**), régénéré et diffé à vide. `test_permissions_locataire.py` :
**49 routes** confrontées à un intrus, plus **4 collections** et **12 routes globales** classées.

Les décisions de contrat de ces deux vagues sont écrites dans `spec-complete.md` **§10 (amendements
A7 à A12)**. A7 et A8 ont été rédigés avant le code par leur agent ; A9 à A12 l'ont été à
l'assemblage, sur la base de ce que les agents ont explicitement demandé de consigner — trois
d'entre eux ne pouvaient pas éditer le fichier sans entrer en collision.

### Signalé par la vague 2, délibérément **pas** traité

Ce sont des changements de contrat ou de périmètre : la règle `CLAUDE.md` demande de les signaler,
pas de les improviser en cours de ticket. Ils sont repris dans `spec-complete.md` §10, section
« questions ouvertes ».

1. ~~**`LayingPattern` n'a pas de valeur `diagonal`**~~ — **traité par la vague 4** (amendement A8).
   La migration annoncée ici n'avait pas lieu d'être : le motif vit dans le blob JSON
   `Face.covering`, l'énumération n'est le type d'aucune colonne. L'union TypeScript et la cellule
   de texture ont été complétées à l'assemblage.
2. **Aucun choix de l'organisation à la création d'un projet.** La règle appliquée est
   déterministe (l'appartenance acceptée la plus ancienne) mais un compte membre de deux
   entreprises ne peut pas désigner la cible. `ProjectCreate` n'a pas le champ.
3. **Aucune vue de back-office** pour les 9 nouveaux modèles. Délibéré côté facturation : un
   `quote_counter` ou une `quote_line` modifiables à la main annulent les deux garanties légales
   du lot (numérotation sans trou, ligne figée). Si elles sont ajoutées, en lecture seule.
4. ~~**Le filigrane d'aperçu n'a aucun appelant**~~ — **traité par la vague 5** (amendement A11). Le
   PDF filigrané se télécharge vraiment, sur le chemin synchrone comme sur le chemin Celery, et la
   décision est prise par le serveur d'après le palier. Il n'a jamais été exposé en paramètre de
   requête, et un test l'exige.
5. **Rien n'achemine les invitations ni les réinitialisations de mot de passe** : les routes rendent
   le jeton, aucun service de mail n'existe dans le dépôt (question ouverte n° 11). `quote.warnings`
   n'est pas non plus recalculé si le plan change après coup — le document dit ce qu'on savait à
   l'instant où il a été établi, et aucune route ne le rafraîchit (question ouverte n° 7, analysée
   par la vague 4 : le correctif évident est faux, rafraîchir les seuls avertissements d'un
   brouillon le rend incohérent).
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
9. ~~**Un meuble libre n'apparaît pas dans le dossier d'élévations**~~ — **traité par la vague 4**
   (amendement A7). Le plan coté le dessine, le récapitulatif et la page de garde le comptent, et
   le métré l'itémise à l'unité — ce dernier point est un ajout de périmètre §4, assumé comme tel.
   Il reste volontairement absent des planches d'élévation : une élévation est la vue d'un mur.
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

### Signalé par les vagues 4 et 5, délibérément **pas** traité

Questions ouvertes n° 9 à 15 de `spec-complete.md` §10. Les quatre premières sont reprises
ci-dessus dans « Vague 6 ». Restent celles qui sont des limites de modèle ou de méthode :

14. **La main d'une porte et son sens de battement ne sont pas modélisés** (question ouverte n° 9).
    Le contrôle de conformité essaie les **deux** ferrages : une porte n'est en défaut que si aucun
    ne passe, et si un seul est libre c'est un conseil (« le plan impose la main de la porte »).
    C'est le choix le moins faux à modèle constant, mais il est structurant : le jour où `Element`
    reçoit un champ de ferrage, les règles `porte.*` changent de nature.
15. **Les seuils du contrôle de conformité n'ont pas été validés par un homme de métier.** Ils sont
    relevés dans la réglementation et l'usage courant, chaque source est écrite à côté de son champ,
    et le rapport republie les seuils appliqués — mais l'avertissement de `strategie-produit.md` §2
    s'applique mot pour mot. **Ne jamais afficher « non conforme »** là où on peut afficher « sous
    le seuil de X cm ».
16. **Le point 7 reste entier : toujours aucune vérification visuelle.** Les vagues 4 et 5 ont ajouté
    sept vues (`PricingView`, `AccountView`, `AccountSettingsView`, `LegalView`, `OnboardingView`,
    `ForgotPasswordView`, `ResetPasswordView`) et un panneau d'inspection, tous écrits sans
    navigateur. La liste des écrans jamais vus s'allonge à chaque vague.
17. **Le déclassement n'est réconcilié qu'à la consultation de l'abonnement**, le dépôt n'ayant pas
    d'ordonnanceur. Un chantier excédentaire reste donc modifiable tant que personne n'ouvre la page
    compte. La règle dure — « on bloque la création, jamais la lecture » — est tenue dans tous les
    cas. `enforce_active_project_limit` est une fonction pure sur session, prête pour un `beat`.
18. **Les réponses 402 / 429 / 403 ne sont pas déclarées dans le schéma OpenAPI** des routes
    concernées (`responses=`). Le frontend les lit par `ApiError.isPaywalled` / `.requiredPlan`,
    donc le comportement est correct, mais le contrat publié est incomplet.

---

## À faire, dans cet ordre

### D'abord : ouvrir l'application dans un navigateur

Ce n'est pas une vague, c'est un préalable, et il est **plus urgent qu'à la vague 3**. Cinq lots de
rendu et d'interaction, plus sept vues et un panneau d'inspection, ont été livrés sans qu'un seul
pixel soit observé (points 7 et 16 ci-dessus). Confronter la 3D, l'éditeur 2D, la page tarifs et le
parcours d'inscription à un écran coûte une heure et peut invalider des choix qu'aucun test ne
discrimine.

### Vague 6 — mettre en ligne pour de vrai

Quatre points bloquent une mise en production, et aucun n'est un problème d'architecture. Ils sont
tous écrits en questions ouvertes dans `spec-complete.md` §10.

1. **Brancher un service d'envoi de courriel** (question ouverte n° 11). La réinitialisation de mot
   de passe fabrique le jeton et ne l'achemine nulle part : elle est **inutilisable en ligne**. Les
   invitations sont dans le même état depuis la vague 2. C'est le point n° 1 de `docs/rgpd.md` §5.
2. **Faire relire les quatre documents légaux par un juriste** (question ouverte n° 13). Tant qu'ils
   portent leurs marqueurs `[ENTRE CROCHETS]` et leur avertissement de relecture, aucune CGV n'est
   opposable et aucun abonnement ne devrait être encaissé.
3. **Appliquer les durées de conservation annoncées** (question ouverte n° 12). Ni purge des comptes
   inactifs, ni purge des documents commerciaux à dix ans. Celery est en place, c'est un lot court.
   Annoncer une durée sans l'appliquer est en soi un manquement.
4. **Intégrer l'encaissement** (question ouverte n° 10). Les colonnes d'identifiants externes
   existent, vides, depuis la première migration. Sans encaissement il n'y a pas de route de
   changement de palier — et c'est volontaire, une telle route laisserait n'importe quel
   administrateur s'attribuer Entreprise gratuitement.

### Ensuite — finir de brancher ce qui existe

Le backend a des capacités que l'interface n'expose pas. C'est aujourd'hui le premier écart entre
ce que le produit sait faire et ce qu'un artisan peut en tirer.

- **Le calepinage et l'aménagement n'ont aucun écran** (question ouverte n° 15). `readLayingPlan` et
  `proposeLayouts` existent dans `frontend/src/api/client.ts`, rien ne les appelle. Le panneau
  d'inspection, lui, n'est monté que dans l'éditeur 2D — pas dans le viewer 3D, alors que le
  composant a été écrit pour les deux.
- **Interface des devis et du barème.** Les opérations des lots V2-L1 et V2-L4 restent sans
  appelant côté frontend : le produit sait établir un devis Factur-X et ne sait toujours pas le
  montrer.
- **Le mobilier ne remonte pas dans l'export CSV du métré** (`app/api/takeoff.py`, `CSV_COLUMNS` est
  figé et une ligne = une face). Ce CSV est le pont vers le classeur de prix de l'artisan.
- **Les quotas déclarés et non appliqués** (question ouverte n° 10) : `rooms_per_project`,
  `share_link_days`, `max_seats`. Ils sont affichés sur la page tarifs, ce qui les rend
  contractuels avant d'être exécutés.
- **`shared_view_hits`, `api_calls` et `drop_off`** sont posés (métrique, compteur, journal,
  affichage) et jamais incrémentés. `shared_view_hits` n'a **pas** été branché à l'assemblage
  délibérément : compter chaque ouverture d'un lien public transforme une lecture non authentifiée
  en écriture, donc en levier de charge sur la base pour un visiteur anonyme. Si on le branche, ce
  doit être avec une agrégation, pas une ligne par vue.

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
