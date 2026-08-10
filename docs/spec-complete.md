# Spec complète — fonctionnalités et architecture
## Éditeur de plan de rénovation 2D → 3D

*Spec fonctionnelle et technique de référence, sans restriction de périmètre : aucune fonctionnalité n'est "hors scope" par principe. C'est le contrat que le développement (assisté par IA, voir `plan-generation-ia.md`) doit respecter — toute divergence passe par une modification explicite de ce document, jamais par une décision silencieuse en cours de code.*

---

## 1. Vision fonctionnelle complète

- Multi-projets, multi-pièces par projet
- Éditeur 2D sans limite artificielle (polygones libres, plafond en face à part entière, motifs de pose avancés — chevron, bâton rompu)
- Bibliothèque de mobilier générique **paramétrique**, volontairement large (voir §4)
- Catalogue de revêtements complet (couleur, matière, dimensions d'unité, motif de pose)
- **Vue 3D complète** : plusieurs points de vue, isolement d'une ou plusieurs faces, transparence, partage de vue (voir §3)
- Génération de devis chiffré (rattaché aux éléments du plan)
- Collaboration temps réel sur un même projet
- Export PDF et image, y compris par face isolée (voir §3.5)

Rien n'est retiré. Le §7 (feuille de route) donne l'ordre logique de construction.

---

## 2. Ce qui ne change pas par rapport au premier document

Le modèle de base (Project → Room → Face → Element), la logique de faces lettrées automatiquement, la bibliothèque d'ouvertures (portes battantes/coulissantes, fenêtres), et le choix Vue 3 + Konva pour le 2D restent valables. Ce document les **étend**, il ne les remplace pas. Réfère-toi au document précédent pour ces bases ; ici on ajoute la couche 3D et le système de mobilier paramétrique.

---

## 3. Le système 3D — cœur de la demande

### 3.1 Principe général : du plan 2D à la scène 3D

Le backend Python calcule un **scene graph** (arbre de données décrivant la scène : murs extrudés, ouvertures, meubles positionnés) à partir des données du plan 2D, et l'envoie en JSON au frontend. Le frontend (Three.js) ne fait que **traduire ce JSON en objets 3D** — aucune logique métier côté client.

Pourquoi côté backend plutôt que côté client : c'est testable unitairement avec des fixtures de référence (voir P11 et `plan-generation-ia.md`), et c'est réutilisable pour d'autres consommateurs futurs (export, génération de miniatures, etc.) sans dupliquer la logique en JS.

- Chaque face (mur) devient un mur extrudé : un rectangle de `longueur × hauteur`, épaisseur définie par un nouveau champ `wall_thickness_cm` sur la pièce
- Chaque ouverture (porte, fenêtre) devient un **trou** dans ce mur
- Chaque élément posé sur une face devient un objet 3D positionné selon ses `x_offset_cm` / `y_offset_cm` déjà stockés en 2D, projetés en profondeur selon l'épaisseur du mur

### 3.2 Découpe des ouvertures dans les murs

Deux approches, à traiter comme un vrai cas d'école (voir aussi §8, cas 5) :
- **Approximation simple** (à construire en premier) : le mur est un `THREE.Shape` avec un trou rectangulaire (`shape.holes`), extrudé via `ExtrudeGeometry`. Rapide, largement suffisant visuellement pour des ouvertures rectangulaires.
- **CSG complet** (amélioration ciblée ensuite) : la librairie `three-bvh-csg` permet des opérations booléennes (soustraction) entre géométries — utile pour des ouvertures non rectangulaires, ou pour construire des meubles composés (voir §4.3, vasque/baignoire). Elle est présentée par son auteur comme nettement plus rapide que les anciennes librairies CSG trois.js à base de BSP, mais reste qualifiée d'expérimentale et exige des géométries "étanches" (two-manifold) pour fonctionner correctement.

### 3.3 Système de caméra et points de vue

Prévoir plusieurs **presets de caméra**, stockés comme configuration réutilisable :
- **Vue du dessus** (orthographique) — reprend exactement le plan 2D, sert de repère
- **Vue isométrique** (perspective 3/4) — vue d'ensemble "catalogue"
- **Vue par face** (orthographique, une par face) — caméra positionnée le long de la normale sortante de la face (calculée à partir du vecteur du mur, tourné à 90°), qui cadre exactement cette face comme une élévation à plat
- **Orbite libre** (perspective, `OrbitControls`) — exploration manuelle

### 3.4 Isolement de face(s) et transparence

C'est le point que ton artifact précédent ne couvrait pas encore. Organiser la scène Three.js en **groupes par face** (`THREE.Group` par mur + un groupe sol + un groupe mobilier, chaque objet 3D taggé avec le label de sa face en `userData.faceLabel`), pour permettre :
- **Isolement** : afficher uniquement la face A, ou A+B, ou l'ensemble — en togglant `group.visible`
- **Transparence** : deux niveaux possibles — masquer complètement une face, ou la rendre semi-transparente (`material.transparent = true` + `opacity`) pour garder le contexte spatial tout en mettant une face en avant. Le deuxième mode est en général plus lisible qu'un simple show/hide : on garde les murs voisins comme repère visuel.
- Interface : une liste de faces avec, pour chacune, un état à trois positions (visible / transparente / masquée)

### 3.5 Capture et partage de vue

Deux besoins distincts à ne pas confondre :

**Capture d'image** — bouton "capturer cette vue" qui exporte le canvas Three.js en PNG (`renderer.domElement.toDataURL()`), pour la configuration de caméra/visibilité active à l'instant T. Ce mécanisme, appliqué à chaque vue "par face", te donne gratuitement les images pour l'export PDF détaillé par mur (recoupe directement le besoin d'export du document précédent).

**Partage de vue** (lien permalien) — un modèle `SharedView` (`project_id`, `state` en JSON : faces visibles/transparentes, preset de caméra, position libre si orbite modifiée) exposé par un endpoint public en lecture seule, sans authentification. C'est un bon exercice d'API "publique mais restreinte" (rate limiting, pas d'info sensible exposée) — pattern qu'on retrouve dans plein d'outils pro (Figma, CodeSandbox).

---

## 4. Mobilier générique paramétrique

### 4.1 Principe : composition par primitives, pas d'assets importés

Chaque type de meuble est une **recette de composition**, pas un modèle 3D fixe. Un `FurnitureType` décrit une liste de primitives (boîte, cylindre...) positionnées en **coordonnées relatives** (fractions de 0 à 1 de la boîte englobante), chacune associée à un "emplacement couleur" (`color_slot`). Une instance du meuble fournit les dimensions réelles (largeur/hauteur/profondeur en cm) et les couleurs choisies pour chaque `color_slot` — le rendu 3D recalcule alors les primitives à la bonne échelle.

Exemple concret pour une commode :

```json
{
  "name": "Commode",
  "category": "rangement",
  "color_slots": ["corps", "facade", "poignee"],
  "parts": [
    {"type": "box", "rel_position": [0.5, 0.5, 0.5], "rel_size": [1, 1, 1], "color_slot": "corps"},
    {"type": "box", "rel_position": [0.5, "auto", 1.01], "rel_size": [0.9, 0.18, 0.02],
     "color_slot": "facade", "repeat_y": 4, "gap": 0.02}
  ]
}
```

`repeat_y` répète la façade N fois verticalement (le nombre de tiroirs devient un paramètre d'instance, pas une nouvelle géométrie codée en dur).

### 4.2 Meubles qui bénéficient du CSG (§3.2)

Certains objets ne se réduisent pas à des boîtes empilées :
- **Vasque / lavabo** : intersection d'une boîte et d'une forme creusée (cylindre ou demi-sphère) → réutilise directement `three-bvh-csg`
- **Baignoire** : soustraction d'une boîte légèrement plus petite dans une boîte pleine

Ça évite de dupliquer la logique CSG : le module construit pour les ouvertures de mur (§3.2) sert aussi ici.

### 4.3 Catalogue cible (générique, pas de marque)

| Pièce | Éléments |
|---|---|
| Général | porte battante, porte coulissante, fenêtre, radiateur, prise, interrupteur, luminaire (applique/suspension) |
| Salle de bain | vasque, meuble sous-vasque, baignoire, bac de douche, WC, miroir, colonne de rangement, panier à linge, barre d'appui |
| Chambre | lit, commode, armoire, table de chevet, bureau |
| Salon | canapé, table basse, meuble TV, bibliothèque/étagère |
| Cuisine | meuble bas, meuble haut, îlot, table, chaise |

Chaque ligne = une entrée `FurnitureType` (recette de composition), pas un asset à modéliser à la main un par un dans un logiciel 3D externe — cohérent avec ta volonté de rester générique et de ne pas dépendre de bibliothèques de marques.

### 4.4 Personnalisation à l'instanciation

Pour approcher un objet réel (comme ton radiateur "Create" vert) sans avoir le vrai modèle : largeur/hauteur/profondeur libres, couleur par `color_slot`, et pour certains types un paramètre de variation (nombre de tiroirs, présence d'un dossier, etc.) — défini dans la recette du `FurnitureType`, pas dans le moteur de rendu générique.

---

## 5. Modèle de données — ajouts

```
Room
 ├─ wall_thickness_cm          (nouveau — nécessaire pour l'extrusion 3D)
 └─ Face
     └─ Element
         ├─ furniture_type_id  (nullable — référence un FurnitureType si mobilier)
         ├─ colors             (JSON: {slot: couleur} si mobilier)
         └─ variant_params     (JSON libre: nb_tiroirs, etc.)

FurnitureType
 ├─ name, category
 ├─ color_slots  (liste)
 └─ parts        (JSON: liste de primitives, voir §4.1)

SharedView
 ├─ project_id
 ├─ token (slug public)
 ├─ state (JSON: faces visibles/transparentes, preset caméra, position libre)
 └─ created_at
```

---

## 6. Stack technique mise à jour

| Couche | Choix | Pourquoi |
|---|---|---|
| 2D | Vue 3 + Konva (`vue-konva`) | inchangé, bindings officiels Vue |
| 3D | Vue 3 + **TresJS** (`@tresjs/core`) sur Three.js | wrapper déclaratif Vue pour Three.js, actif et maintenu, cohérent avec le reste du stack Vue |
| Géométrie booléenne | `three-bvh-csg` | le plus rapide des CSG pour Three.js actuellement, malgré son statut expérimental |
| Backend | **FastAPI** | async natif, typé de bout en bout, standard qui monte pour les projets API-first en 2026 (voir §6.1) |
| ORM / validation | **SQLModel** (SQLAlchemy + Pydantic) | fait par l'auteur de FastAPI pour éviter la duplication modèle DB / schéma API ; possibilité de redescendre en SQLAlchemy pur si besoin de contrôle fin |
| Migrations | **Alembic** | équivalent des migrations Django, mais explicite plutôt qu'auto-généré par magie — bon exercice pour comprendre ce qu'une migration fait réellement |
| Admin / back-office | **SQLAdmin** | admin CRUD auto-généré à partir des modèles SQLAlchemy/SQLModel, pour combler l'absence d'admin natif de FastAPI (gestion du catalogue `FurnitureType` notamment) |
| Auth | JWT via le pattern officiel FastAPI (`OAuth2PasswordBearer` + `pwdlib`/`pyjwt`) | FastAPI ne fournit pas d'auth intégrée ; ce pattern est celui du tutoriel officiel, donc le plus fiable à faire générer par un agent IA (très bien documenté, faible risque d'API inventée) ; `fastapi-users` existe mais est en mode maintenance, pas un bon choix long terme |
| Tâches async | Celery + Redis | standard le plus répandu et le plus transférable sur le marché de l'emploi, indépendant du framework web (fonctionne à l'identique avec FastAPI) |
| Base de données | PostgreSQL | JSON pour la géométrie, tables normalisées pour le reste |
| Calcul géométrique | Python + `numpy` | scene graph, normales, extrusions |

> **Amendement (ticket P2), ligne « Auth »** — cette ligne citait initialement `passlib` /
> `python-jose`. Vérification faite au moment de l'implémentation : `passlib` n'a plus été
> publié depuis le 8 octobre 2020, et avec `bcrypt` 5.0 il lève
> `AttributeError: module 'bcrypt' has no attribute '__about__'` puis échoue au hachage
> (reproduit en environnement jetable ; avec `bcrypt` 4.1 l'erreur est encore rattrapée en
> interne). Le tutoriel officiel FastAPI — la raison même invoquée par cette ligne — s'appuie
> désormais sur `pwdlib` et `pyjwt`. Le critère énoncé par la spec est donc mieux respecté par
> `pwdlib` (Argon2id) + `pyjwt` que par les deux librairies nommées à l'origine. Aucune autre
> décision d'architecture n'est modifiée.

### 6.1 Pourquoi FastAPI plutôt que Django

Sous le cadrage MVP précédent, Django avait un avantage concret : son admin et son auth intégrés font gagner du temps. Ce n'est plus le critère qui compte ici. FastAPI est devenu le choix par défaut recommandé pour les projets API-first en 2026 — Django restant surtout préférable pour les applications à forte composante back-office, précisément parce qu'il inclut nativement ce que FastAPI n'a pas. Vu que l'objectif est d'apprendre un maximum, construire soi-même ce que Django offrirait gratuitement (auth, admin) devient une opportunité plutôt qu'un coût — d'où `SQLAdmin` et une auth JWT écrite à la main plutôt que des raccourcis.

---

## 7. Séquencement des phases de développement

*Ordre détaillé, avec découpage en tickets exécutables : voir `plan-generation-ia.md`.*

| Phase | Contenu technique | Livrable |
|---|---|---|
| P1 | ORM SQLModel/SQLAlchemy, migrations Alembic, relations | Modèles Project/Room/Face/Element |
| P2 | Auth (hachage, JWT, dépendances FastAPI), permissions objet | Comptes, propriété des projets |
| P3 | Schémas Pydantic imbriqués, validation | API CRUD du plan 2D |
| P4 | Canvas interactif (Konva + Vue) | Éditeur 2D complet |
| P5 | Modélisation de données génériques | Catalogue `FurnitureType` paramétrique |
| P6 | Calcul géométrique en Python (`numpy`) | Génération du scene graph 3D côté backend |
| P7 | Rendu déclaratif 3D (TresJS/Three.js) | Viewer 3D : caméras, isolement de face, transparence |
| P8 | API publique restreinte | Partage de vue (`SharedView`) |
| P9 | Tâches asynchrones (Celery) | Export PDF/image en tâche de fond |
| P10 | Performance (indexation, eager loading SQLAlchemy, cache) | Étude de cas appliquée (§8) |
| P11 | Tests (`pytest`, `httpx.AsyncClient`, `factory_boy`) | Couverture des règles métier |
| P12 | Déploiement (Docker, CI) | Mise en ligne, même sans lancement marketing |

---

## 8. Études de cas : performance vs intégrité des données

Ces arbitrages sont tranchés ici pour servir de référence stable — que l'implémentation soit humaine ou assistée par IA, elle ne doit pas les redécider à chaque ticket. Chaque cas démarre dans sa version "simple", à faire évoluer consciemment si un besoin réel apparaît.

| Cas | Option performance | Option intégrité | Recommandation |
|---|---|---|---|
| Stockage de la géométrie (murs, positions d'éléments) | Colonne JSON unique, lecture/écriture rapide | Tables normalisées, contraintes FK/CHECK, requêtes riches (ex. "tous les projets avec plus de 5 portes") | Démarrer en JSON (P1), migrer vers un modèle normalisé quand un vrai besoin de requête apparaît, via une migration Alembic |
| Export PDF / calcul de scène 3D | Génération synchrone, simple | Celery + tâche de fond, réponse HTTP immédiate mais complexité opérationnelle (retries, idempotence) | Construire en synchrone (P6), migrer vers Celery (P9) en mesurant le gain avant/après |
| Édition concurrente (mode collaboratif) | Dernière écriture gagne (perte silencieuse possible) | Verrouillage optimiste (champ `version`, conflit détecté et affiché) | Verrouillage optimiste — bon terrain pour comprendre les compromis des systèmes distribués à petite échelle |
| Chargement des relations (scène 3D = projet + faces + éléments) | Requêtes naïves en boucle (N+1) | Eager loading SQLAlchemy (`selectinload`/`joinedload`) | Mesurer le N+1 en activant le logging SQL (`echo=True`) avant d'optimiser — sinon l'optimisation n'a pas de sens concret |
| Découpe des ouvertures dans les murs | `THREE.Shape` avec trou simple, rapide | CSG complet (`three-bvh-csg`), précis mais coûteux et expérimental | Simple d'abord (§3.2), CSG seulement pour les cas que la version simple ne couvre pas |
| Scène 3D calculée à chaque requête | Cache Redis, invalidé à la modification du plan | Recalcul systématique, toujours à jour mais coûteux | Bon terrain pour pratiquer l'invalidation de cache — un des rares vrais problèmes difficiles de l'informatique |

---

## 9. Sur l'idée de lancement réel

Ça reste un bonus, pas un objectif — mais concrètement, rien dans cette feuille de route ne t'empêche de déployer publiquement à n'importe quel stade (P12 est volontairement placé tôt dans la séquence). Si l'envie de le sortir revient en cours de route, aucune des décisions ci-dessus n'est à défaire pour ça.

---

## 10. Amendements

*Ce document est le contrat : « toute divergence passe par une modification explicite de ce document, jamais par une décision silencieuse en cours de code ». Les amendements sont donc écrits ici, datés et numérotés, plutôt que réécrits dans les sections d'origine — l'historique des décisions vaut autant que la décision.*

### A1 — Les permissions passent de la propriété à l'appartenance (vague 2, 2026-08-08)

**Amende §7 (P2)** — « Comptes, propriété des projets » — et **complète §5**.

Ce qui autorise l'accès à un projet n'est plus `Project.owner_id == user.id` mais une **appartenance acceptée** à l'organisation qui porte ce projet. Motif : le produit s'adresse à un artisan, donc à une entreprise avec des salariés, un comptable et un remplaçant pendant les congés — pas à un particulier (`strategie-produit.md` §6). Un modèle mono-propriétaire rendait le partage d'un chantier entre deux personnes de la même société littéralement impossible.

Trois tables s'ajoutent au §5 :

```
Organization                    (identité légale : SIRET, forme juridique, capital
 │                               en centimes entiers, RCS, adresse, n° de TVA,
 │                               assureur décennal + n° de police + couverture)
 ├─ Membership                  (user_id, organization_id, role, accepted_at)
 └─ Invitation                  (email, role, token_hash, expires_at, accepted_at)

Project
 └─ organization_id             (NOT NULL — c'est lui qui porte les droits)
```

Règles figées, non rediscutables sans un nouvel amendement :

- **Quatre rôles ordonnés** : `viewer` < `editor` < `admin` < `owner`. Lecture = `viewer`, écriture du plan = `editor`, suppression d'un projet et gouvernance = `admin`.
- **Une appartenance n'autorise que si `accepted_at` est renseigné.** Être invité n'est pas être membre.
- **Hors de l'organisation : 404, jamais 403.** Un 403 confirmerait l'existence de l'objet. À l'intérieur, un rôle insuffisant reçoit 403 : l'intéressé sait déjà que l'objet existe, et un 404 lui ferait croire à une suppression.
- **`Project.owner_id` subsiste comme trace de création et n'autorise plus rien.** Le comparer à l'utilisateur courant pour décider d'un accès est un défaut, pas un raccourci.

La contre-mesure est exécutable : `backend/tests/test_permissions_locataire.py` confronte la liste des routes exercées au schéma OpenAPI publié, dans les deux sens. Une route de locataire ajoutée sans test de cloisonnement fait échouer la suite ; une route de **liste** — qui ne porte aucun identifiant, et par laquelle une fuite est totale — doit être classée explicitement.

### A2 — Métré, barème, devis et facture Factur-X (vague 2, 2026-08-08)

**Complète §1** (« Génération de devis chiffré ») et **§7**, qui n'en décrivaient que l'intention.

- `app/geometry/quantities.py::build_takeoff(scene_graph)` — fonction **pure**, testée par les fixtures 07 à 10. Elle ne lit jamais `floor_area_cm2` (l'aire de la ligne médiane des murs, surévaluée de 6 à 20 %) mais `net_floor_area_cm2`. Une valeur non établissable sort à `None` avec un avertissement, **jamais à zéro**.
- Tables `price_book`, `price_item`, `face_costing`, `quote`, `quote_line`, `quote_counter`. **Tout montant est un entier de centimes**, toute quantité un `Numeric(12,3)` : aucun flottant sur le chemin de l'argent.
- `quote_line` **copie** libellé, prix et taux à l'émission et ne fait aucune jointure de lecture vers `price_item`. En France un devis signé est un contrat : modifier un tarif ne réécrit pas un document déjà émis.
- Numérotation séquentielle **sans trou, générée en base** et attribuée à l'émission seulement — un brouillon abandonné ne consomme aucun numéro.
- Factur-X (PDF/A-3 + XML CII, profil BASIC WL) produit entièrement en interne, sans dépendance ajoutée ni appel réseau. Le document porte, dans le PDF **et** dans le XML, la mention qu'on n'est pas une plateforme de dématérialisation agréée et qu'on ne transmet rien à l'administration.

### A3 — L'export PDF devient un dossier d'élévations cotées (vague 2, 2026-08-08)

**Amende §3.5**, qui ne prévoyait qu'« export PDF et image ».

`services/export_pdf.py` produit une page de garde, puis par pièce un plan coté et **une page A4 paysage par mur** : chaînes de cotes, allèges, échelle normalisée écrite sur chaque planche et cartouche. Les surfaces annoncées sont les surfaces **nettes**, calculées par la même fonction que le métré (`geometry.scene.net_floor_area`) — deux documents du même produit ne peuvent pas donner deux surfaces pour la même pièce.

### A4 — Un élément s'ancre à une face **ou** à une pièce (vague 3, 2026-08-08)

**Amende §5**, et répond à la question ouverte n° 3 ci-dessous, qui exigeait cet amendement avant tout code.

Jusqu'ici `Element.face_id` était obligatoire. Chaque élément était donc, par construction, **adossé à une face**. C'est juste pour ce que la spec avait en tête en §3.1 — une ouverture est un percement du mur, une applique et un radiateur sont accrochés — mais c'est faux pour la moitié du catalogue §4.3 : un lit, une table, un canapé, une chaise, un îlot de cuisine ne touchent aucun mur. Ils n'étaient pas seulement mal placés, ils étaient **impossibles** : le modèle obligeait à en coller un contre un mur choisi arbitrairement, et le décalage stocké devenait une coordonnée qui ne voulait rien dire dès que la pièce changeait de forme.

La limitation venait donc du modèle et non de l'interface : aucun travail sur l'éditeur 2D (glisser-déposer, aimantation) ne pouvait la lever. Elle est levée ici.

**Deux ancrages, exactement un par élément.**

```
Element
 ├─ face_id      (nullable)  — pose sur une face : décalages x_offset_cm / y_offset_cm
 └─ room_id      (nullable)  — pose au sol de la pièce : pos_x_cm / pos_y_cm
```

Règles figées, non rediscutables sans un nouvel amendement :

- **Exactement un des deux ancrages est renseigné**, et la contrainte est posée **en base** (`ck_element_exactly_one_anchor`) et non seulement dans Pydantic. C'est la leçon de la vague 1 : les `Field(...)` de SQLModel sont **inertes** sur les modèles `table=True`, et SQLAdmin, la CLI, Celery et `psql` écrivent sans passer par l'API. La contrainte porte aussi sur les coordonnées, pour qu'aucune ligne ne puisse porter les deux repères à la fois : `face_id` va avec `pos_x_cm` / `pos_y_cm` **nuls**, `room_id` avec ces deux colonnes **renseignées**.
- **Une ouverture reste ancrée à une face** (`ck_element_opening_needs_a_wall`) : un percement flottant au milieu d'une pièce n'a aucun sens en §3.1, et `services/faces.py` refuse déjà une ouverture sur un sol ou un plafond. La pose sur face n'est donc pas un cas hérité : c'est le cas **obligatoire** des ouvertures et le cas **naturel** de ce qui est accroché au mur. C'est la pose au sol qui s'ajoute, pas l'inverse.
- **`pos_x_cm` / `pos_y_cm` désignent le centre de l'emprise au sol, dans le repère du plan** — le même que `Room.polygon`, au centimètre. Le centre et non un coin : la rotation est libre autour de la verticale, et tourner autour d'un coin déplacerait le meuble au lieu de l'orienter. C'est aussi le repère que l'éditeur 2D manipule déjà, donc un glisser-déposer n'a aucune conversion à faire.
- **Le repère de la pièce n'est pas celui du sol-en-tant-que-face.** Un élément posé sur la face `SOL` garde la convention historique : ses décalages partent du coin de la boîte englobante du polygone. Les deux conventions coexistent parce qu'elles décrivent deux choses différentes ; la seconde est celle qui permet de vérifier l'appartenance au polygone, et c'est elle qu'un nouvel outil doit employer.
- **Un meuble libre doit tenir dans le polygone de la pièce**, emprise comprise : les quatre coins de son rectangle **après rotation** sont testés un par un (appartenance point-dans-polygone), pas seulement son centre. Un contrôle sur le seul centre laisse une table de 2 m traverser un mur. Le refus dit où et de combien ça déborde.
- **Le changement d'ancrage n'est pas une modification** : passer une applique du mur au sol change de repère, donc de sens des coordonnées. `PATCH /api/elements/{id}` refuse `face_id` et `room_id` ; le client supprime puis recrée. Reste une question ouverte (n° 5) si le geste devient courant.

### A5 — Fond de plan calibré sur la pièce (vague 3, 2026-08-08)

**Complète §5** et **§1** (« Éditeur 2D sans limite artificielle »).

Un artisan de second œuvre n'arrive jamais devant un canevas vide : il arrive avec le plan de l'architecte, un relevé de géomètre ou une photo du plan affiché dans la cage d'escalier. Sans moyen de poser cette image sous le dessin, il ressaisit le logement au mètre, mur par mur, avant d'avoir produit la moindre valeur. C'est le premier frein à l'adoption mesuré par `strategie-produit.md`, et il est structurel : aucun raccourci d'interface ne le compense.

`Room` reçoit donc les colonnes du calage :

```
Room
 ├─ background_url                 (nullable — l'image elle-même vit ailleurs)
 ├─ background_scale_cm_per_px     (nullable tant que le calibrage n'a pas eu lieu)
 ├─ background_offset_x_cm         (translation, repère du plan)
 ├─ background_offset_y_cm
 ├─ background_rotation_deg        (plan photographié de travers)
 └─ background_opacity             (0 à 1 — le fond doit s'effacer derrière le trait)
```

- **L'échelle est nullable et le reste tant que le calibrage n'a pas eu lieu.** Une valeur par défaut inventée (« 1 cm par pixel ») serait indiscernable d'un calibrage réel, et l'utilisateur dessinerait un logement faux sans être averti. `NULL` veut dire « image posée, pas encore calibrée ».
- **Le stockage du fichier et l'outil de calibrage à deux clics ne relèvent pas de cet amendement** : ce sont deux lots distincts. Ce qui est figé ici, c'est le contrat de données et sa validation.
- **`background_url` est validée côté serveur** : chemin relatif commençant par un seul `/`, ou URL `https://`. Ni `javascript:`, ni `data:`, ni `//hôte` protocol-relative. Le champ est écrit par un client et relu dans un attribut d'image : c'est une entrée utilisateur au sens OWASP A03, et la valider à l'écriture est le seul endroit où elle ne dépend pas de la vigilance du rendu.

### A6 — Écriture en lot du plan (vague 3, 2026-08-08)

**Amende §8, cas 3** (« édition concurrente »), dont le verrouillage optimiste avait une conséquence non anticipée.

Chaque écriture du plan passe par `_claim_project`, qui incrémente `Project.version`. C'est ce qui rend la détection de conflit possible — et c'est aussi ce qui **sérialise** toute modification multiple : déplacer quinze meubles impose quinze allers-retours strictement séquentiels, puisque chacun invalide la version que le client détient. Un glisser-déposer, qui produit naturellement des rafales de modifications, est inutilisable dans ces conditions.

`POST /api/projects/{project_id}/batch` prend **une** version et une liste d'opérations typées (création, modification, suppression d'éléments et de pièces), appliquées dans **une seule transaction** :

- un seul `_claim_project` en tête, un seul `_commit_or_conflict` en fin, donc **une seule** incrémentation de version pour tout le lot ;
- **tout ou rien** : une opération refusée annule le lot entier et nomme son rang. Un lot partiellement appliqué laisserait le client dans un état qu'il ne peut pas reconstituer ;
- **le nombre d'opérations est borné** (100) : sans borne, une seule requête tient une transaction ouverte arbitrairement longtemps ;
- **chaque opération désigne un objet du projet nommé dans l'URL**, revérifié un par un. Un identifiant d'élément appartenant à un autre projet est traité comme inexistant (404) : l'appartenance vérifiée une fois sur le projet ne dit rien des identifiants portés par le corps de la requête ;
- le résultat de chaque opération est renvoyé **dans l'ordre d'envoi**, avec la nouvelle version du projet.

Les routes unitaires existantes ne sont ni retirées ni dépréciées : un lot est une optimisation du chemin d'écriture, pas un remplacement du modèle CRUD.

### A7 — Le mobilier est compté, et le plan coté le montre (vague 4, 2026-08-08)

**Amende §3.5 et A3**, et **répond à la question ouverte n° 6** ci-dessous, qui exigeait cet amendement avant tout code.

Depuis A4, un lit, une table ou un îlot s'ancrent à la pièce et non à une face. Or les deux livrables du plan parcourent les éléments **par face** : `services/export_pdf.py` lit `face["elements"]`, et `geometry/quantities.py` ne retenait que les nœuds porteurs d'un revêtement. Ce qui n'est adossé à rien n'apparaissait donc **nulle part** — ni sur le plan coté, ni dans le récapitulatif de pièce, ni dans le métré. L'éditeur de la vague 3 sait en poser en masse : l'écart est passé de théorique à visible sur le premier chantier venu.

Deux corrections distinctes, et elles n'ont pas la même nature.

**Le plan coté de la pièce dessine le mobilier posé au sol** (correction). Chaque meuble libre y figure à sa position et à sa rotation, emprise au sol réelle, coins calculés comme le fait déjà `services/faces.py::free_element_footprint` — la même convention que le scene graph, sans quoi un meuble tourné à 90° serait dessiné avec sa largeur et sa profondeur échangées. Le récapitulatif de pièce et la page de garde cessent de le sous-compter. Il reste **absent des planches d'élévation**, et c'est voulu : une élévation est la vue d'un mur, et un îlot de cuisine n'est sur aucun mur.

**Le métré compte le mobilier à l'unité** (ajout de périmètre §4, autorisé ici). `build_takeoff` gagne, par pièce et pour le projet, une liste `furniture` regroupée par recette et par gabarit : `furniture_type_slug`, dimensions, emprise au sol unitaire, et trois décomptes — total, posés au sol, adossés à une face. Règles figées :

- **Le mobilier se compte, il ne se chiffre pas.** Aucun montant n'apparaît dans cette liste : un `FurnitureType` est une recette de composition (§4.1), il n'a pas de prix, et le barème de A2 ne connaît que des ouvrages au m², au ml et à l'unité de pose. Brancher une fourniture sur le chiffrage demanderait un tarif par recette, donc un nouvel amendement.
- **Le métré ne compte que ce que la scène porte.** Un élément dont la recette manque au catalogue ne produit aucun nœud (`geometry/scene.py::_furniture_node` rend `None`) : il n'a déjà ni forme 3D ni élévation, et il n'est pas davantage compté. Ce n'est pas un silence du métré, c'est un catalogue incomplet.
- **La clé `furniture` n'est présente que si la pièce en porte.** Son absence vaut zéro et jamais « inconnu » — la présence de mobilier est toujours établissable depuis la scène, contrairement à une surface nette manquante. C'est aussi ce qui laisse intact le contrat décrit par les fixtures de référence 07 à 10, qui font foi (`CLAUDE.md`) et décrivent exhaustivement la forme du métré des vagues précédentes.

### A8 — `LayingPattern` accepte la pose en diagonale (vague 4, 2026-08-08)

**Amende §1** (« motifs de pose avancés »), et **répond à la question ouverte n° 1** ci-dessous.

`WASTE_RATIO_BY_PATTERN` provisionne 12 % de chute pour une pose en diagonale depuis la vague 2, mais `Covering.pattern` est typé sur `LayingPattern`, qui n'avait que `straight`, `staggered`, `chevron` et `herringbone` : **aucune saisie ne pouvait produire ce motif**, et la provision était morte. La valeur `diagonal` est ajoutée à l'énumération.

La question ouverte annonçait « plus une migration d'énumération ». Vérification faite : **il n'y en a pas besoin**, et c'est une correction de la question elle-même. `LayingPattern` n'est le type d'aucune colonne — le motif de pose vit dans le blob `Face.covering`, colonne JSON assumée par §8 (cas 1), et l'énumération n'existe qu'à la frontière de l'API (`schemas/plan.py::Covering`). Aucun type SQL `layingpattern` n'a jamais été créé. Une migration ici n'aurait rien eu à migrer.

Conséquence à ne pas oublier : la valeur nouvelle **change le schéma OpenAPI publié**, donc `frontend/src/api/openapi-snapshot.json` doit être régénéré (une fois, à l'assemblage — un fichier généré ne se fusionne pas).

La pose en diagonale reste hors des `ALIGNED_PATTERNS` du métré : sa trame n'est pas parallèle aux bords de la face, les unités entières et les coupes n'y sont donc pas dénombrées. La quantité à commander, elle, l'est. Inventer un décompte serait pire que ne rien annoncer.

### A9 — Un compte se reprend, s'exporte et se ferme (vague 5, 2026-08-08)

**Amende §7 (P2)** — « Comptes, propriété des projets » — et **complète §5**.

La phase P2 s'arrêtait à l'ouverture d'un compte. Un mot de passe oublié signifiait le compte et tous les chantiers perdus définitivement, et le seul droit RGPD couvert l'était par accident, par les cascades `ON DELETE`. On ne vend pas un abonnement dans cet état.

Une table s'ajoute au §5 :

```
UserToken   (user_id, purpose, token_hash UNIQUE, expires_at, consumed_at)
```

Règles figées :

- **Seul le hachage est stocké**, et la ligne consommée est **conservée** pour interdire le rejeu — même règle qu'`Invitation` (A1), et pour la même raison : une ligne effacée est une ligne qu'on ne peut plus opposer.
- **`User.token_version` cesse d'être décoratif.** Il est recopié dans chaque JWT (revendication `ver`) et confronté au compte à chaque requête authentifiée. Toute route qui pose un nouveau mot de passe **doit** l'incrémenter ; sans quoi « fermer toutes les sessions » est un bouton qui ne fait rien. Un jeton antérieur, sans revendication, retombe sur 0 — la valeur par défaut de la colonne.
- **La demande de réinitialisation répond 202 avec un corps constant**, que l'adresse existe ou non, comme l'inscription. Deux portes d'énumération valent zéro protection.
- **La fermeture d'un compte est refusée en 409** tant qu'il est le dernier propriétaire accepté d'une organisation habitée. Le droit à l'effacement de l'un ne détruit pas les données des autres, et l'obligation comptable de dix ans sur les documents **émis** prime également. Le message nomme les organisations à transmettre.
- **L'export de portabilité a exactement le périmètre des routes de l'API** (`accessible_organization_ids`) et ne contient jamais de secret. Un export plus large est une fuite entre locataires déguisée en conformité.

Ce que cet amendement **ne** couvre **pas**, et qui reste à faire : aucun transport de courriel n'est branché — la réinitialisation est inutilisable en ligne (voir question ouverte n° 11) — et aucune purge automatique n'applique les durées de conservation annoncées (question ouverte n° 12).

### A10 — Le projet de démonstration est un objet de produit (vague 5, 2026-08-08)

**Complète §1.**

Une organisation vierge peut demander **une fois**, par `POST /api/auth/demo-project`, un chantier de démonstration figé dans `app/services/demo.py` : une salle de bain de 240 × 200 cm entièrement chiffrable, dont un devis sort sans aucune saisie de barème.

Deux règles figées :

- **Il est demandé, jamais semé à l'inscription.** Il est refusé (409) dès qu'un chantier existe, ce qui garantit qu'un artisan qui le supprime ne le revoit jamais. Un semis à l'inscription le recréerait à chaque nouveau compte, sans possibilité de refus, et ferait construire une salle de bain complète à chacun des tests qui ouvrent un compte.
- **Sa géométrie est une constante du code, pas un artefact de test.** Elle est vérifiée par les mêmes fonctions d'encombrement que l'API et, au même titre que les fixtures de `backend/tests/geometry/fixtures/`, elle ne s'ajuste pas pour faire passer un test.

### A11 — Offres, quotas et compteurs d'usage (vague 5, 2026-08-08)

**Complète §5** et **§7**, et pose la frontière technique décrite par `strategie-produit.md` §4. Aucun prestataire de paiement n'est intégré : on pose le modèle et la frontière, pas l'encaissement.

Quatre tables s'ajoutent au §5 :

```
PlanCatalog    (code PK, name, tagline, monthly_price_cents, yearly_price_cents,
 │              seat_price_cents, currency, limits JSONB, features JSONB,
 │              is_public, sort_order)
Subscription   (organization_id, plan_code FK, status, current_period_start/end,
 │              trial_ends_at, cancel_at, seats,
 │              external_customer_id, external_subscription_id)
UsageCounter   (organization_id, metric, period_start, value)  UNIQUE(les trois premiers)
UsageEvent     (append-only ; idempotency_key UNIQUE NOT NULL, metadata JSONB,
                occurred_at, user_id ON DELETE SET NULL)
Project
 └─ archived_at                 (déclassement — voir plus bas)
```

Règles figées :

- **Les codes de palier et les clés de `limits` / `features` sont des chaînes libres, jamais des énumérations.** Ajouter un palier négocié doit rester un `INSERT`, et déplacer une fonctionnalité d'un palier à l'autre un `UPDATE` — sinon chaque négociation commerciale redevient un déploiement. Le palier requis annoncé dans un refus est **calculé depuis la base**, pas depuis une table de correspondance codée.
- **Une organisation sans ligne d'abonnement est au palier Découverte**, implicitement. Un compte neuf n'a aucune ligne, et c'est l'état normal. C'est aussi ce qui permet à l'essai Pro de 14 jours sans carte de s'ouvrir **au premier geste monétisé** et jamais à l'inscription : la garde ouvre l'essai, réévalue, et le geste aboutit.
- **Une limite inconnue vaut « illimité », jamais zéro.** Le sens de défaillance est choisi : le pire incident imaginable est un quota qui bloque un client payant en pleine journée de chantier, pas un PDF de trop.
- **Le compteur s'incrémente en une seule instruction** (`INSERT … ON CONFLICT DO UPDATE SET value = value + :n RETURNING value`). Un `SELECT` puis `UPDATE` laisse deux onglets passer au-dessus de la même limite.
- **Un événement rejoué ne compte pas deux fois** : `usage_event.idempotency_key` est unique, et pour un export c'est l'identifiant de la tâche Celery. Une panne du courtier ne doit pas se traduire en surfacturation.
- **La période de comptage est celle de la facturation, pas le mois calendaire.** Un abonnement souscrit le 20 se remet à zéro le 20 ; l'ancre est le début du premier abonnement ou, à défaut, la création de l'organisation.
- **On bloque la création, jamais la lecture.** Au-delà du plafond de chantiers actifs, les projets excédentaires reçoivent `archived_at` — **rien n'est supprimé**, les plus récemment modifiés sont conservés, et le chantier archivé reste lisible, exportable et partageable. Seule l'écriture est refusée, en 403 `{code: 'project_archived'}`, au point de passage unique de toute écriture du plan (route de lot comprise).
- **Le filigrane est une décision du serveur**, déduite du palier, et aucun paramètre de requête n'a de prise dessus. Le PDF filigrané **se télécharge quand même** : bloquer le téléchargement ferait douter du résultat, le livrer filigrané le prouve.
- **Le métré reste entièrement ouvert sans abonnement.** Les trois murs posés sont le devis, l'export sans filigrane et le deuxième chantier — pas la mesure.

Trois colonnes vont au-delà de la liste de `strategie-produit.md` §4 et sont assumées : `tagline` (la colonne « Pour qui » de la grille) et `seat_price_cents` (le « + 19 €/siège »), sans lesquelles la page tarifs aurait dû coder ces valeurs en dur — c'est-à-dire exactement ce que ce modèle existe pour éviter. `yearly_price_cents` est le **prix mensuel équivalent en engagement annuel** (2400 pour Artisan), et non le montant annuel : c'est la lecture qui rend « 29 € (24 €) » cohérent avec « deux mois offerts ». Il est `NULL` pour le palier Réseau, dont §4 dit « sur devis » — inventer un tarif annuel afficherait un prix que personne n'a négocié.

### A12 — Le moteur d'intelligence du plan (vague 5, 2026-08-08)

**Complète §1** (« contrôle de conformité », « calepinage ») et applique `strategie-produit.md` §3.8. **N'ajoute aucune table, aucune colonne, aucune migration.**

Trois moteurs — contrôle de conformité, calepinage optimisé, aménagement sous contraintes — servis par `GET /api/projects/{id}/inspection`, `GET /api/projects/{id}/laying-plan` et `POST /api/rooms/{id}/layouts`.

Règles figées :

- **L'intelligence est algorithmique et locale.** Aucun LLM, aucun appel réseau sortant, aucune clé, aucun aléa : deux appels sur la même pièce rendent le même octet. C'est ce qui la rend testable par fixtures, et c'est une contrainte de produit, pas une préférence d'implémentation.
- **L'entrée est le scene graph, pas les modèles SQLModel** — même choix que le métré, et pour les mêmes trois raisons : fonction pure alimentable par fixtures sans base, une seule géométrie mise en cache pour le viewer, le devis, le dossier et le contrôle, et `furniture_type_slug` n'existe que là. Conséquence à connaître : un meuble dont la recette manque au catalogue ne produit aucun nœud, donc échappe au contrôle — c'est le comportement de `build_scene_graph`, pas une décision de ce lot.
- **Les seuils sont une classe d'exigences, pas un avis juridique.** Ils sont relevés dans la réglementation et l'usage courant du bâtiment français (décret n° 2002-120 sur la décence, arrêté du 24 décembre 2015 sur l'accessibilité, NF P01-012 sur les protections contre les chutes, largeurs de bloc-porte du commerce), chaque source est écrite à côté de son champ dans `ergonomy.Thresholds`, et **le rapport republie les seuils appliqués**. L'avertissement de `strategie-produit.md` §2 s'applique mot pour mot : cette liste doit être validée par un homme de métier avant d'être présentée comme une norme, et le produit n'affiche jamais « non conforme » là où il peut afficher « sous le seuil de X cm ».
- **Aucun seuil n'entre par le corps d'une requête.** Seul le mode « logement accessible » est pilotable par le client. Les ouvrir transformerait un contrôle métier en paramètre d'affichage : il suffirait de demander 10 cm de passage pour rendre conforme un plan invivable. Un réglage par organisation est une ligne SQL, pas un paramètre de requête.
- **Une proposition d'aménagement n'écrit rien.** Le moteur rend deux ou trois implantations valides et classées ; le client en choisit une et crée lui-même les éléments. Un moteur qui poserait d'autorité quinze meubles dans le plan d'un artisan est un moteur qu'on désactive au premier essai.
- **Le calepinage ne réécrit pas le métré.** `cuts_saved` mesure l'écart avec la pose de référence ; la quantité à commander reste établie sur la surface et le taux de chute (A2). Un test croisé exige que le calepinage par défaut retombe exactement sur le décompte du métré.
- **`ai_runs` se compte par version de plan et non par clic** (A11) : les trois moteurs étant déterministes, deux appels sur la même version sont une seule analyse.

### A13 — La fermeture d'un compte ne détruit plus rien d'autre que lui (vague 6, 2026-08-08)

**Amende A9** — dont la règle était juste et l'implémentation fausse — et **complète §5**.

A9 posait que « le droit à l'effacement de l'un ne détruit pas les données des autres, et l'obligation comptable de dix ans sur les documents **émis** prime également ». Le code faisait l'inverse sur les deux points, en répondant 204. Une revue de fin de chantier l'a établi par deux sondes, pas par lecture :

- un second propriétaire ferme son compte : ses trois chantiers, que sa collègue éditait le matin même, disparaissent. Le garde-fou ne regardait que le **rôle** du partant, alors que ce qui détruisait était le `ON DELETE CASCADE` de `project.owner_id` — lequel frappe aussi bien un simple `editor`, cas que le garde-fou ne examinait même pas ;
- un artisan ferme son compte après avoir émis `DEV-2026-0001` et facturé `FAC-2026-0001` : la facture n'est plus en base. L'organisation dont il était seul membre était supprimée, et `quote.organization_id` est en `ON DELETE CASCADE`.

Règles figées, non rediscutables sans un nouvel amendement :

- **`project.owner_id` est nullable et en `ON DELETE SET NULL`.** A1 dit depuis la vague 2 que la colonne « subsiste comme trace de création et n'autorise plus rien » ; elle détruisait pourtant encore tout. Une trace ne détruit rien. « Chantier créé par un compte depuis fermé » devient un état normal, et le chantier reste à l'**organisation**, qui est ce qui porte les droits. C'est la seule colonne du modèle qui pointait vers `user` avec ce défaut : `membership.user_id` et `usertoken.user_id` sont en cascade parce qu'ils ne décrivent que le partant, et `usage_event.user_id` était déjà en `SET NULL`.
- **Un document émis survit à son émetteur.** Une organisation à effacer qui porte un devis émis ou une facture n'est **pas** supprimée : dix ans (art. L. 123-22 du code de commerce), et c'est l'artisan qui serait redressé, pas l'éditeur. Un brouillon, lui, n'a ni numéro ni date d'émission : il part avec le compte.
- **Le compte est alors pseudonymisé au lieu d'être supprimé**, et c'est l'arbitrage RGPD de cet amendement. Refuser la fermeture en 409 tant qu'une facture existe — l'autre option étudiée — rendrait l'article 17 littéralement inatteignable : aucune route ne supprime un document émis, par construction. La pseudonymisation, elle, tient les deux bouts : l'adresse e-mail — seule donnée personnelle que porte `user` — est remplacée par une adresse en `.invalid` (RFC 2606), le mot de passe est rendu illisible, les sessions sont fermées, les jetons effacés, et le compte quitte toutes ses organisations sauf celles qui portent un document émis. La pièce comptable survit, la personne non. Le résultat visible est identique dans les deux cas : 204, plus aucun accès, plus aucune donnée personnelle.
- **Il reste un seul refus, en 409, et il est de gouvernance** : le dernier propriétaire accepté d'une organisation habitée ne peut pas partir, faute de quoi plus personne ne peut y inviter, payer ni fermer l'entreprise. Ce refus ne protège plus aucune donnée — la cascade qui menaçait a disparu — et son message ne le prétend plus.
- **Un lien de partage a toujours une échéance.** `expires_in_days` étant facultatif, le chemin par défaut — celui que le frontend emprunte — fabriquait un lien **permanent** sur la géométrie d'un logement, alors que `docs/rgpd.md` annonce « jusqu'à révocation ou échéance ». La durée par défaut est celle que le palier déclare (`share_link_days`), et une durée demandée y est rabotée. Une limite absente du catalogue ne vaut « illimité » que pour le **plafond** — c'est le sens de défaillance de A11, et il est bon pour un quota ; la **valeur par défaut**, elle, retombe sur trente jours, parce qu'une durée de conservation absente ne vaut pas « éternel », elle vaut « pas de politique ».

Ce que cet amendement **ne** couvre **pas** : la page publique `/legal/confidentialite` ne mentionne pas encore la pseudonymisation, alors que `docs/rgpd.md` impose que les deux disent la même chose. Le corps de `DELETE /api/auth/me` reste vide (204), donc rien n'annonce à l'intéressé, au moment où il ferme son compte, laquelle des deux issues s'est appliquée — ce qui relève de l'information de l'article 12 et demande un changement de contrat de la route.

### A14 — La grille tarifaire ne vend que ce qui existe, et le savoir métier se règle (vague 6, 2026-08-08)

**Amende A11** (offres et quotas) et **A12** (moteur d'intelligence), **complète §5**, et met `docs/strategie-produit.md` §4 en cohérence avec le produit.

A11 posait la frontière technique entre le gratuit et le payant, et le lot l'avait construite : quatre tables, un incrément atomique, un déclassement qui ne détruit rien. Ce qu'il n'avait pas fait, c'est **brancher** la grille. Une revue de fin de chantier l'a établi par sondes, compte au palier Découverte, toutes fonctionnalités à `false` :

| Fonctionnalité annoncée bloquée | Route | Réponse observée |
|---|---|---|
| `compliance_check` | `GET /projects/{id}/inspection` | 200 |
| `tiling_waste` | `GET /projects/{id}/laying-plan` | 200 |
| `auto_layout` | `POST /rooms/{id}/layouts` | 200 |
| `dimensioned_elevations` | exports PDF | 200, dossier complet |
| `multi_seat` | `POST /organizations/{id}/invitations` | 201 |
| `rooms_per_project` (2) | 3ᵉ, 4ᵉ, 5ᵉ pièce | 201, 201, 201 |

Seuls `quotes` et `exports_without_watermark` étaient réellement appliqués. Pire : `white_label`, `client_signature`, `priced_variants`, `sso`, `agency_stats` et `api` n'avaient d'implémentation **nulle part**, et la page `/tarifs` — qui est publique et s'affiche avant l'inscription — les portait en « ✓ » en face de 79 €.

Règles figées, non rediscutables sans un nouvel amendement :

- **Une clé de `features` n'existe que si une garde la refuse.** Les six fonctionnalités jamais construites sont retirées de `plan_catalog`, de `FEATURE_LABELS` et donc de la page tarifs. Elles ne sont pas marquées « à venir » : sur une page de vente, « à venir » se lit comme un argument, et aucune n'a de date. Le jour où l'une est construite, la clé **et** son point de passage entrent dans le même lot. `app/services/seed_plans.py::ENFORCEMENT_POINTS` nomme la garde de chaque clé, et un test confronte les deux tables — il est impossible d'ajouter une ligne à la grille sans nommer où elle est appliquée.
- **Une limite ne s'affiche que si une garde l'applique**, même règle et même table (`LIMIT_ENFORCEMENT_POINTS`). `exports_pdf`, `quotes_issued`, `ai_runs` et `api_calls` quittent la page : elles valent `null` sur les quatre paliers, si bien qu'elle répétait « Illimité » quatre fois sans que rien ne puisse jamais le démentir. Les clés restent dans `plan_catalog.limits`, où le plafond s'écrira le jour où la garde existera.
- **`dimensioned_elevations` s'applique sur le contenu du dossier, jamais sur son téléchargement.** §4 place l'« export PDF filigrané » dans ce que le palier gratuit *inclut* et les « élévations cotées » dans ce qu'il *bloque* : ce sont deux lignes distinctes, et le produit n'en appliquait qu'une. Un palier sans la fonctionnalité reçoit la page de garde, le plan coté de chaque pièce et les récapitulatifs, filigranés ; il ne reçoit pas les planches d'élévation, et la pagination annoncée par la page de garde suit. La règle de A11 est intacte et le reste : **le fichier se télécharge quand même**, et aucun paramètre de requête n'a de prise sur l'une ou l'autre décision.
- **`rooms_per_project` se compte sur le chantier, pas sur l'organisation, et c'est un état.** Le plafond annoncé est « 2 pièces par chantier » : compter des créations laisserait une pièce supprimée puis redessinée consommer deux fois le quota. Les deux points de création — la route unitaire et la route de lot (A6) — passent par la même garde ; le lot est plafonné sur son **solde** (`créations − suppressions`), parce qu'il est tout-ou-rien et qu'un lot qui remplace une pièce par une autre ne consomme rien.
- **`multi_seat` et `seats` sont deux refus distincts**, sur l'invitation, qui est le seul endroit d'où un second siège naît : 402 quand le palier n'ouvre pas le multi-utilisateur, 429 quand il l'ouvre et que les places sont prises. Les confondre proposerait un changement de palier à une entreprise qui n'a besoin que d'un siège. Les sièges se comptent sur les appartenances **acceptées** : une invitation en attente n'ouvre aucun accès, et la faire compter laisserait une invitation oubliée bloquer une embauche. `shared_price_book` n'a pas de garde propre et n'en aura pas : le barème appartient à l'organisation, il est donc partagé par construction dès qu'un second siège existe.
- **`POST /organizations/{id}/subscription/trial` exige `admin`.** Ce qu'il consomme est l'essai unique et non renouvelable de l'entreprise ; le laisser à `editor` — le rôle qu'on obtient en rejoignant une organisation — permettait à n'importe quel salarié de le griller d'un clic. L'ouverture **automatique** de l'essai n'est pas concernée : elle n'est pas une route, elle est décidée par la garde au moment où le geste monétisé aboutit, et n'exige aucun rôle au-delà de celui du geste.
- **Le taux de chute est réglable par face.** `Covering.waste_ratio_bp` (en points de base, `800` = 8 %) l'emporte sur `WASTE_RATIO_BY_PATTERN`, qui devient un **repli**. C'est le chiffre qui alimente la surface à commander, donc la quantité facturée, et un carreleur qui pose du grand format sait que 8 % est faux pour lui. Il vit sur le revêtement et non sur `price_item` : la chute est une propriété **physique** de la pose — coupes de rive, casse, rattrapage de trame — au même titre que le motif et les dimensions d'unité, déjà là. Ce n'est pas du chiffrage, et c'est ce qui la distingue de `face_costing` (A2). Aucune migration : le motif vit dans le blob JSON `Face.covering`, comme la pose en diagonale de A8. Une valeur illisible ou hors bornes retombe sur le motif **et l'écrit dans `warnings`** ; le regroupement des revêtements prend le taux dans sa clé, faute de quoi une ligne de commande afficherait un taux qui n'explique pas sa quantité.
- **Les défauts commerciaux appartiennent à l'entreprise.** `organization` reçoit `default_payment_days`, `default_validity_days`, `default_late_penalty_rate_bp`, `default_recovery_indemnity_cents`, `default_payment_terms`, `default_mediator_name` et `default_mediator_url`. `docs/strategie-produit.md` §2 le demandait en toutes lettres — « le produit doit rendre ces champs paramétrables plutôt que codés en dur, c'est la seule manière de suivre une réglementation qui bouge » — et ils l'étaient **par devis** et jamais par entreprise. L'ordre est : saisie du devis > défaut de l'organisation > constante réglementaire ; `NULL` **et rien d'autre** fait descendre d'un niveau, parce que zéro est une valeur et écrirait « paiement à 0 jour ». Un document déjà émis n'est pas touché : il porte ses propres copies (A2), et c'est la règle qui prime.
- **La durée de l'essai est une colonne du catalogue** (`plan_catalog.trial_days`), plus une constante. C'est le levier commercial le plus souvent tiré, et tout le reste du palier se réglait déjà par `UPDATE`. Zéro veut dire « aucun essai offert » sur ce palier, et c'est le défaut : seul le palier d'essai en porte un. La page tarifs annonce la valeur réellement appliquée par `start_trial`.
- **Les seuils de conformité se surchargent par organisation** (`organization.inspection_thresholds`, JSONB). A12 refuse tout seuil venu du corps d'une requête — « il suffirait de demander 10 cm de passage pour rendre conforme un plan invivable » — en s'accordant une porte de sortie : « un réglage par organisation est une ligne SQL ». Il n'existait aucune colonne où l'écrire, et l'API construisait toujours `Thresholds` avec ses valeurs par défaut : la porte était fictive. Elle est réelle, et la règle devient tenable telle quelle. **On ne peut régler que ce que le rapport republie** : les clés surchargeables sont exactement celles de `Thresholds.to_dict()`, moins `accessible` qui est un mode demandé requête par requête. Une clé inconnue, non numérique ou nulle est ignorée et jamais fatale — ces valeurs arrivent par `psql`, et faire échouer chaque inspection sur une faute de frappe transformerait un réglage raté en panne ; l'opérateur le voit immédiatement puisque le rapport republie les seuils **appliqués**.

Ce que cet amendement **ne** couvre **pas**, et qu'il faut lire comme tel : `priced_variants` (§3.5, « le levier de panier moyen ») est retirée de la grille et **reste à construire** — duplication de projet, champ auto-référent, chiffrage de chaque variante ; c'est un lot, pas une ligne. Le palier Réseau n'a plus de fonctionnalité qui le distingue d'Entreprise autre que les sièges illimités, et son tagline le dit désormais : sous-domaine, SSO, catalogue imposé au réseau et statistiques par agence ne sont pas construits. Enfin, `exports_pdf`, `quotes_issued` et `ai_runs` restent sans garde : leurs plafonds sont `null` partout, donc invisibles aujourd'hui, mais un `UPDATE` qui en poserait un ne serait respecté par personne.

### Questions ouvertes — à trancher par le propriétaire, pas en cours de ticket

1. ~~**`LayingPattern` n'a pas de valeur `diagonal`**~~ — **tranchée par A8** (vague 4, 2026-08-08) : la valeur est ajoutée, et la migration que la question annonçait n'avait pas lieu d'être. Conservée ici parce que l'historique des décisions vaut autant que la décision.
2. **Le choix de l'organisation à la création d'un projet** n'existe pas : la règle appliquée est déterministe (l'appartenance acceptée la plus ancienne), mais un compte membre de deux entreprises ne peut pas désigner la cible.
3. ~~**`Element.face_id` reste obligatoire**~~ — **tranchée par A4** (vague 3). Conservée ici parce que l'historique des décisions vaut autant que la décision.
4. **Aucune vue de back-office** pour les organisations, appartenances et documents commerciaux. C'est délibéré côté facturation : un `quote_counter` ou une `quote_line` modifiables à la main annulent les deux garanties légales de A2 (numérotation sans trou, ligne figée). Si ces vues sont ajoutées, elles doivent l'être en lecture seule.
5. **Le changement d'ancrage d'un élément** (décrocher une applique du mur pour la poser au sol, ou l'inverse) n'existe pas : A4 impose de supprimer puis recréer. C'est délibéré — les deux repères n'ont pas la même signification — mais si le geste devient courant dans l'éditeur, il faudra une opération dédiée qui exige un placement complet dans le nouveau repère, jamais un simple `PATCH` de `face_id`.
6. ~~**Un meuble libre n'apparaît pas dans le dossier d'élévations**~~ — **tranchée par A7** (vague 4, 2026-08-08) : le plan coté le dessine, le récapitulatif et la page de garde le comptent, et le métré l'itémise à l'unité. Il reste volontairement absent des planches d'élévation. Conservée ici parce que l'historique des décisions vaut autant que la décision.
7. **Un brouillon de devis ne suit pas le plan qu'il chiffre.** `quote.warnings`, les lignes et les totaux sont figés à la création. Pour un devis **émis** c'est la règle de A2, et elle n'est pas discutable : un document dit ce qu'on savait à l'instant où il a été établi. Pour un **brouillon**, c'est un défaut — mais rafraîchir les seuls `warnings` le rendrait incohérent, puisque les quantités des lignes, elles, resteraient celles de l'ancien plan : l'artisan lirait « aucun avertissement » sur un devis dont le métré a changé. Deux obstacles concrets à traiter ensemble, jamais séparément : (a) `quote` ne mémorise **ni** le `price_book_id` employé **ni** la `Project.version` chiffrée, si bien qu'un recalcul devrait deviner le barème et ne saurait pas dire *si* le plan a bougé ; (b) régénérer les lignes d'un brouillon écraserait les `extra_lines` saisies à la main. La forme retenue le jour où ce sera traité : deux colonnes (`price_book_id`, `plan_version`), un indicateur de péremption calculé à la lecture (`plan_version != project.version`), et une action explicite de régénération — jamais un rafraîchissement silencieux.
8. **Aucun prix ne s'attache à une recette de mobilier.** A7 fait compter les meubles par le métré ; les porter dans un devis demande un tarif par `FurnitureType` (ou un code de barème par recette), donc un nouvel amendement de A2. En l'état, le mobilier est une information de dossier, pas une ligne d'argent.
9. **Le modèle ne stocke ni la main d'une porte ni son sens de battement** (§5). Le contrôle de conformité (A12) suppose donc une ouverture **vers l'intérieur** de la pièce qui porte le percement, et essaie les **deux** ferrages : une porte n'est en défaut que si aucun des deux ne passe ; si un seul est libre, c'est un conseil (« le plan impose la main de la porte, il faut la noter sur la commande »). C'est le choix le moins faux à modèle constant, mais il est structurant : le jour où `Element` reçoit un champ de ferrage, c'est un amendement de §5 **plus** une migration, et les règles `porte.*` changent de nature — elles deviennent des vérifications au lieu d'inférences. Corollaire du même manque : les pièces sont inspectées **indépendamment**, le scene graph ne portant aucune adjacence entre pièces ; un passage qui traverse la porte entre deux pièces n'est pas mesuré.
10. **Aucun encaissement, donc aucune route de changement de palier** (A11). Ouvrir une telle route sans paiement laisserait n'importe quel administrateur s'attribuer le palier Entreprise gratuitement. Un changement de palier est aujourd'hui un `UPDATE subscription`. Corollaire : le déclassement n'est réconcilié qu'à la consultation de l'abonnement, le dépôt n'ayant pas d'ordonnanceur — un chantier excédentaire reste modifiable tant que personne n'ouvre la page compte. `enforce_active_project_limit` est déjà une fonction pure sur session, prête à être branchée sur un `beat` Celery. ~~Enfin, `rooms_per_project`, `share_link_days` et `max_seats` sont **déclarés dans le catalogue et affichés**, mais aucune garde ne les applique~~ — **traité par A13 et A14** (vague 6) : les deux points de création de pièce, la durée des liens de partage et l'invitation de membres ont chacun la leur, et une limite ne s'affiche désormais sur la page tarifs que si une garde l'applique. Restent sans garde `exports_pdf`, `quotes_issued`, `ai_runs` et `api_calls` : leurs plafonds valent `null` sur les quatre paliers, donc rien ne se voit aujourd'hui, mais un `UPDATE` qui en poserait un ne serait respecté par personne — c'est pourquoi ces clés ont quitté la page publique.
11. **Aucun transport de courriel n'est branché.** `POST /api/auth/password/forgot` fabrique le jeton mais rien ne l'achemine : il n'est rendu dans la réponse qu'en développement (même repli que `TokenPair.refresh_token`, et c'est un oracle d'énumération assumé, gated sur `settings.is_development`). En production la route répond 202 et personne ne reçoit rien : **la réinitialisation de mot de passe est inutilisable en ligne** tant qu'un service d'envoi n'est pas branché. C'est le point n° 1 de `docs/rgpd.md` §5.
12. **Les durées de conservation sont annoncées et non appliquées.** Ni purge des comptes inactifs (3 ans), ni purge des documents commerciaux au-delà de dix ans, alors que la politique de confidentialité publique les annonce. Annoncer une durée sans l'appliquer est en soi un manquement. Celery est en place, c'est un lot court.
13. **Les quatre documents légaux sont des gabarits.** Aucun n'a été relu par un juriste et les valeurs de l'exploitant y sont des marqueurs entre crochets. Tant qu'ils sont dans cet état, **aucune CGV n'est opposable et aucun abonnement ne devrait être encaissé**. L'avertissement en tête de chaque document est ce qui rend cet état visible ; le retirer est une décision, pas un nettoyage.
14. **La carte d'aperçu d'un lien partagé reste générique.** Les robots d'iMessage, WhatsApp et Slack n'exécutent aucun JavaScript : ni le `document.title` dynamique ni aucune manipulation du DOM ne les atteint. Un aperçu portant le nom du chantier exige d'injecter les balises Open Graph **côté serveur** pour `/partage/:token` (nginx, ou une route backend dédiée). Pas d'`og:image` non plus : la balise exige une URL absolue, inconnue à la compilation, et une valeur en dur afficherait une image cassée dans chaque message envoyé.
15. **Le panneau d'inspection n'est monté que dans l'éditeur 2D.** Le composant est présentationnel exprès, pour être montable aussi dans le viewer 3D — mais recentrer une caméra Three.js sur un point du plan n'est pas le même geste que déplacer une vue Konva, et ce second hôte n'a pas été écrit. Le calepinage (`GET /laying-plan`) et l'aménagement (`POST /rooms/{id}/layouts`) n'ont eux aucune interface : les deux appels existent dans `frontend/src/api/client.ts`, aucun écran ne les invoque.
