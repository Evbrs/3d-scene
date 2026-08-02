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
| Auth | JWT via le pattern officiel FastAPI (`OAuth2PasswordBearer` + `passlib`/`python-jose`) | FastAPI ne fournit pas d'auth intégrée ; ce pattern est celui du tutoriel officiel, donc le plus fiable à faire générer par un agent IA (très bien documenté, faible risque d'API inventée) ; `fastapi-users` existe mais est en mode maintenance, pas un bon choix long terme |
| Tâches async | Celery + Redis | standard le plus répandu et le plus transférable sur le marché de l'emploi, indépendant du framework web (fonctionne à l'identique avec FastAPI) |
| Base de données | PostgreSQL | JSON pour la géométrie, tables normalisées pour le reste |
| Calcul géométrique | Python + `numpy` | scene graph, normales, extrusions |

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
