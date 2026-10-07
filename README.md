# Habiterra IA

Petit service Python 3.12+ : transcription audio et extraction de critères immobiliers.
Spring Boot conserve toute la logique métier et l'accès aux biens. Aucun accès base de données.

## Démarrage

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Renseigner les clés et les modèles dans .env.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Documentation : http://127.0.0.1:8000/docs ; schéma : `/openapi.json`.
Le service démarre sans clés ; les appels IA nécessitent une configuration valide.

## Structure

```text
app/
  main.py                              # Démarrage, client HTTP partagé, logs
  api/routes/search.py                 # Les deux endpoints
  core/config.py, logging.py           # Configuration et logging
  schemas/search.py                    # DTO et validation des critères
  services/search_parser_service.py    # Extraction, validation, fallback
  services/transcription_service.py    # Transcription, fallback
  providers/                           # Quatre providers et un helper HTTP
  exceptions/                          # Exceptions et réponses d'erreur
```

## API

- `GET /health` → `{"status":"ok"}`, sans appel externe.
- `POST /api/v1/search/text` : `{"query":"Je cherche une villa à Dakar avec 3 chambres"}`
  → `{"query":"...","criteria":{...}}`.
- `POST /api/v1/search/audio` : multipart, champ `file`
  → `{"transcription":"...","criteria":{...}}`.

Les deux routes utilisent le même `SearchParserService`. Les critères sont exposés en
camelCase, avec toutes les valeurs absentes à `null`. `propertyType` reste du texte.
Les champs supplémentaires (dont `typeId`) sont refusés. Aucun SQL ou identifiant
n'est demandé aux modèles ni interprété ou exécuté par ce service.

`PropertySearchCriteria` valide les nombres positifs ou nuls, la capacité minimale
de colocation à 1, les bornes min/max, les dates et rejette NaN/Infinity.
Le texte est limité à 1–5000 caractères après suppression des espaces aux extrémités.
Les loyers sont des `Decimal`, sérialisés en chaînes décimales pour préserver la
précision (ex. `"700000"`), acceptables par un champ Java `BigDecimal`.

## Providers et fallback

- Audio : Groq → Hugging Face (modèle Distil-Whisper à configurer).
- Critères : Groq → Gemini ; le JSON est validé par Pydantic à chaque tentative.
- Une erreur réseau, timeout, erreur API ou configuration absente déclenche le fallback.
  Pour les critères, un JSON invalide ou non conforme le déclenche également.
- Aucun retry : chaque provider est appelé au plus une fois par étape.
- Une transcription vide ou peu pertinente ne déclenche pas le fallback.
  Un texte vide ou trop long est refusé par la route audio avec 422.

## Variables d'environnement

`.env.example` contient uniquement les noms, sans secrets. Les valeurs vides utilisent
les valeurs par défaut ; les variables système sont prioritaires sur `.env`.

| Variables | Valeur par défaut / usage |
| --- | --- |
| `APP_NAME`, `APP_ENV` | `Habiterra AI`, `development` |
| `GROQ_API_KEY` | Clé Groq |
| `GROQ_TRANSCRIPTION_MODEL` | `whisper-large-v3-turbo` |
| `GROQ_STRUCTURED_MODEL` | `llama-3.1-8b-instant` (voir disponibilité ci-dessous) |
| `HUGGINGFACE_API_KEY` | Token autorisant l'inférence |
| `HUGGINGFACE_TRANSCRIPTION_MODEL` | Obligatoire pour le fallback ; choisir un Distil-Whisper servi par votre compte |
| `HUGGINGFACE_TRANSCRIPTION_URL` | Optionnel : URL d'un endpoint HF dédié ; sinon route HF Inference |
| `GEMINI_API_KEY` | Clé Gemini |
| `GEMINI_MODEL` | `gemini-1.5-flash` (retiré, à remplacer pour un usage réel) |
| `HTTP_TIMEOUT_SECONDS` | 60 s au total par appel provider |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | 5 s pour établir la connexion |
| `HTTP_READ_TIMEOUT_SECONDS` | 30 s pour lecture, écriture et attente du pool |
| `MAX_AUDIO_SIZE_MB` | 20 MiB |
| `CORS_ORIGINS` | `[]`, désactivé ; sinon tableau JSON d'origines explicites |

## Erreurs et limites

Les erreurs ont la forme `{"code":"...","message":"..."}` : 400 audio vide,
413 fichier trop gros, 415 MIME refusé, 422 entrée invalide, 503 échec des deux
providers, 500 erreur interne. Aucun contenu brut de provider, secret ou stack trace
n'est retourné. Les logs indiquent route, provider, durée et fallback sans texte utilisateur.

Audio accepté : MPEG, WAV, x-WAV, MP4, WebM, OGG et FLAC. Lecture bornée à la taille
maximale + 1 octet, fichier fermé après lecture, nom généré avant envoi au provider.
Le MIME est déclaratif ; aucune conversion ni inspection du codec. FastAPI reçoit
le multipart avant la vérification de taille : configurer également une limite de
corps HTTP au niveau du proxy d'entrée pour borner les uploads sur disque.
Le service est destiné à un réseau interne, derrière la sécurité du backend/proxy.

Les modèles ne garantissent pas une extraction sémantique parfaite : Pydantic contrôle
la structure, pas la véracité. Aucune déduction géographique complexe n'est demandée.

### Disponibilité des modèles demandés

Les identifiants demandés restent configurables, sans substitution silencieuse :

- [Groq](https://console.groq.com/docs/deprecations) a retiré Llama 3.1 8B Instant
  le 16 août 2026 des offres free/developer ; les contrats entreprise concernés
  par l'exception doivent être vérifiés auprès du fournisseur.
- [Google](https://ai.google.dev/gemini-api/docs/changelog) a arrêté Gemini 1.5 Flash
  le 29 septembre 2025. Renseigner un modèle Gemini disponible pour utiliser le fallback.
- La présence d'un modèle Distil-Whisper sur le Hub ne garantit pas son hébergement
  dans [HF Inference](https://huggingface.co/docs/inference-providers/providers/hf-inference).
  Un endpoint dédié peut être nécessaire. Vérifier aussi les langues prises en charge
  par le modèle choisi, notamment le français.

## Conversation multi-tour

Les nouvelles routes `POST /api/v1/search/conversation` (JSON) et
`POST /api/v1/search/conversation/audio` (multipart) partagent un moteur à opérations
SET/REMOVE et un store temporaire en mémoire. Les anciennes routes gardent leur
contrat. Voir [le contrat conversationnel](docs/conversation.md) pour les critères
Spring Boot, les clarifications, les types de biens et les exemples.

Expiration configurable avec `CONVERSATION_TTL_SECONDS=1800`. Utiliser un seul
worker ; les conversations sont perdues au redémarrage.

Le type de bien conversationnel est résolu automatiquement via le catalogue
`GET /api/v1/property-types` de `SPRING_BOOT_BASE_URL` (défaut :
`http://localhost:8080`). Cache mémoire : `PROPERTY_TYPES_CACHE_TTL_SECONDS=300` ;
timeout catalogue : `PROPERTY_TYPES_TIMEOUT_SECONDS=5`. Aucun ID n'est hardcodé.

Tests automatisés sans dépendance supplémentaire :

```powershell
.\.venv-runtime\Scripts\python.exe -m unittest discover -s tests -v
```

Les providers et Spring Boot sont simulés dans les tests unitaires. Un contrôle
manuel réel LLM + catalogue a aussi validé « Je cherche un appartement à Mermoz »
le 7 octobre 2026 ; voir le contrat conversationnel pour les résultats et limites.
