# Habi Search multi-tour

## Architecture et compatibilité

Les routes historiques `/api/v1/search/text` et `/api/v1/search/audio` gardent
leur contrat : `query`/`transcription`, `PropertySearchCriteria`, libellé
`propertyType` et loyers Decimal sérialisés en chaînes. Leur prompt est inchangé.

Les nouvelles routes utilisent `PropertyFilterRequest` avec exactement les 16
champs Spring Boot, tous optionnels, sérialisés même lorsqu'ils valent `null`.
Les loyers sont des entiers JSON. Les types, bornes, dates calendaires ISO et
contraintes de colocation sont validés. Aucun pays ou ville n'est ajouté par défaut.

```text
texte ────────────────────┐
audio → contrôles → STT ──┴→ ConversationService
                            → SearchParserService.parse_update
                              → Groq, puis Gemini si erreur ou JSON invalide
                              → CriteriaUpdate validé par Pydantic
                            → PropertyTypeResolver → catalogue Spring Boot / cache
                            → CriteriaMerger → validation du résultat
                            → ConversationStore → réponse au frontend
```

Le STT existant reste Groq puis Hugging Face. Les providers, client HTTP partagé,
configuration et gestion des erreurs HTTP sont réutilisés. Seul le catalogue
public des types est lu dans Spring Boot. Aucun accès base de données n'est ajouté.

## API

`POST /api/v1/search/conversation`, JSON :

```json
{"conversationId": null, "message": "Je cherche à Mermoz"}
```

Omettre l'identifiant ou envoyer `null` crée une conversation avec un identifiant
aléatoire. Réutiliser l'identifiant retourné pour tous les tours suivants.

`POST /api/v1/search/conversation/audio`, multipart :

- `file` : mêmes MIME et limites que la route audio historique ;
- `conversationId` : optionnel, identifiant retourné précédemment.

La transcription brute est retournée dans `transcription`. Seul le message utilisé
par le moteur est normalisé (espaces aux extrémités). Le STT et les contrôles audio
sont partagés avec l'ancienne route ; toute la logique de conversation est commune.

Exemple de réponse :

```json
{
  "conversationId": "identifiant-retourne-par-le-service",
  "status": "SEARCH_READY",
  "message": null,
  "criteria": {
    "country": null,
    "city": null,
    "municipality": null,
    "neighborhood": "Mermoz",
    "typeId": null,
    "minRent": null,
    "maxRent": null,
    "rooms": null,
    "bedrooms": null,
    "bathrooms": null,
    "minArea": null,
    "maxArea": null,
    "furnished": null,
    "sharedHousingAllowed": null,
    "minSharedHousingCapacity": null,
    "availableBefore": null
  },
  "awaitingField": null,
  "unresolvedPropertyType": null,
  "transcription": null
}
```

Les réponses conversationnelles utilisent HTTP 200 et les statuts :

| Statut | Sens |
| --- | --- |
| `SEARCH_READY` | Critères exploitables ; ne garantit aucun résultat immobilier |
| `NEEDS_CLARIFICATION` | Une information ambiguë attend une réponse ou un type attend sa résolution |
| `INVALID_INPUT` | Aucun critère exploitable, transcription vide/trop longue, ou conversation inconnue/expirée |
| `ERROR` | Extraction/fusion invalide, échec STT ou catalogue indisponible ; critères précédents conservés |

Un identifiant inconnu/expiré n'est jamais recréé silencieusement. Le client doit
recommencer sans identifiant. Les identifiants acceptent 1–128 caractères ASCII
alphanumériques, tiret et underscore. Une requête mal formée (message vide,
identifiant de syntaxe invalide, champs inconnus) garde l'erreur HTTP 422 existante
`{"code":"INVALID_REQUEST","message":"..."}`. Les erreurs d'upload conservent
HTTP 400/413/415 ; les erreurs internes inattendues utilisent le handler 500 existant.
Sur erreur, le frontend doit conserver sa recherche courante et ne pas déclencher
automatiquement une nouvelle recherche Spring Boot.

## État, opérations et clarification

`ConversationState` contient `conversationId`, `criteria`, `awaitingField`,
`createdAt`, `updatedAt` (UTC), ainsi que `clarificationMessage` pour conserver la
question et `unresolvedPropertyType` pour transporter un libellé non résolu.

Le LLM reçoit le message, les critères actuels et le champ attendu. Il renvoie
uniquement les modifications et, éventuellement, une clarification :

```json
{
  "operations": [
    {"field": "neighborhood", "operation": "SET", "value": "Ouakam"},
    {"field": "maxRent", "operation": "REMOVE"}
  ],
  "clarification": null,
  "unresolvedPropertyType": null
}
```

`SET` exige une valeur non nulle conforme au champ ; `REMOVE` le remet à `null`.
Les champs absents restent inchangés. Champs inconnus, opérations inconnues,
valeurs incompatibles, modifications dupliquées et modification/clarification du
même champ sont refusés. La fusion valide les bornes après application du lot
complet. Désactiver ou retirer la colocation efface sa capacité devenue inapplicable ;
fixer explicitement une capacité incompatible est refusé.

Le store ne reçoit le nouvel état qu'après validation. Un lot invalide ne modifie
aucun critère ; les timestamps peuvent être rafraîchis à la fin d'un tour en erreur
car il s'agit toujours d'une activité. Une exception inattendue annule la transaction.

Une recherche partielle telle que « Mermoz » est suffisante. Le prompt interdit
les questions systématiques sur les champs manquants. Pour « moins de 300 » sans
unité claire, le modèle peut retourner :

```json
{
  "operations": [],
  "clarification": {"field": "maxRent", "message": "Quel budget maximum souhaitez-vous indiquer ?"}
}
```

Le champ `awaitingField=maxRent` aide le LLM à interpréter « 350000 » au tour
suivant. Une opération sur ce champ efface l'attente ; un tour sur un autre champ
la conserve. Les modifications non ambiguës du même tour peuvent être appliquées.

## Types de biens

Les routes historiques conservent `propertyType` comme libellé. Pour les routes
conversationnelles, `PropertyTypeResolver` résout automatiquement le libellé
émis par le LLM, avant `CriteriaMerger`. Il utilise le même client HTTP que les
providers, mais ne dépend pas de leurs exceptions IA.

Le contrat a été vérifié dans le projet voisin `HabiTerra BackEnd` :
`PropertyTypeController.list()` retourne directement `List<PropertyTypeResponse>` ;
le record expose `Long id`, `String label`, `String description`. `libelle` est
un champ interne de l'entité, pas le nom exposé en JSON. Le GET est public :

```json
[{"id": 2, "label": "Appartement", "description": "Parfait pour petites familles"}]
```

Cet ID est un exemple observé localement, jamais une constante de résolution.
La réponse est validée par Pydantic avant mise en cache. Une enveloppe paginée,
un ID invalide, un libellé vide ou des IDs dupliqués sont refusés.

La comparaison applique `casefold`, une décomposition Unicode, la suppression
des accents et la normalisation des espaces. Après échec de cette comparaison,
un « s » final est toléré pour les libellés de plus de trois caractères : le
catalogue réel contient « Villas », auquel « villa » peut ainsi correspondre.
Pas de fuzzy matching, de liste de synonymes métier ni de mapping d'IDs.
Plusieurs correspondances, même après normalisation, entraînent une clarification.

Une correspondance unique devient `SET typeId=<id du catalogue>` ; le libellé
non résolu et l'attente sur le type sont effacés. Un SET numérique explicite est
aussi vérifié dans le catalogue. Les tours sans type et `REMOVE typeId` ne font
aucun appel HTTP. Le type déjà résolu reste conservé entre les tours.

Pour un type inconnu ou ambigu, `NEEDS_CLARIFICATION` pose une question naturelle
avec au maximum cinq libellés réels. Le `typeId` précédent reste intact ; les
autres opérations valides peuvent être appliquées. Le nouveau libellé reste
dans `unresolvedPropertyType` jusqu'à résolution ou suppression. Cette question
est prioritaire si le même tour contient une autre ambiguïté. Aucun ID technique
n'est présenté dans la question. `awaitingField=typeId` est seulement une donnée
du protocole, et le client affiche `message` à l'utilisateur.

Configuration :

| Variable | Défaut |
| --- | --- |
| `SPRING_BOOT_BASE_URL` | `http://localhost:8080` |
| `PROPERTY_TYPES_CACHE_TTL_SECONDS` | `300` |
| `PROPERTY_TYPES_TIMEOUT_SECONDS` | `5` |

Le cache mémoire est partagé par les conversations du processus. Il stocke
uniquement un catalogue validé, même vide, et expire selon une horloge monotone.
Un verrou évite les rafraîchissements simultanés réussis. Un cache valide est
utilisé sans contacter Spring Boot ; un cache expiré n'est pas utilisé en secours.
Timeout global, erreur réseau, non-2xx ou JSON/DTO invalide donnent `ERROR` sans
modifier les critères ni l'attente précédente. Aucun retry n'est ajouté.
Les logs indiquent cache, résolution, clarification et catégorie d'erreur,
sans contenu du backend ni texte utilisateur.

## Exemple de quatre tours

Le client conserve le même `conversationId` après le premier tour. Tous les champs
non mentionnés ci-dessous restent à `null` ; chaque réponse est `SEARCH_READY`.

| Tour | Message | Opérations | Critères après fusion |
| --- | --- | --- | --- |
| 1 | Je cherche à Mermoz | SET neighborhood=Mermoz | neighborhood=Mermoz |
| 2 | Deux chambres et maximum 350000 | SET bedrooms=2, SET maxRent=350000 | neighborhood=Mermoz, bedrooms=2, maxRent=350000 |
| 3 | Finalement plutôt à Ouakam | SET neighborhood=Ouakam | neighborhood=Ouakam, bedrooms=2, maxRent=350000 |
| 4 | Enlève la limite de prix | REMOVE maxRent | neighborhood=Ouakam, bedrooms=2, maxRent=null |

## Stockage et exploitation

`ConversationStore` fournit une transaction isolée ; `InMemoryConversationStore`
est sa seule implémentation. Les tours sont sérialisés par conversation et les
états copiés pour empêcher les mutations externes. Deux conversations peuvent
traiter leurs appels LLM simultanément. L'expiration est paresseuse, à chaque accès,
après `CONVERSATION_TTL_SECONDS` d'inactivité (1800 secondes par défaut). Les
conversations utilisées/en attente ne sont pas supprimées pendant un tour.

Utiliser **un seul worker/processus**. Plusieurs instances ne partagent pas les
conversations. Redémarrer le service les perd. L'abstraction permet ultérieurement
un store partagé avec les mêmes garanties transactionnelles. Aucune persistance
de recherche sauvegardée n'est réalisée ici.

Logs : création/retrouvaille/expiration, provider, nombre d'opérations, clarification
et catégorie d'erreur. Aucun message, audio, critère ou identifiant de conversation
n'est journalisé par le nouveau moteur.

## Vérification et démarrage

Tests sans nouvelle dépendance, avec `unittest` et le TestClient déjà disponible :

```powershell
.\.venv-runtime\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-runtime\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

La suite couvre le resolver, cache/expiration, erreurs HTTP, inconnus/ambiguïtés,
remplacement/suppression des types et non-régression conversationnelle : fusion,
clarification, rollback, isolation, validation, fallback, texte/voix, anciens
endpoints, lifespan, health et OpenAPI. Les providers LLM/STT et le transport HTTP
Spring Boot sont simulés dans ces tests unitaires. Le dossier des 21 tests de
l'étape précédente n'était plus présent ; les tests de non-régression ont été rétablis.

Un test manuel réel effectué le 7 octobre 2026 a lu le catalogue local (HTTP 200 :
Appartement=2, Studio=1, Villas=3) et utilisé le LLM configuré sur « Je cherche un
appartement à Mermoz ». Résultat vérifié : `SEARCH_READY`, `typeId=2`,
`neighborhood=Mermoz`, `unresolvedPropertyType=null`, `awaitingField=null`, les
autres critères à `null`. Ce contrôle ponctuel ne valide pas toutes les formulations
possibles ni la transcription d'un vrai audio.

À connecter : conservation de l'identifiant et affichage des clarifications dans
React Native, puis envoi de `criteria` à Spring Boot. Recherche, validation métier, sauvegarde et alertes
restent entièrement côté Spring Boot.
