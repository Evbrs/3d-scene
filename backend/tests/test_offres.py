"""Les trois murs de paiement, la grille en base, l'essai et le déclassement.

`docs/strategie-produit.md` §4 décrit trois murs, et chacun a une forme précise qu'un test doit
tenir — parce que c'est la forme, pas le blocage, qui fait la différence commerciale :

1. **le devis** : le métré s'affiche réellement, seul le document chiffré demande un palier ;
2. **l'export** : le PDF filigrané **se télécharge vraiment**. Bloquer le téléchargement ferait
   douter du résultat ; le livrer filigrané le prouve. Et le filigrane est décidé par le serveur,
   jamais par le client ;
3. **le deuxième chantier** : la limite la plus lisible du palier gratuit.

Deux propriétés transversales sont vérifiées ici et nulle part ailleurs :

- **les limites viennent de la base.** Un `UPDATE` sur `plan_catalog.limits` change le
  comportement de l'API sans redéploiement. C'est la raison d'être du modèle, et un quota codé en
  dur passerait tous les autres tests ;
- **l'essai démarre au premier geste monétisé, pas à l'inscription.** Un compte neuf n'a aucun
  abonnement ; il en obtient un au moment exact où il demande son premier devis ou son deuxième
  chantier.
"""

import base64
import re
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.models.billing_plan import (
    PlanCatalog,
    Subscription,
    SubscriptionStatus,
    UsageEvent,
    UsageMetric,
)
from app.models.plan import Project
from app.services.seed_plans import (
    ENFORCEMENT_POINTS,
    FEATURE_EXPORTS_WITHOUT_WATERMARK,
    FEATURE_LABELS,
    FEATURE_QUOTES,
    LIMIT_ACTIVE_PROJECTS,
    LIMIT_ENFORCEMENT_POINTS,
    LIMIT_LABELS,
    LIMIT_SEATS,
    PLAN_ARTISAN,
    PLAN_BUSINESS,
    PLAN_DISCOVERY,
    PLAN_GRID,
    PLAN_NETWORK,
)
from tests.conftest import subscribe
from tests.test_permissions_locataire import logged_in

CARRE: list[list[float]] = [[0, 0], [400, 0], [400, 300], [0, 300]]


def _watermarked(content: bytes) -> bool:
    """Vrai si le PDF porte la mention « APERÇU ».

    Les flux de page produits par reportlab sont encodés en ASCII85 puis compressés : la chaîne
    n'est pas lisible dans les octets bruts. On cherche son préfixe ASCII, le « Ç » étant écrit en
    octal par le format.
    """
    return any(
        b"APER" in payload
        for payload in (
            _decoded(stream.group(1).strip())
            for stream in re.finditer(rb"stream\r?\n(.*?)\s*endstream", content, re.DOTALL)
        )
        if payload is not None
    )


def _page_count(content: bytes) -> int:
    """Nombre de pages du PDF, lu dans les objets `/Type /Page` du fichier.

    Compté sur les octets plutôt qu'avec une bibliothèque de lecture : le dépôt n'en embarque
    aucune, et la structure produite par reportlab déclare une page par objet, sans indirection.
    `/Type /Pages` — le nœud de l'arbre, au pluriel — est exclu par la borne de mot.
    """
    return len(re.findall(rb"/Type\s*/Page[^s]", content))


def _decoded(payload: bytes) -> bytes | None:
    try:
        if payload.endswith(b"~>"):
            payload = base64.a85decode(payload[:-2], ignorechars=b" \t\r\n")
        return zlib.decompress(payload)
    except (ValueError, zlib.error):
        return None


async def _organization_id(client: AsyncClient) -> int:
    """Force la création paresseuse de l'organisation personnelle, puis rend son identifiant."""
    await client.post("/api/projects", json={"name": "Premier chantier"})
    return int((await client.get("/api/organizations")).json()[0]["id"])


async def _consume_trial(session: AsyncSession, organization_id: int) -> None:
    """Place l'organisation dans l'état « essai déjà fait et terminé ».

    C'est l'état intéressant du produit : celui où les murs se referment. L'écrire directement en
    base plutôt que d'attendre quatorze jours est le seul moyen de le tester.
    """
    passe = datetime.now(UTC) - timedelta(days=20)
    session.add(
        Subscription(
            organization_id=organization_id,
            plan_code=PLAN_ARTISAN,
            status=SubscriptionStatus.TRIALING,
            current_period_start=passe,
            current_period_end=passe + timedelta(days=14),
            trial_ends_at=passe + timedelta(days=14),
        )
    )
    await session.commit()


async def _set_limit(session: AsyncSession, code: str, key: str, value: Any) -> None:
    """Change une limite **en base**, comme le ferait une remise commerciale par `psql`."""
    plan = (
        await session.execute(select(PlanCatalog).where(col(PlanCatalog.code) == code))
    ).scalar_one()
    plan.limits = {**plan.limits, key: value}
    await session.commit()


# --- La grille vient de la base -------------------------------------------------------------------


async def test_the_public_grid_is_served_from_the_catalog(client: AsyncClient) -> None:
    """La page tarifs n'a rien à coder en dur : prix, limites et libellés viennent d'ici.

    La route est publique : une grille de prix s'affiche avant l'inscription, et elle ne contient
    rien qui appartienne à un locataire.
    """
    response = await client.get("/api/plans")
    assert response.status_code == 200, response.text
    body = response.json()

    codes = [plan["code"] for plan in body["plans"]]
    assert codes == [PLAN_DISCOVERY, PLAN_ARTISAN, PLAN_BUSINESS, PLAN_NETWORK]

    par_code = {plan["code"]: plan for plan in body["plans"]}
    # `docs/strategie-produit.md` §4, en centimes entiers : 0 / 29 (24) / 79 (65) + 19 par siège.
    assert par_code[PLAN_DISCOVERY]["monthly_price_cents"] == 0
    assert par_code[PLAN_ARTISAN]["monthly_price_cents"] == 2_900
    assert par_code[PLAN_ARTISAN]["yearly_price_cents"] == 2_400
    assert par_code[PLAN_BUSINESS]["monthly_price_cents"] == 7_900
    assert par_code[PLAN_BUSINESS]["yearly_price_cents"] == 6_500
    assert par_code[PLAN_BUSINESS]["seat_price_cents"] == 1_900
    # « Sur devis, à partir de 390 € » : le tarif annuel se négocie, on n'en invente pas.
    assert par_code[PLAN_NETWORK]["monthly_price_cents"] == 39_000
    assert par_code[PLAN_NETWORK]["yearly_price_cents"] is None

    assert par_code[PLAN_DISCOVERY]["limits"][LIMIT_ACTIVE_PROJECTS] == 1
    assert par_code[PLAN_ARTISAN]["limits"][LIMIT_ACTIVE_PROJECTS] is None
    assert body["feature_labels"][FEATURE_QUOTES]
    assert body["trial_days"] == 14


async def test_a_plan_hidden_in_database_never_reaches_the_pricing_page(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Un palier négocié pour un réseau existe en base sans figurer sur la page tarifs."""
    await client.get("/api/plans")
    session.add(
        PlanCatalog(
            code="reseau-bretagne",
            name="Réseau Bretagne",
            monthly_price_cents=32_000,
            is_public=False,
            sort_order=50,
        )
    )
    await session.commit()

    codes = [plan["code"] for plan in (await client.get("/api/plans")).json()["plans"]]
    assert "reseau-bretagne" not in codes


async def test_moving_a_limit_in_database_moves_the_wall_without_a_deployment(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Le correctif d'urgence : un quota qui bloque un client se lève par une ligne SQL.

    C'est **la** propriété que le modèle existe pour offrir. Un quota codé en dur passerait tous
    les autres tests de ce fichier et échouerait sur celui-ci.
    """
    organization_id = await _organization_id(auth_client)
    await _consume_trial(session, organization_id)

    refuse = await auth_client.post("/api/projects", json={"name": "Deuxième chantier"})
    assert refuse.status_code == 429, refuse.text

    await _set_limit(session, PLAN_DISCOVERY, LIMIT_ACTIVE_PROJECTS, 3)

    accepte = await auth_client.post("/api/projects", json={"name": "Deuxième chantier"})
    assert accepte.status_code == 201, accepte.text


# --- Mur n° 3 : le deuxième chantier ------------------------------------------------------------


async def test_the_second_project_opens_the_trial_instead_of_refusing(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """L'essai démarre **ici**, pas à l'inscription.

    Un essai qui démarre à l'inscription est consommé par quelqu'un qui n'a pas encore compris le
    produit. Celui-ci démarre au moment exact où la valeur est comprise — et sans carte, donc sans
    écran intermédiaire : le deuxième chantier se crée.
    """
    organization_id = await _organization_id(auth_client)

    avant = (
        await session.execute(
            select(Subscription).where(col(Subscription.organization_id) == organization_id)
        )
    ).scalars().all()
    assert avant == [], "l'inscription ne doit consommer aucun essai"

    deuxieme = await auth_client.post("/api/projects", json={"name": "Deuxième chantier"})
    assert deuxieme.status_code == 201, deuxieme.text

    apres = (
        await session.execute(
            select(Subscription).where(col(Subscription.organization_id) == organization_id)
        )
    ).scalars().one()
    assert apres.plan_code == PLAN_ARTISAN
    assert apres.status is SubscriptionStatus.TRIALING
    assert apres.trial_ends_at is not None


async def test_once_the_trial_is_spent_the_second_project_is_refused_in_a_readable_way(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """429 avec un corps machine-lisible : le frontend doit proposer le bon palier sans lire le
    français."""
    organization_id = await _organization_id(auth_client)
    await _consume_trial(session, organization_id)

    refuse = await auth_client.post("/api/projects", json={"name": "Deuxième chantier"})

    assert refuse.status_code == 429, refuse.text
    corps = refuse.json()
    assert corps["code"] == "quota_exceeded"
    assert corps["metric"] == UsageMetric.PROJECTS_ACTIVE
    assert corps["limit"] == 1
    assert corps["current"] == 1
    assert corps["current_plan"] == PLAN_DISCOVERY
    assert corps["required_plan"] == PLAN_ARTISAN


# --- Mur n° 1 : le devis chiffré ----------------------------------------------------------------


async def test_the_takeoff_stays_open_while_the_priced_quote_is_walled(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """On prouve que le calcul est juste **avant** de demander de payer.

    Le métré reste entièrement lisible sans abonnement — surfaces nettes, calepinage, linéaires.
    Seul le document chiffré demande un palier. Bloquer aussi le métré supprimerait la seule
    démonstration que le produit sait faire son travail.
    """
    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    await auth_client.post(
        f"/api/projects/{project_id}/rooms", json={"name": "Salle d'eau", "polygon": CARRE}
    )
    organization_id = int((await auth_client.get("/api/organizations")).json()[0]["id"])
    await _consume_trial(session, organization_id)

    metre = await auth_client.get(f"/api/projects/{project_id}/takeoff")
    assert metre.status_code == 200, metre.text
    assert metre.json()["rooms"][0]["faces"][0]["net_area_m2"] is not None

    devis = await auth_client.post(
        f"/api/projects/{project_id}/quotes", json={"client_name": "Madame Dupont"}
    )
    assert devis.status_code == 402, devis.text
    corps = devis.json()
    assert corps["code"] == "feature_required"
    assert corps["feature"] == FEATURE_QUOTES
    assert corps["current_plan"] == PLAN_DISCOVERY
    assert corps["required_plan"] == PLAN_ARTISAN


async def test_the_first_quote_opens_the_trial(auth_client: AsyncClient) -> None:
    """Le premier devis demandé est un geste monétisé : il ouvre l'essai et il aboutit."""
    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    await auth_client.post(
        f"/api/projects/{project_id}/rooms", json={"name": "Cuisine", "polygon": CARRE}
    )

    devis = await auth_client.post(
        f"/api/projects/{project_id}/quotes", json={"client_name": "Madame Dupont"}
    )
    assert devis.status_code == 201, devis.text

    organization_id = int((await auth_client.get("/api/organizations")).json()[0]["id"])
    etat = (await auth_client.get(f"/api/organizations/{organization_id}/subscription")).json()
    assert etat["plan"]["code"] == PLAN_ARTISAN
    assert etat["subscription"]["status"] == SubscriptionStatus.TRIALING
    assert etat["trial_available"] is False


async def test_issuing_a_quote_is_counted_once_even_on_a_double_click(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """La clé d'idempotence est l'identifiant du devis, qui ne s'émet qu'une fois."""
    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    await auth_client.post(
        f"/api/projects/{project_id}/rooms", json={"name": "Cuisine", "polygon": CARRE}
    )
    quote_id = int(
        (
            await auth_client.post(
                f"/api/projects/{project_id}/quotes", json={"client_name": "Madame Dupont"}
            )
        ).json()["id"]
    )

    assert (await auth_client.post(f"/api/quotes/{quote_id}/issue")).status_code == 200
    # Deuxième clic : le devis est déjà émis, la route refuse et rien n'est compté deux fois.
    assert (await auth_client.post(f"/api/quotes/{quote_id}/issue")).status_code == 409

    events = (
        await session.execute(
            select(UsageEvent).where(col(UsageEvent.metric) == UsageMetric.QUOTES_ISSUED)
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].event_metadata["quote_id"] == quote_id


# --- Mur n° 2 : l'export filigrané --------------------------------------------------------------


async def test_the_free_export_downloads_a_real_watermarked_file(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Le fichier **se télécharge**, filigrané. C'est tout l'inverse d'un blocage.

    Bloquer le téléchargement ferait douter du résultat ; le livrer filigrané prouve que le
    document existe et qu'il est juste.
    """
    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    await auth_client.post(
        f"/api/projects/{project_id}/rooms", json={"name": "Salon", "polygon": CARRE}
    )
    organization_id = int((await auth_client.get("/api/organizations")).json()[0]["id"])
    await _consume_trial(session, organization_id)

    reponse = await auth_client.get(f"/api/projects/{project_id}/exports/pdf/direct")

    assert reponse.status_code == 200, reponse.text
    assert reponse.headers["content-type"] == "application/pdf"
    assert len(reponse.content) > 1_000
    assert _watermarked(reponse.content)


async def test_the_watermark_disappears_with_the_plan_and_never_with_a_query_parameter(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Un filigrane retirable par le client n'en est pas un.

    Le même projet, exporté sur le palier gratuit puis sur un palier qui inclut l'export propre,
    ne rend pas le même fichier — et aucun paramètre de requête n'a de prise dessus.
    """
    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    await auth_client.post(
        f"/api/projects/{project_id}/rooms", json={"name": "Salon", "polygon": CARRE}
    )
    organization_id = int((await auth_client.get("/api/organizations")).json()[0]["id"])
    await _consume_trial(session, organization_id)

    filigrane = (await auth_client.get(f"/api/projects/{project_id}/exports/pdf/direct")).content
    assert _watermarked(filigrane)

    force = (
        await auth_client.get(
            f"/api/projects/{project_id}/exports/pdf/direct?watermark=false&watermark=0"
        )
    ).content
    assert _watermarked(force), "un paramètre de requête n'a aucune prise sur le filigrane"

    abonnement = (
        await session.execute(
            select(Subscription).where(col(Subscription.organization_id) == organization_id)
        )
    ).scalars().one()
    abonnement.status = SubscriptionStatus.ACTIVE
    abonnement.trial_ends_at = None
    abonnement.current_period_end = datetime.now(UTC) + timedelta(days=20)
    await session.commit()

    propre = (await auth_client.get(f"/api/projects/{project_id}/exports/pdf/direct")).content

    assert not _watermarked(propre)
    # Le fichier abonné est **plus lourd**, et c'est le sens attendu depuis l'amendement A14 : il
    # perd le filigrane mais gagne les quatre planches d'élévation que le palier gratuit n'a pas.
    # Comparer les tailles ne dirait plus grand-chose ; on compte les pages, qui sont ce que le
    # palier change réellement.
    assert _page_count(propre) > _page_count(filigrane)


async def test_a_replayed_export_task_is_not_billed_twice(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """L'identifiant de tâche Celery est la clé d'idempotence.

    Après un incident du courtier, la même tâche revient. Sans cette clé, le client paierait deux
    exports pour un fichier qu'il n'a demandé qu'une fois.
    """
    from app.tasks.exports import export_project_pdf

    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    await auth_client.post(
        f"/api/projects/{project_id}/rooms", json={"name": "Salon", "polygon": CARRE}
    )

    demande = await auth_client.post(f"/api/projects/{project_id}/exports/pdf")
    assert demande.status_code == 202, demande.text
    task_id = demande.json()["task_id"]

    # Rejeu à l'identique, exactement comme le ferait un courtier qui redistribue la tâche.
    export_project_pdf.apply(args=(project_id,), task_id=task_id)

    events = (
        await session.execute(
            select(UsageEvent).where(col(UsageEvent.metric) == UsageMetric.EXPORTS_PDF)
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].idempotency_key == f"{UsageMetric.EXPORTS_PDF}:{task_id}"


# --- Déclassement : lecture seule, jamais suppression -------------------------------------------


async def test_a_downgraded_project_is_read_only_and_still_there(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """On bloque la création et l'écriture, **jamais** la lecture.

    Le chantier excédentaire reste lisible et exportable : c'est la situation la plus favorable au
    réabonnement, et la seule qui ne détruise pas la confiance.
    """
    premier = int(
        (await auth_client.post("/api/projects", json={"name": "Premier"})).json()["id"]
    )
    second = int((await auth_client.post("/api/projects", json={"name": "Second"})).json()["id"])
    organization_id = int((await auth_client.get("/api/organizations")).json()[0]["id"])

    # L'essai ouvert par le second chantier est arrivé à son terme.
    abonnement = (
        await session.execute(
            select(Subscription).where(col(Subscription.organization_id) == organization_id)
        )
    ).scalars().one()
    abonnement.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()

    etat = await auth_client.get(f"/api/organizations/{organization_id}/subscription")
    assert etat.status_code == 200, etat.text
    assert etat.json()["archived_project_ids"] == [premier]

    # Toujours là, toujours lisible.
    assert (await auth_client.get(f"/api/projects/{premier}")).status_code == 200
    assert (
        await session.execute(select(Project).where(col(Project.id) == premier))
    ).scalar_one() is not None

    refuse = await auth_client.patch(f"/api/projects/{premier}", json={"name": "Renommé"})
    assert refuse.status_code == 403, refuse.text
    assert refuse.json()["code"] == "project_archived"

    # Le chantier conservé est celui sur lequel on travaillait : il reste modifiable.
    assert (
        await auth_client.patch(f"/api/projects/{second}", json={"name": "Toujours ouvert"})
    ).status_code == 200


async def test_the_batch_route_is_no_back_door_into_an_archived_project(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """La route de lot passe par le même point de passage que les écritures unitaires.

    C'est la route la plus dense en opérations : la laisser en dehors du contrôle rendrait le
    déclassement décoratif.
    """
    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    projet = (
        await session.execute(select(Project).where(col(Project.id) == project_id))
    ).scalar_one()
    projet.archived_at = datetime.now(UTC)
    await session.commit()

    lot = await auth_client.post(
        f"/api/projects/{project_id}/batch",
        json={"operations": [{"op": "create_room", "room": {"name": "Salon", "polygon": CARRE}}]},
    )
    assert lot.status_code == 403, lot.text
    assert lot.json()["code"] == "project_archived"


# --- Ouverture explicite de l'essai -------------------------------------------------------------


async def test_the_trial_can_be_opened_explicitly_and_only_once(
    auth_client: AsyncClient,
) -> None:
    """C'est le bouton « retirer le filigrane » : il ouvre l'essai, sans carte.

    Rejoué, il ne rend pas une erreur mais l'état courant : l'utilisateur voulait connaître ses
    droits, il les obtient, et le frontend n'a aucun cas particulier à traiter.
    """
    organization_id = await _organization_id(auth_client)

    ouverture = await auth_client.post(
        f"/api/organizations/{organization_id}/subscription/trial"
    )
    assert ouverture.status_code == 201, ouverture.text
    assert ouverture.json()["plan"]["code"] == PLAN_ARTISAN
    assert ouverture.json()["plan"]["features"][FEATURE_EXPORTS_WITHOUT_WATERMARK] is True
    assert ouverture.json()["trial_available"] is False

    rejeu = await auth_client.post(f"/api/organizations/{organization_id}/subscription/trial")
    assert rejeu.status_code == 201, rejeu.text
    assert rejeu.json()["subscription"]["id"] == ouverture.json()["subscription"]["id"]


async def test_the_product_metrics_are_written_from_the_very_first_gestures(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """L'activation et le délai jusqu'au premier devis sont posés **maintenant**.

    Aucun des deux ne plafonne quoi que ce soit et aucun n'est facturé. Ils sont écrits parce que
    leur historique ne se reconstitue pas : le jour où il faudra arbitrer la grille ou corriger
    l'accueil, la question sera « combien de comptes de mars ont dessiné une pièce », et personne
    ne pourra y répondre après coup.

    Chacun est posé **une seule fois par entreprise** : deux pièces ne font pas deux activations.
    """
    project_id = int(
        (await auth_client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    for nom in ("Salon", "Cuisine"):
        await auth_client.post(
            f"/api/projects/{project_id}/rooms", json={"name": nom, "polygon": CARRE}
        )
    await auth_client.post(
        f"/api/projects/{project_id}/quotes", json={"client_name": "Madame Dupont"}
    )
    # Second devis : le délai « jusqu'au premier » ne doit pas être réécrit.
    await auth_client.post(
        f"/api/projects/{project_id}/quotes", json={"client_name": "Monsieur Martin"}
    )

    events = (await session.execute(select(UsageEvent))).scalars().all()
    par_metrique: dict[str, list[UsageEvent]] = {}
    for event in events:
        par_metrique.setdefault(event.metric, []).append(event)

    assert len(par_metrique[UsageMetric.ACTIVATION]) == 1
    assert len(par_metrique[UsageMetric.TIME_TO_FIRST_QUOTE]) == 1
    # La quantité porte le délai en secondes : un événement unique suffit donc à le lire.
    assert par_metrique[UsageMetric.TIME_TO_FIRST_QUOTE][0].quantity >= 0


async def test_the_account_page_reports_usage_against_the_limits(
    auth_client: AsyncClient,
) -> None:
    """Une seule route sert la page compte et les boîtes de dialogue des murs de paiement."""
    organization_id = await _organization_id(auth_client)

    etat = (await auth_client.get(f"/api/organizations/{organization_id}/subscription")).json()
    par_metrique = {ligne["metric"]: ligne for ligne in etat["usage"]}

    assert set(par_metrique) == {str(metric) for metric in UsageMetric}
    assert par_metrique[UsageMetric.PROJECTS_ACTIVE] == {
        "metric": UsageMetric.PROJECTS_ACTIVE,
        "value": 1,
        "limit": 1,
    }
    # Les métriques produit sont rendues, sans plafond : elles servent à comprendre, pas à bloquer.
    assert par_metrique[UsageMetric.ACTIVATION]["limit"] is None
    assert etat["plan"]["code"] == PLAN_DISCOVERY


# --- La grille ne vend que ce qui existe (spec §10, amendement A14) -------------------------------


def test_every_published_feature_names_the_guard_that_refuses_it() -> None:
    """Une ligne de la page tarifs sans garde est une promesse de vente sans contrepartie.

    Six clés — marque blanche, signature « bon pour accord », variantes chiffrées, SSO,
    statistiques par agence, API — étaient affichées « ✓ » en face de 79 € sans exister nulle part.
    Ce test est ce qui empêche la septième : ajouter une fonctionnalité à la grille impose de dire
    où elle est refusée, et retirer la garde sans retirer la ligne échoue ici.

    Il ne remplace pas les tests de comportement plus bas — une entrée dans la table ne prouve pas
    que la garde fonctionne — il empêche l'**oubli**, qui est ce qui s'est réellement produit.
    """
    assert set(FEATURE_LABELS) == set(ENFORCEMENT_POINTS)
    assert set(LIMIT_LABELS) == set(LIMIT_ENFORCEMENT_POINTS)


def test_the_grid_carries_no_feature_the_product_does_not_have() -> None:
    """Les six clés retirées ne doivent revenir ni dans les libellés, ni dans un palier semé.

    Nommées une par une, et non déduites : le jour où l'une est **construite**, ce test doit être
    modifié à la main, et cette modification est exactement la relecture qu'on veut provoquer.
    """
    jamais_construites = {
        "white_label",
        "client_signature",
        "priced_variants",
        "sso",
        "agency_stats",
        "api",
    }
    assert jamais_construites.isdisjoint(FEATURE_LABELS)
    for entree in PLAN_GRID:
        assert jamais_construites.isdisjoint(entree["features"]), entree["code"]


async def test_the_public_pricing_page_never_serves_an_unimplemented_feature(
    client: AsyncClient,
) -> None:
    """La sonde de la revue, retournée : ce que la page publique annonce doit exister.

    Elle interroge la route réelle et non le module de semis — c'est la réponse HTTP que lit un
    prospect, et c'est elle qui affichait un « ✓ » mensonger.
    """
    body = (await client.get("/api/plans")).json()

    assert set(body["feature_labels"]) == set(ENFORCEMENT_POINTS)
    for plan in body["plans"]:
        annoncees = {cle for cle, accordee in plan["features"].items() if accordee}
        assert annoncees <= set(ENFORCEMENT_POINTS), plan["code"]


async def test_the_compliance_check_is_walled_and_the_first_call_opens_the_trial(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """`compliance_check` était annoncé bloqué au palier gratuit et répondait 200.

    Deux moitiés, et les deux comptent : sur un compte neuf le contrôle **aboutit** en ouvrant
    l'essai — refuser sèchement le premier geste monétisé serait le contraire de la stratégie §4 —
    et une fois l'essai consommé il est refusé, avec le palier à prendre.
    """
    project_id, organization_id = await _project_and_organization(auth_client)

    premier = await auth_client.get(f"/api/projects/{project_id}/inspection")
    assert premier.status_code == 200, premier.text

    abonnements = (
        await session.execute(
            select(Subscription).where(col(Subscription.organization_id) == organization_id)
        )
    ).scalars().all()
    assert [abonnement.plan_code for abonnement in abonnements] == [PLAN_ARTISAN]


async def test_once_the_trial_is_spent_the_three_analyses_are_refused_by_their_own_plan(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Les trois moteurs, et le palier que chacun demande.

    Le contrôle de conformité et le calepinage arrivent avec Artisan, l'aménagement automatique
    avec Entreprise : la grille §4 les place à deux endroits différents, et le corps du refus doit
    le dire — c'est lui que le frontend lit pour proposer le bon palier.
    """
    project_id, organization_id = await _project_and_organization(auth_client)
    room_id = int(
        (
            await auth_client.post(
                f"/api/projects/{project_id}/rooms",
                json={"name": "Salle d'eau", "polygon": CARRE},
            )
        ).json()["id"]
    )
    await _consume_trial(session, organization_id)

    inspection = await auth_client.get(f"/api/projects/{project_id}/inspection")
    assert inspection.status_code == 402, inspection.text
    assert inspection.json()["feature"] == "compliance_check"
    assert inspection.json()["required_plan"] == PLAN_ARTISAN

    calepinage = await auth_client.get(f"/api/projects/{project_id}/laying-plan")
    assert calepinage.status_code == 402, calepinage.text
    assert calepinage.json()["feature"] == "tiling_waste"
    assert calepinage.json()["required_plan"] == PLAN_ARTISAN

    amenagement = await auth_client.post(f"/api/rooms/{room_id}/layouts", json={"count": 2})
    assert amenagement.status_code == 402, amenagement.text
    assert amenagement.json()["feature"] == "auto_layout"
    # L'essai ne suffit pas : l'aménagement demande un palier au-dessus de celui qu'il offre.
    assert amenagement.json()["required_plan"] == PLAN_BUSINESS


async def test_the_free_dossier_arrives_without_its_dimensioned_elevations(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """`dimensioned_elevations` s'applique au **contenu**, jamais au téléchargement.

    §4 place l'export filigrané dans ce que le palier gratuit inclut et les élévations cotées dans
    ce qu'il bloque : deux lignes distinctes, dont une seule était appliquée. Le fichier continue de
    se télécharger — c'est la règle de A11 et elle n'est pas négociable — mais il n'a plus que la
    page de garde et le plan coté de la pièce.
    """
    project_id, organization_id = await _project_and_organization(auth_client)
    await _consume_trial(session, organization_id)

    gratuit = await auth_client.get(f"/api/projects/{project_id}/exports/pdf/direct")
    assert gratuit.status_code == 200, gratuit.text
    assert gratuit.headers["content-type"] == "application/pdf"
    # Page de garde + plan coté de l'unique pièce, et rien d'autre. Quatre murs, donc quatre
    # planches d'élévation en moins.
    assert _page_count(gratuit.content) == 2

    abonnement = (
        await session.execute(
            select(Subscription).where(col(Subscription.organization_id) == organization_id)
        )
    ).scalars().one()
    abonnement.status = SubscriptionStatus.ACTIVE
    abonnement.trial_ends_at = None
    abonnement.current_period_end = datetime.now(UTC) + timedelta(days=20)
    await session.commit()

    abonne = await auth_client.get(f"/api/projects/{project_id}/exports/pdf/direct")
    assert _page_count(abonne.content) == 6


async def test_inviting_a_second_person_is_refused_by_the_feature_then_by_the_seats(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """Deux refus distincts, et c'est ce qui rend la proposition commerciale juste.

    402 quand le palier n'ouvre pas le travail à plusieurs, 429 quand il l'ouvre et que les places
    sont prises. Les confondre proposerait un changement de palier à une entreprise qui n'a besoin
    que d'un siège de plus.
    """
    _project_id, organization_id = await _project_and_organization(auth_client)
    await _consume_trial(session, organization_id)

    refus = await auth_client.post(
        f"/api/organizations/{organization_id}/invitations",
        json={"email": "compagnon@exemple.fr", "role": "editor"},
    )
    assert refus.status_code == 402, refus.text
    assert refus.json()["feature"] == "multi_seat"
    assert refus.json()["required_plan"] == PLAN_BUSINESS

    # Palier Entreprise, mais ramené à un seul siège par une négociation : la fonctionnalité est
    # ouverte, le plafond ne l'est pas.
    await subscribe(session, organization_id, PLAN_BUSINESS)
    await _set_limit(session, PLAN_BUSINESS, LIMIT_SEATS, 1)

    plafond = await auth_client.post(
        f"/api/organizations/{organization_id}/invitations",
        json={"email": "compagnon@exemple.fr", "role": "editor"},
    )
    assert plafond.status_code == 429, plafond.text
    assert plafond.json()["code"] == "quota_exceeded"
    assert plafond.json()["metric"] == LIMIT_SEATS
    assert plafond.json()["current"] == 1

    await _set_limit(session, PLAN_BUSINESS, LIMIT_SEATS, 15)
    accepte = await auth_client.post(
        f"/api/organizations/{organization_id}/invitations",
        json={"email": "compagnon@exemple.fr", "role": "editor"},
    )
    assert accepte.status_code == 201, accepte.text


async def test_only_an_admin_may_burn_the_single_trial_of_the_company(
    auth_client: AsyncClient, session: AsyncSession
) -> None:
    """L'essai est unique et non renouvelable : un `editor` ne le grille pas d'un clic.

    Le rôle `editor` est celui qu'on obtient en rejoignant une entreprise. Laisser ce bouton à sa
    portée revenait à laisser n'importe quel salarié consommer, un lundi matin, ce que le patron
    gardait pour le chantier de la semaine suivante.
    """
    _project_id, organization_id = await _project_and_organization(auth_client)
    await subscribe(session, organization_id, PLAN_BUSINESS)

    async with logged_in("compagnon@exemple.fr") as compagnon:
        invitation = await auth_client.post(
            f"/api/organizations/{organization_id}/invitations",
            json={"email": "compagnon@exemple.fr", "role": "editor"},
        )
        assert invitation.status_code == 201, invitation.text
        await compagnon.post(
            "/api/invitations/accept", json={"token": invitation.json()["token"]}
        )

        refus = await compagnon.post(
            f"/api/organizations/{organization_id}/subscription/trial"
        )
        assert refus.status_code == 403, refus.text

    # Le propriétaire, lui, l'ouvre — la route n'est pas cassée, elle est cloisonnée.
    assert (
        await auth_client.post(f"/api/organizations/{organization_id}/subscription/trial")
    ).status_code == 201


async def _project_and_organization(client: AsyncClient) -> tuple[int, int]:
    """Un chantier avec une pièce, et l'organisation personnelle qui le porte."""
    project_id = int(
        (await client.post("/api/projects", json={"name": "Chantier"})).json()["id"]
    )
    created = await client.post(
        f"/api/projects/{project_id}/rooms", json={"name": "Salon", "polygon": CARRE}
    )
    assert created.status_code == 201, created.text
    organization_id = int((await client.get("/api/organizations")).json()[0]["id"])
    return project_id, organization_id
