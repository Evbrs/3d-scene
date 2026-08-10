"""Maîtrise du coût serveur : les routes qui calculent portent toutes un plafond de débit.

Deux défauts sont fermés ici, et le second explique pourquoi ce fichier existe.

**Le garde-fou n'était branché nulle part.** `RateLimited` était écrit, testé, et absent de toutes
les routes du dépôt : `Depends(RateLimited(...))` n'apparaissait pas une seule fois. Les seules
routes plafonnées étaient la connexion, l'inscription et l'oubli de mot de passe — pas une seule
de celles qui consomment du processeur. Mesuré sur une pièce vide de 5 x 4 m,
`POST /rooms/{id}/layouts` coûte 132 ms pour une salle de bain et **633 ms** pour une cuisine
accessible en cinq variantes : une boucle sur cette seule route tenait les quatre workers occupés
depuis un compte gratuit.

**Et le test qui le couvrait ne protégeait rien.** Il montait une `FastAPI()` jetable dans son
propre corps, y posait la dépendance, et vérifiait qu'elle répondait 429. Il serait resté vert
quel que soit l'état de l'API réelle — et il l'est resté. Les tests de ce fichier interrogent donc
**l'application publiée**, et le garde-fou de complétude ci-dessous ne recopie aucune liste de
routes : il parcourt les routes du service et cherche lui-même celles qui calculent.
"""

import ast
import inspect
import textwrap
from typing import Any

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient

from app.core.rate_limit import COSTLY_QUOTAS, RateLimited, rate_limiter
from app.main import app as fastapi_app
from tests.test_socle import FakeRedis

CARRE: list[list[float]] = [[0, 0], [500, 0], [500, 400], [0, 400]]

# Modules dont l'appel *est* le coût : géométrie, moteur d'intelligence, rendus de documents et
# tâches de fond. Une route qui atteint l'un d'eux fait travailler un processeur, et c'est la seule
# définition de « coûteuse » qui ne se recopie pas à la main.
COMPUTATION_MODULES = (
    "app.geometry",
    "app.intelligence",
    "app.services.export_pdf",
    "app.services.facturx",
    "app.tasks",
)

# Modules traversés à la recherche de ces appels. Le calcul n'est presque jamais fait par la route
# elle-même : `read_takeoff` appelle `compute_takeoff`, qui appelle `scene_for_project`, qui appelle
# `build_scene_graph`. Sans la traversée, aucune des trois ne serait vue comme coûteuse.
TRAVERSED_MODULES = ("app.api.", "app.services.")

# Routes coûteuses que la traversée doit continuer à trouver. Ce n'est **pas** la liste de ce qui
# est protégé — c'est le garde-fou du garde-fou : si un remaniement casse la traversée, elle ne
# trouve plus rien, et un test qui ne trouve rien passe pour toujours.
KNOWN_COSTLY = {
    ("GET", "/api/projects/{project_id}/scene"),
    ("GET", "/api/projects/{project_id}/takeoff"),
    ("GET", "/api/projects/{project_id}/inspection"),
    ("GET", "/api/projects/{project_id}/laying-plan"),
    ("POST", "/api/rooms/{room_id}/layouts"),
    ("GET", "/api/projects/{project_id}/exports/pdf/direct"),
    ("POST", "/api/projects/{project_id}/quotes"),
    ("GET", "/api/quotes/{quote_id}/pdf"),
    ("GET", "/api/public/views/{token}"),
}


def api_routes() -> list[APIRoute]:
    """Toutes les routes de l'application, routeurs inclus rendus à plat.

    `app.routes` ne contient plus les routes des routeurs inclus depuis FastAPI 0.11x : il contient
    des enveloppes qui gardent une référence sur le routeur d'origine. Lire `app.routes` seul
    rendrait une liste vide, et un parcours qui ne trouve aucune route ne vérifie rien.
    """

    def descend(node: Any) -> list[APIRoute]:
        routes = getattr(node, "routes", None)
        if routes is None:
            included = getattr(node, "original_router", None)
            routes = getattr(included, "routes", []) if included is not None else []
        found: list[APIRoute] = []
        for route in routes:
            if isinstance(route, APIRoute):
                found.append(route)
            else:
                found.extend(descend(route))
        return found

    return descend(fastapi_app)


def _module_of(candidate: Any) -> str:
    """Module d'origine d'un objet appelable, `""` pour tout le reste.

    Les constantes sont ignorées volontairement : une durée ou un libellé importé d'un module de
    calcul ne fait rien calculer. Le repli sur le type ne sert qu'aux appelables qui ne portent pas
    leur module en propre — la tâche Celery en est une.
    """
    if not callable(candidate):
        return ""
    declared = getattr(candidate, "__module__", "") or ""
    return declared if declared.startswith("app.") else type(candidate).__module__


def _referenced_globals(function: Any) -> list[Any]:
    """Objets du module que `function` nomme dans son corps.

    On lit les noms et non les seuls appels : `run_in_threadpool(build_scene_graph, ...)` passe la
    fonction coûteuse en **argument**, et un parcours qui ne regarderait que les `ast.Call`
    laisserait passer exactement les routes qui déportent leur calcul dans un fil — toutes.
    """
    try:
        source = textwrap.dedent(inspect.getsource(function))
    except (OSError, TypeError):
        return []
    namespace = getattr(function, "__globals__", {})
    return [
        namespace[node.id]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Name) and node.id in namespace
    ]


def computation_reached_by(endpoint: Any) -> str | None:
    """Nom du premier calcul atteignable depuis `endpoint`, `None` s'il n'en atteint aucun."""
    seen: set[int] = set()
    pending = [endpoint]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for candidate in _referenced_globals(current):
            module = _module_of(candidate)
            if module.startswith(COMPUTATION_MODULES):
                return f"{module}.{getattr(candidate, '__name__', candidate)}"
            if module.startswith(TRAVERSED_MODULES) and id(candidate) not in seen:
                pending.append(candidate)
    return None


def rate_limit_scopes(route: APIRoute) -> list[str]:
    return [
        dependency.call.scope
        for dependency in route.dependant.dependencies
        if isinstance(dependency.call, RateLimited)
    ]


def costly_routes() -> list[tuple[APIRoute, str]]:
    """Chaque route qui fait travailler un processeur, avec le calcul qu'elle atteint."""
    found = []
    for route in api_routes():
        computation = computation_reached_by(route.endpoint)
        if computation is not None:
            found.append((route, computation))
    return found


def label(route: APIRoute) -> tuple[str, str]:
    return sorted(route.methods or set())[0], route.path


# --- Garde-fou de complétude ------------------------------------------------------------------


def test_every_route_that_computes_carries_a_rate_limit() -> None:
    """Une route coûteuse qui perd son plafond fait échouer la suite, pas la production.

    Le parcours part des routes que le service publie et remonte leurs appels jusqu'à la géométrie,
    au moteur d'intelligence et aux rendus de documents. Rien n'est recopié : une route ajoutée
    demain sur le scene graph apparaît ici toute seule.
    """
    unprotected = sorted(
        f"{label(route)[0]} {label(route)[1]} (atteint {computation})"
        for route, computation in costly_routes()
        if not rate_limit_scopes(route)
    )

    assert unprotected == [], (
        "routes qui consomment du processeur sans plafond de débit — poser "
        "`dependencies=[Depends(costly(\"<portée>\"))]` et calibrer la portée dans "
        f"`COSTLY_QUOTAS` : {unprotected}"
    )


def test_the_traversal_still_finds_the_routes_it_is_supposed_to_find() -> None:
    """Le garde-fou du garde-fou : un parcours cassé ne trouve rien, et ne prouve donc rien.

    C'est la faiblesse exacte du test que ce fichier remplace. Si `computation_reached_by` cesse de
    voir les appels — renommage, indirection, module déplacé — l'assertion ci-dessus deviendrait
    verte pour la pire des raisons.
    """
    found = {label(route) for route, _ in costly_routes()}

    assert found >= KNOWN_COSTLY, (
        f"le parcours ne voit plus ces routes coûteuses : {sorted(KNOWN_COSTLY - found)}"
    )


def test_no_calibrated_quota_is_left_unused() -> None:
    """Un plafond calibré mais branché sur aucune route est un plafond qui n'existe pas.

    C'est la forme silencieuse du défaut corrigé ici : `RateLimited` était écrit et testé, et
    aucune route ne s'en servait.
    """
    used = {scope for route in api_routes() for scope in rate_limit_scopes(route)}

    assert set(COSTLY_QUOTAS) == used, (
        f"portées calibrées sans route : {sorted(set(COSTLY_QUOTAS) - used)} ; "
        f"portées posées sans calibrage : {sorted(used - set(COSTLY_QUOTAS))}"
    )


# --- Le plafond appliqué par l'application réelle ----------------------------------------------


async def _project_with_a_room(client: AsyncClient) -> tuple[int, int]:
    project = (await client.post("/api/projects", json={"name": "Coût"})).json()
    room = await client.post(
        f"/api/projects/{project['id']}/rooms", json={"name": "Salon", "polygon": CARRE}
    )
    assert room.status_code == 201, room.text
    return int(project["id"]), int(room.json()["id"])


async def test_two_routes_of_the_same_cost_share_one_bucket(auth_client: AsyncClient) -> None:
    """Alterner deux formes du même travail ne doit pas doubler le budget.

    Les deux chemins d'export produisent le même PDF, l'un dans la requête et l'autre sur un worker
    Celery. Leur donner un compteur chacun rendrait le plafond contournable par simple alternance —
    et c'est précisément ce qu'un plafond posé « par route » sans réflexion produit.
    """
    project_id, _room_id = await _project_with_a_room(auth_client)
    quota = COSTLY_QUOTAS["export_pdf"]

    codes = []
    for index in range(quota.max_events + 1):
        if index % 2:
            response = await auth_client.post(f"/api/projects/{project_id}/exports/pdf")
        else:
            response = await auth_client.get(f"/api/projects/{project_id}/exports/pdf/direct")
        codes.append(response.status_code)

    # Les codes attendus sont énumérés et non simplement « pas 429 » : un mur de paiement posé
    # demain sur l'export répondrait 402 sans jamais rien produire, et l'alternance ne prouverait
    # plus rien du partage de seau.
    assert set(codes[:-1]) <= {200, 202}, f"le plafond partagé tombe trop tôt : {codes}"
    assert codes[-1] == 429, (
        "alterner les deux chemins d'export double le budget : ils ne partagent pas leur seau"
    )


async def test_a_saturated_route_does_not_close_the_others(auth_client: AsyncClient) -> None:
    """Les portées séparent les compteurs : épuiser le métré ne ferme pas la lecture de la scène.

    Un plafond unique pour toute l'API rendrait une route capable de fermer les autres — l'artisan
    qui a exporté son métré une fois de trop ne verrait plus son plan.
    """
    project_id, _room_id = await _project_with_a_room(auth_client)

    codes = [
        (await auth_client.get(f"/api/projects/{project_id}/takeoff")).status_code
        for _ in range(COSTLY_QUOTAS["takeoff"].max_events + 1)
    ]
    assert codes[:-1] == [200] * COSTLY_QUOTAS["takeoff"].max_events, codes
    assert codes[-1] == 429, codes

    assert (await auth_client.get(f"/api/projects/{project_id}/scene")).status_code == 200


# --- L'endpoint public compte dans le compteur partagé, pas dans un processus -------------------


@pytest.fixture
def shared_counter(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Branche le compteur partagé de l'application sur un Redis de test.

    `_script` est remis à zéro en même temps que `_client` : le script Lua est mémorisé à la
    première utilisation, et le laisser en place ferait fuiter le faux Redis dans les tests
    suivants.
    """
    fake = FakeRedis()
    monkeypatch.setattr(rate_limiter, "_client", lambda: fake)
    monkeypatch.setattr(rate_limiter, "_script", None)
    return fake


async def _shared_token(client: AsyncClient) -> str:
    project_id, _room_id = await _project_with_a_room(client)
    created = await client.post(
        f"/api/projects/{project_id}/shared-views",
        json={"state": {"camera_preset": "face", "room_index": 0}},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["token"])


async def test_the_public_view_counts_in_the_shared_counter(
    auth_client: AsyncClient, client: AsyncClient, shared_counter: FakeRedis
) -> None:
    """La seule route sans authentification ne doit pas compter dans la mémoire d'un processus.

    C'était pourtant le cas : elle utilisait un `SlidingWindowRateLimiter`, que le module décrit
    lui-même comme « repli du compteur Redis, et rien d'autre ». Avec quatre workers en production,
    le plafond affiché de 60 en valait 240, et il repartait de zéro à chaque redémarrage — sur
    l'unique route qu'atteint quelqu'un qui n'a pas de compte, et qui déclenche un calcul de scène
    complet à chaque appel.
    """
    token = await _shared_token(auth_client)

    for _ in range(3):
        assert (await client.get(f"/api/public/views/{token}")).status_code == 200

    counted = {key: len(events) for key, events in shared_counter.windows.items()}
    assert counted.get("rl:public_view:127.0.0.1") == 3, (
        f"la vue publique ne compte pas dans le compteur partagé : {counted}"
    )


async def test_the_public_view_falls_back_more_strictly_when_redis_is_down(
    auth_client: AsyncClient, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une panne du compteur partagé resserre le plafond au lieu de le multiplier par quatre.

    Chaque processus ne voit alors qu'une fraction du trafic : lui laisser le quota complet le
    multiplierait par le nombre de workers, ce que faisait exactement le compteur mémoire posé
    en dur sur cette route.
    """
    token = await _shared_token(auth_client)
    monkeypatch.setattr(rate_limiter, "_client", lambda: FakeRedis(fail=True))
    monkeypatch.setattr(rate_limiter, "_script", None)

    quota = COSTLY_QUOTAS["public_view"]
    allowed = 0
    for _ in range(quota.max_events):
        if (await client.get(f"/api/public/views/{token}")).status_code == 200:
            allowed += 1

    assert allowed == quota.tightened().max_events < quota.max_events, (
        f"le repli laisse passer {allowed} appels au lieu de {quota.tightened().max_events}"
    )


async def test_the_public_view_answers_429_with_a_retry_after(
    auth_client: AsyncClient, client: AsyncClient, shared_counter: FakeRedis
) -> None:
    """Un 429 sans `Retry-After` laisse le client retenter à l'aveugle, donc trop tôt.

    La vérification manuelle qui plafonnait cette route levait une 429 nue : elle aggravait la
    charge qu'elle prétendait contenir.
    """
    token = await _shared_token(auth_client)
    quota = COSTLY_QUOTAS["public_view"]

    responses = [
        await client.get(f"/api/public/views/{token}") for _ in range(quota.max_events + 1)
    ]

    assert [response.status_code for response in responses[:-1]] == [200] * quota.max_events
    assert responses[-1].status_code == 429
    assert int(responses[-1].headers["Retry-After"]) >= 1
