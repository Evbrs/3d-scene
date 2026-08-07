# Stratégie produit, différenciation et monétisation

*Document de décision. Il complète `spec-complete.md` (le contrat fonctionnel) en répondant à une
question que celui-ci ne traite pas : à qui ce produit s'adresse, pourquoi on le paierait, et ce
qu'il faut coder pour que ça soit vrai. Comme la spec, il ne se contourne pas silencieusement.*

---

## 1. Le segment retenu : l'artisan de second œuvre

**Décision : on vise l'artisan qui rénove (carreleur, plaquiste, cuisiniste, salle de bain,
peintre), pas le particulier qui aménage.**

Le segment grand public est verrouillé, et il l'est par un actif qu'on a explicitement décidé de
ne pas construire. Kazaplan, HomeByMe et Planner 5D sont gratuits ou quasi gratuits
(Planner 5D Premium à 4,99 €/mois) parce que leur revenu ne vient pas de l'utilisateur : il vient
des fabricants qui paient pour que leur catalogue soit dans l'outil. Notre mobilier est
**paramétrique et sans marque** (`spec-complete.md` §4.1), et le propriétaire refuse toute
intégration de catalogue externe type IKEA. Sur ce terrain-là, on ne gagne pas : on n'a pas la
même monnaie.

Sur le segment artisan, le rapport s'inverse. La référence de prix n'est plus 5 €/mois mais
19 à 49 € HT/mois — c'est ce que coûtent Tolteck (19 € HT/mois en engagement annuel, 25 € au
mois) et Obat (à partir de 25 € HT/mois). Et ces outils-là **n'ont aucune géométrie** : ils font
le devis, pas le plan.

### Ce que le code sait déjà faire et que le marché ne fait pas

Deux choix d'architecture déjà en place sont, sur cette niche, des avantages :

1. **Le modèle est organisé autour de la face, pas de la pièce.** `Face.covering` porte déjà
   `unit_width_cm`, `unit_height_cm` et `pattern`. Un artisan ne pense pas en pièces, il pense en
   murs à habiller et en mètres carrés à poser. Le modèle de données parle déjà sa langue.
2. **Le backend génère déjà une caméra d'élévation orthographique par mur**
   (`app/geometry/cameras.py`). L'élévation cotée par mur est le document que le carreleur donne
   à son poseur. Aucun outil grand public ne la produit.

S'y ajoute un avantage de coût structurel : le scene graph est calculé côté serveur et le
mobilier n'a aucun asset à télécharger. Le coût marginal d'une scène est quasi nul, ce qui rend
crédibles à la fois un palier gratuit généreux et un palier marque blanche à forte marge.

**Promesse produit :** *du relevé de la pièce au devis chiffré par mur, avec les élévations
cotées et le lien 3D à envoyer au client.* Les outils de chiffrage font le devis sans la 3D. Les
outils 3D font la 3D sans le devis. Personne ne tient les deux bouts.

### Conséquence sur les priorités

Le métré et le devis passent devant le photoréalisme. L'élévation vectorielle imprimable passe
devant la texture WebGL. Et on n'investit pas un euro dans un catalogue de marques — on en fait
au contraire un argument de vente : *catalogue complet dès le palier gratuit, rien à télécharger,
tout redimensionnable au centimètre.*

---

## 2. La fenêtre réglementaire : la facture électronique

**C'est le fait le plus important de ce document, et il est daté.**

| Échéance | Obligation |
|---|---|
| **1er septembre 2026** | **Toutes** les entreprises, y compris micro et TPE, doivent pouvoir **recevoir** une facture électronique. Grandes entreprises et ETI doivent aussi l'**émettre**. |
| **1er septembre 2027** | Les TPE, PME et indépendants doivent à leur tour **émettre** en électronique. |

Les formats structurés acceptés sont **Factur-X**, UBL et CII, et la transmission passe par une
plateforme agréée.

Ce que ça veut dire pour nous, très concrètement : **chaque artisan de France est en train de
devoir changer d'outil**, dans une fenêtre de douze mois. C'est le moment où un coût de
changement habituellement rédhibitoire tombe à zéro, parce qu'il est payé de toute façon.

Et surtout, **Factur-X se génère entièrement chez nous** : c'est un PDF/A-3 avec un XML CII
embarqué. On produit déjà des PDF avec reportlab. Générer un devis puis une facture conformes ne
demande **aucun service externe** — ce qui satisfait exactement la contrainte du propriétaire. La
transmission via plateforme agréée reste chez l'artisan, avec l'outil qu'il a déjà : on produit
le fichier conforme, on ne prétend pas être une plateforme agréée.

> **Limite assumée, à écrire noir sur blanc dans l'interface :** nous produisons un fichier au
> format réglementaire. Nous ne sommes pas une plateforme de dématérialisation agréée et nous ne
> transmettons rien à l'administration. Prétendre le contraire serait faux et juridiquement
> dangereux.

### Les mentions qui rendent un devis d'artisan valable

Un devis de bâtiment n'est pas un tableau de prix. Il doit porter, sous peine d'être inopposable :

- **l'assurance décennale** : nom de l'assureur, numéro de police, couverture géographique —
  obligatoire sur les devis *et* les factures ;
- l'identité complète : SIRET, forme juridique, capital, RCS, adresse, numéro de TVA ;
- l'adresse du chantier, distincte de celle du client ;
- **le taux de TVA par ligne** et non global : la rénovation relève de 10 % (logement de plus de
  deux ans), 5,5 % (rénovation énergétique) ou 20 %, et un même chantier mélange les trois ;
- **l'attestation TVA du client** : depuis le 16 février 2025, une attestation portée sur le devis
  ou la facture remplace les formulaires CERFA 13947 et 13948. Sans elle, c'est l'artisan qui est
  redressé, pas nous — donc c'est un service qu'on lui rend ;
- durée de validité, conditions de règlement, pénalités de retard, indemnité forfaitaire de
  recouvrement, droit de rétractation et médiateur de la consommation en B2C ;
- une numérotation **chronologique et sans trou**, générée en base et non en Python.

> Cette liste est une classe d'exigences relevée dans des sources professionnelles, pas un avis
> juridique. Elle doit être validée par un expert-comptable avant mise en production, et le
> produit doit rendre ces champs **paramétrables** plutôt que codés en dur — c'est la seule
> manière de suivre une réglementation qui bouge.

C'est cette couche de conformité qui *est* la valeur de Tolteck et d'Obat. Sans elle, l'artisan
ressort notre chiffrage et le ressaisit ailleurs pour l'envoyer : on ne remplace rien, et le prix
de 29 à 79 € n'est pas défendable. Avec elle, on remplace son outil de devis **et** on lui donne
la 3D en plus.

---

## 3. Les fonctionnalités qui changent la donne

Classées par (valeur × différenciation) / coût. Chacune est ancrée dans ce que le code permet
déjà — c'est le critère qui les fait entrer dans cette liste plutôt que dans une liste d'idées.

### 3.1 Métré automatique et devis chiffré par face — *le cœur*

Toutes les données sont **déjà calculées** : `length_cm`, `height_cm`, `holes`, `floor_area_cm2`.
Le métré n'est qu'une fonction pure sur le scene graph : surface brute, surface nette après
déduction des ouvertures, linéaire de plinthe (périmètre moins la largeur des portes), volume,
nombre d'unités de revêtement d'après les dimensions d'unité déjà stockées.

Un barème de prix par organisation, une règle de correspondance `matière → ligne de prix`, et le
devis se génère en un clic depuis le plan. C'est la seule raison économique de payer.

**Piège à éviter, identifié à l'audit :** `floor_area_cm2` est aujourd'hui l'aire de la **ligne
médiane des murs**, surévaluée de 6 % (murs de 10 cm) à 20 % (murs de pierre de 30 cm). Facturer
là-dessus, c'est un litige. On expose `net_floor_area_cm2` **à côté** de l'existant, sans toucher
à la fixture qui fige la valeur actuelle.

### 3.2 Devis et facture Factur-X conformes — *la fenêtre réglementaire*

Voir §2. Génération 100 % interne. Le devis accepté se transforme en facture en conservant la
copie des prix : une ligne de devis **copie** son libellé, son prix unitaire et son taux de TVA au
moment de l'émission, et ne fait aucune jointure de lecture vers le barème. En France un devis
signé est un contrat : s'il change après envoi, c'est un problème juridique, pas un défaut
d'affichage.

### 3.3 Élévation vectorielle cotée, par mur — *le document de chantier*

Une page A4 paysage par mur : rectangle du mur, trous en blanc, cotes de longueur, de hauteur,
d'allège et de largeur pour chaque ouverture, calepinage hachuré à la trame de pose réelle.
Vectoriel, donc net à l'impression et bien moins coûteux qu'une texture WebGL.

`outline` et `holes` sont déjà émis, en centimètres, au bon format. **Cette fonctionnalité ne
dépend techniquement de rien d'autre** — elle n'a aucune raison d'attendre le moteur de devis.
C'est l'artefact le plus démontrable du produit et il est à portée immédiate.

### 3.4 Lien client, signature « bon pour accord » et boucle d'acquisition

Le lien de partage existe déjà. Ce qui manque en fait un moteur commercial :

- un **compteur de vues** et une alerte à la première ouverture — le signal de relance le plus
  rentable de la vente de chantier : *« votre client a ouvert le devis trois fois hier »* ;
- l'acceptation **« bon pour accord »** avec horodatage : c'est le moment exact de la conversion,
  et aujourd'hui il se passe ailleurs ;
- un bandeau *« Projet préparé par {entreprise} »*, et la mention de notre produit affichée
  **uniquement** sur les paliers gratuit et Artisan : c'est le levier du palier marque blanche, et
  chaque lien partagé est vu par un prospect qui n'a pas de compte.

Deux corrections indispensables avant d'en faire un canal : le lien livre aujourd'hui la
géométrie **complète du logement** plus le nom du projet — typiquement *« Rénovation Dupont,
12 rue des Lilas »* — sans authentification, alors que l'état ne vise qu'une pièce ; et rien
n'empêche son indexation par un moteur de recherche.

### 3.5 Variantes chiffrées — *le levier de panier moyen*

La même pièce, trois habillages, trois prix, un seul lien où le client choisit. Présenter trois
options chiffrées plutôt qu'une augmente mécaniquement le panier moyen et déplace la question de
« est-ce que je le fais » à « laquelle je prends ». Techniquement c'est une duplication de projet
et un champ auto-référent — quelques heures pour un effet commercial direct.

### 3.6 Éditeur professionnel : glisser-déposer, cotation, annuler/refaire

C'est le plafond de verre de l'adoption, et il vient du **modèle**, pas de l'interface :
`Element.face_id` est obligatoire, donc **tout meuble est adossé à une face**. Un lit, une table,
un îlot de cuisine sont aujourd'hui impossibles ou forcés contre un mur. Rendre `face_id`
nullable et ajouter un placement dans le repère de la pièce est un **amendement de spec §5
explicite**, pas une improvisation de ticket.

Une fois ce verrou levé : palette de mobilier en glisser-déposer sur le plan, aimantation aux
murs et aux angles droits, saisie numérique de la cote pendant le tracé (un plan de rénovation se
saisit depuis un relevé au mètre laser, pas à la souris), cotation affichée en permanence,
annuler/refaire, sélection multiple.

Un point d'architecture en découle : déplacer quinze meubles impose aujourd'hui quinze
allers-retours strictement sériels, puisque chaque écriture incrémente la version du projet. Il
faut une route de **lot** prenant une version unique et une liste d'opérations appliquées en une
transaction.

### 3.7 Import d'un fond de plan et calibrage à deux clics

Le premier geste du métier : l'artisan arrive avec le plan de l'architecte ou une photo du plan
existant. Sans ça, chaque utilisateur ressaisit son logement au mètre depuis zéro — c'est le
premier frein à l'adoption, et il est à la portée d'un lot court.

### 3.8 L'IA, et ce qu'elle doit être ici

Le dépôt s'appelle `3d-viewer-IA` mais `plan-generation-ia.md` porte sur le **développement**
assisté par IA : aucune fonctionnalité d'IA n'est promise par la spec. Le nom est donc libre.

**Décision : l'IA de ce produit est algorithmique et locale.** Pas d'appel sortant, pas de clé,
pas de latence, un résultat déterministe donc testable par fixtures exactement comme la
géométrie — et une facture d'infrastructure qui ne dépend pas d'un tiers. Un adaptateur LLM
optionnel pourra venir plus tard pour la formulation en langage naturel, sans qu'aucune
fonctionnalité n'en dépende.

Trois usages, par valeur décroissante :

1. **Contrôle de conformité du plan.** Un moteur de règles qui relit la géométrie et signale ce
   qu'un homme de métier verrait : passage inférieur à 90 cm, débattement de porte qui percute un
   meuble ou une autre porte, hauteur sous plafond insuffisante, allège de fenêtre hors norme,
   ouverture trop proche d'un angle pour être réalisable, pièce humide sans point d'eau cohérent.
   Sortie : une liste d'anomalies cliquables qui recentrent le plan sur le problème. C'est du
   calcul géométrique pur sur des données qu'on a déjà, et ça évite une reprise de chantier —
   donc ça se vend tout seul.

2. **Calepinage et chutes.** Nombre d'unités entières, nombre de coupes et taux de chute par motif
   de pose — un chevron consomme de l'ordre de 15 % contre 8 % en pose droite. **C'est le chiffre
   qui rend le devis crédible auprès d'un homme de métier**, et aucun outil grand public ne le
   donne.

3. **Aménagement automatique sous contraintes.** Poser le mobilier d'une pièce en respectant les
   dégagements, les circulations, les débattements de porte et les adjacences (un plan de travail
   contre un mur, une table au centre). C'est un problème d'optimisation sous contraintes, pas un
   problème de génération : on propose deux ou trois implantations valides et l'utilisateur
   choisit. Démarrable sur la salle de bain et la cuisine, qui sont les pièces les plus
   contraintes et les plus lucratives.

### 3.9 Mode hors ligne

Un chantier en sous-sol est exactement l'endroit où le produit sert. Une coquille applicative et
la dernière scène en cache suffisent à rendre l'outil utilisable là où on l'utilise — et c'est la
meilleure réponse à l'objection *« pourquoi pas une application native »*.

---

## 4. Monétisation — côté éditeur

Prix en euros **hors taxes par mois**, tarif annuel entre parenthèses (deux mois offerts).

| Palier | Prix | Pour qui | Ce qui est inclus | Ce qui est bloqué |
|---|---|---|---|---|
| **Découverte** | **0 €** | Essayer, et faire circuler des liens | 1 chantier actif, 2 pièces, 3D complète, catalogue entier, export PDF **filigrané**, lien de partage 30 jours avec notre mention | Devis chiffré (métré visible sur la première face, total masqué), export sans filigrane, élévations cotées |
| **Artisan** | **29 €** (24 €) | Le solo, cœur de cible | Chantiers illimités, **devis + facture Factur-X**, élévations cotées, calepinage et chutes, exports sans filigrane, liens 90 jours, contrôle de conformité, 1 siège | Multi-utilisateurs, marque blanche, API |
| **Entreprise** | **79 €** (65 €) + 19 €/siège | 2 à 15 personnes | Tout Artisan, plus rôles et invitations, barème de prix partagé, **marque blanche du lien client**, signature « bon pour accord », variantes chiffrées, aménagement automatique, API | — |
| **Réseau** | **sur devis, à partir de 390 €** | Franchises, réseaux de cuisinistes, négoces | Sous-domaine et identité visuelle, catalogue et barème imposés au réseau, SSO, API, statistiques par agence | — |

**Positionnement du prix.** 29 € se lit face à Tolteck (19-25 €) et Obat (à partir de 25 €) : on
est au même niveau *et* on apporte la 3D et les élévations, que ni l'un ni l'autre n'ont. On ne
cherche pas à être moins cher : sur ce segment, moins cher signale « outil de particulier ».

**L'essai se déclenche au premier geste monétisé, pas à l'inscription.** 14 jours de palier
Artisan, sans carte, déclenchés quand l'utilisateur essaie de générer son premier devis ou son
premier export propre. Un essai qui démarre à l'inscription est consommé par quelqu'un qui n'a
pas encore compris le produit ; celui-ci démarre au moment exact où la valeur est comprise.

**Les trois murs de paiement**, et comment ils sont posés :

- *générer le devis* : le métré s'affiche réellement sur la première face, le total est masqué —
  on montre que le calcul est juste avant de demander de payer ;
- *exporter le PDF* : le fichier filigrané **se télécharge vraiment**, et un bouton propose de
  retirer le filigrane. Bloquer le téléchargement ferait douter du résultat ; le livrer filigrané
  le prouve ;
- *créer un deuxième chantier* : la limite la plus lisible du palier gratuit.

**Le dépassement de quota déclasse, il ne supprime jamais.** Les projets excédentaires passent en
lecture seule via `archived_at`. La valeur reste visible : c'est la situation la plus favorable au
réabonnement, et la seule qui ne détruit pas la confiance.

### Garde-fous techniques à coder

- Les limites vivent **en base** (`plan_catalog.limits` en JSONB), pas en dur dans le code :
  accorder une remise ou déplacer une limite doit être une ligne SQL, pas un déploiement — sinon
  chaque négociation commerciale devient un ticket de développement. C'est aussi le correctif
  d'urgence quand un quota bloque un client payant en pleine journée de chantier.
- Les compteurs s'incrémentent par un **unique `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`** :
  un `SELECT` suivi d'un `UPDATE` laisse deux onglets ouverts passer au-dessus de la limite.
- La période de comptage est celle de la **facturation**, pas le mois calendaire — sinon un
  abonnement souscrit le 20 offre une remise à zéro gratuite le 1er.
- `usage_event` est **append-only** avec une clé d'idempotence : ajouter une métrique plus tard
  est facile, reconstituer son historique est impossible. On pose donc dès maintenant
  `projects_active`, `exports_pdf`, `quotes_issued`, `shared_view_hits`, `ai_runs`, `api_calls`,
  **et les métriques produit** (activation, délai jusqu'au premier devis, point d'abandon) — sans
  elles, on ne peut ni corriger l'accueil ni arbitrer une grille tarifaire.
- La **résolution d'export et le filigrane sont décidés par le serveur**, jamais par le client :
  un filigrane apposé côté navigateur se retire en dix secondes par la console.
- Les colonnes `external_customer_id` / `external_subscription_id` existent **dès la première
  migration**, même vides : les ajouter plus tard signifierait migrer une table qui reçoit déjà
  des webhooks en production.
- On bloque la **création**, jamais la lecture.

---

## 5. Monétisation — côté client, c'est-à-dire comment l'artisan gagne de l'argent avec

C'est ce qui rend l'abonnement indolore : à 29 € HT/mois, l'outil doit se rembourser sur le
premier chantier. Quatre mécanismes, à mettre en avant dans l'interface elle-même, pas seulement
sur une page de vente.

**1. Il signe plus souvent, et plus vite.** Le rendez-vous de relevé devient le rendez-vous de
vente : la pièce est modélisée sur place, le client voit sa salle de bain en 3D et le prix
s'affiche dans la foulée. Le devis part avant que le concurrent n'ait sorti son mètre. Un point de
taux de transformation gagné sur un panier moyen de rénovation de salle de bain paie l'année.

**2. Il facture l'étude.** Un relevé, un plan coté, trois élévations et une vue 3D constituent un
livrable — donc quelque chose qui se facture (couramment de 100 à 300 € pour une conception de
pièce), et qui se déduit du chantier s'il est signé. C'est le passage du devis gratuit subi à
l'étude payante, et c'est le produit qui le rend possible en fournissant le document.

**3. Il vend plus cher.** Les variantes chiffrées (§3.5) déplacent la décision de *« est-ce que je
le fais »* vers *« laquelle je prends »*. Le calepinage et le taux de chute justifient les
quantités ligne à ligne : un client qui comprend pourquoi il y a 12 % de carrelage en plus ne
négocie pas ce poste.

**4. Il paraît plus grand qu'il n'est.** Sur les paliers Entreprise et Réseau, le lien client
porte **son** logo et **son** nom, sans mention de notre produit. Un artisan seul livre un
dossier qui a l'air de sortir d'un bureau d'études. C'est exactement ce qui justifie l'écart de
prix entre 29 et 79 €, et c'est la raison d'être commerciale du multi-locataire.

**Et le palier Réseau est le même mécanisme, en gros.** Une franchise ou un négoce équipe ses
adhérents, impose son barème et son catalogue, et récupère les statistiques par agence. Marge
élevée, coût marginal quasi nul puisque le mobilier n'a aucun asset — c'est là que le choix du
paramétrique cesse d'être une contrainte pour devenir l'avantage économique du produit.

---

## 6. Ce qu'il faut poser maintenant pour ne pas tout refaire

Par ordre : chacun de ces points coûte peu aujourd'hui et coûte une migration sur une table
chaude si on attend.

1. **`organization` et `membership`** — les droits doivent être portés par une entité qui peut
   avoir des sièges et un moyen de paiement. `owner_id` reste une information de création et cesse
   d'être ce qui autorise. Point de vigilance : réécrire les permissions est le chemin le plus
   court vers une fuite de données entre clients, donc le test *« un membre d'une autre
   organisation reçoit 404 »* s'écrit sur chaque endpoint **avant** la réécriture.
2. **Les champs d'entreprise sur `organization`** — SIRET, forme juridique, capital, RCS, adresse,
   numéro de TVA, **assureur décennal, numéro de police et couverture** : sans eux, aucun devis
   émis n'est valable (§2).
3. **Les montants en centimes entiers**, jamais en flottant. Tout le reste du modèle est en
   flottants centimètres et la tentation de continuer sera forte.
4. **`usage_event` append-only**, dès maintenant, avec les métriques produit (§4).
5. **`face_id` nullable et placement dans le repère de la pièce** (§3.6) — amendement de spec §5 à
   acter explicitement.
6. **`price_book` / `price_item` par organisation**, et la copie des valeurs dans `quote_line`.
7. **L'époque de cache par organisation** — sans elle, une modification de catalogue chez un
   client invalide le cache de tous les autres, et devient un signal d'activité observable entre
   clients.

---

## 7. Ce qui est écarté, et pourquoi

- **Catalogue de marques et assets 3D téléchargés** — c'est le fossé défensif des concurrents
  grand public, monétisé auprès des fabricants. On perdrait sur leur terrain, et le propriétaire
  l'a explicitement exclu. On en fait un argument commercial inverse.
- **Internationalisation et unités impériales** — réels, mais le positionnement est l'artisan
  français, où la référence de prix est Tolteck/Obat et où la conformité est notre valeur.
  L'introduire maintenant ferait payer un surcoût à chaque lot sans une ligne de revenu. À
  rouvrir comme décision explicite le jour où un marché anglophone est visé, en sachant que le
  coût croît avec chaque écran écrit d'ici là.
- **CRDT/Yjs pour la collaboration temps réel** — disproportionné alors que le verrouillage
  optimiste, la version et l'invalidation de cache par version sont déjà en place. On retient la
  diffusion des changements de version et le partage par rôles, qui apportent l'essentiel.
- **Devenir plateforme de dématérialisation agréée** — métier réglementé, hors de portée et hors
  sujet. On produit le fichier conforme, l'artisan le transmet avec son outil.
- **Post-traitement photoréaliste (SSAO, etc.)** — rapport coût/bénéfice défavorable face au métré
  et à l'élévation imprimable. On retient en revanche les ombres et le tone mapping, qui corrigent
  un vrai défaut : l'éclairage actuel écrête au point de rendre un beige clair et un blanc
  indiscernables, sur un outil dont le but est de choisir un revêtement.

---

## Sources

Prix et positionnement des concurrents, et calendrier réglementaire, relevés en août 2026 :

- [Avis Tolteck : tarifs et fonctionnalités 2026 — independant.io](https://independant.io/avis/tolteck/)
- [Prix d'un logiciel de devis facture artisan en 2026 — Nexartis](https://nexartis.fr/blog/prix-logiciel-devis-facture-artisan)
- [Obat : avis et tarifs 2026 — independant.io](https://independant.io/avis/obat/)
- [Facturation électronique BTP — Obat](https://www.obat.fr/devis-factures/facturation-electronique/)
- [La facturation électronique obligatoire au 1er septembre 2026 — Urssaf](https://www.urssaf.fr/accueil/actualites/facturation-electronique.html)
- [Calendrier facture électronique 2026-2027 — Cegid](https://www.cegid.com/fr/facture-electronique-obligatoire/calendrier-facture-electronique/)
- [Mentions obligatoires sur un devis artisan — Manay](https://www.manay.fr/blog/artisan/mentions-obligatoires-devis/)
- [Garantie décennale sur les devis — Orus](https://www.orus.eu/blog-orus/garantie-decennale-devis)

*Les montants et échéances ci-dessus engagent une décision produit, pas un conseil juridique ou
fiscal. À faire valider par un expert-comptable avant la première facture émise.*
