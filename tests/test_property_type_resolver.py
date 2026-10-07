import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.exceptions.exceptions import AIProviderException, TranscriptionException
from app.main import app
from app.schemas.conversation import ConversationRequest, CriteriaOperation, CriteriaUpdate, PropertyFilterRequest
from app.services.conversation_service import ConversationService
from app.services.conversation_store import ConversationNotFound, InMemoryConversationStore
from app.services.criteria_merger import CriteriaMerger
from app.services.property_type_resolver import PropertyTypeResolver, PropertyTypesUnavailable
from app.services.search_parser_service import SearchParserService


# Deliberately arbitrary fixture IDs: no production mapping is derived from these.
CATALOG = [
    {"id": 47, "label": "Appartement", "description": "Un logement"},
    {"id": 83, "label": "Villas", "description": None},
    {"id": 129, "label": "Résidence", "description": None},
]


def update(*ops, label=None, clarification=None):
    return CriteriaUpdate.model_validate({
        "operations": [dict(field=field, operation=operation, value=value) for field, operation, value in ops],
        "unresolvedPropertyType": label, "clarification": clarification,
    })


class Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def extract(self, query, prompt):
        self.calls.append((query, prompt))
        if not self.responses:
            raise AIProviderException("no_fixture")
        value = self.responses.pop(0)
        return value.model_dump_json() if isinstance(value, CriteriaUpdate) else value


class ResolverFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.payload = CATALOG
        self.failure = None
        self.now = 0.0

        async def handler(request):
            self.requests.append(request)
            await asyncio.sleep(0)
            if isinstance(self.failure, Exception):
                raise self.failure
            if self.failure:
                return self.failure
            return httpx.Response(200, json=self.payload)

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        settings = Settings(_env_file=None, spring_boot_base_url="http://backend.test")
        self.resolver = PropertyTypeResolver(self.client, settings, clock=lambda: self.now)

    async def asyncTearDown(self):
        await self.client.aclose()

    def engine(self, *responses):
        self.provider = Provider(*responses)
        return ConversationService(SearchParserService(self.provider, Provider()), InMemoryConversationStore(), self.resolver)


class ResolverTests(ResolverFixture):
    async def test_exact_case_spaces_and_accents(self):
        for label, expected in [("appartement", 47), ("APPARTEMENT", 47), ("  appartement  ", 47), ("residence", 129)]:
            with self.subTest(label=label):
                result = await self.resolver.resolve_update(update(label=label))
                self.assertEqual(result.operations[0].value, expected)
                self.assertIsNone(result.unresolvedPropertyType)
                self.assertIsNone(result.clarification)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].url.path, "/api/v1/property-types")
        self.assertEqual(self.requests[0].method, "GET")

    async def test_simple_plural_and_exact_precedence(self):
        result = await self.resolver.resolve_update(update(label="villa"))
        self.assertEqual(result.operations[0].value, 83)
        self.payload = CATALOG + [{"id": 200, "label": "Villa"}]
        self.now = 301
        result = await self.resolver.resolve_update(update(label="Villa"))
        self.assertEqual(result.operations[0].value, 200)

    async def test_multiturn_replacement_and_preservation(self):
        engine = self.engine(update(("neighborhood", "SET", "Mermoz"), label="appartement"),
                             update(("bedrooms", "SET", 2)), update(label="villa"))
        first = await engine.process(ConversationRequest(message="Un appartement à Mermoz"))
        self.assertEqual(first.status, "SEARCH_READY")
        self.assertEqual(first.criteria.typeId, 47)
        second = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Deux chambres"))
        self.assertEqual(second.criteria.typeId, 47)
        third = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Finalement une villa"))
        self.assertEqual(third.criteria.typeId, 83)
        self.assertEqual(third.criteria.neighborhood, "Mermoz")
        self.assertEqual(third.criteria.bedrooms, 2)
        self.assertIsNone(third.unresolvedPropertyType)
        self.assertEqual(len(self.requests), 1)

    async def test_unknown_keeps_previous_type_and_clarifies_with_catalog_labels(self):
        engine = self.engine(update(label="appartement"), update(label="loft"), update(label="villa"))
        first = await engine.process(ConversationRequest(message="Appartement"))
        second = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Loft"))
        self.assertEqual(second.status, "NEEDS_CLARIFICATION")
        self.assertEqual(second.criteria.typeId, 47)
        self.assertEqual(second.awaitingField, "typeId")
        self.assertEqual(second.unresolvedPropertyType, "loft")
        self.assertIn("Appartement", second.message)
        self.assertIn("Villas", second.message)
        self.assertNotIn("47", second.message)
        self.assertNotIn("typeId", second.message)
        third = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Villa"))
        self.assertEqual(third.status, "SEARCH_READY")
        self.assertEqual(third.criteria.typeId, 83)
        self.assertIsNone(third.awaitingField)

    async def test_ambiguous_normalization_never_chooses_arbitrarily(self):
        self.payload = [{"id": 11, "label": "Résidence"}, {"id": 22, "label": "Residence"}]
        result = await self.resolver.resolve_update(update(label="résidence"))
        self.assertEqual(result.operations, [])
        self.assertIn("Plusieurs", result.clarification.message)
        self.assertIn("Résidence", result.clarification.message)
        self.assertIn("Residence", result.clarification.message)
        self.assertNotIn("11", result.clarification.message)

    async def test_remove_and_unrelated_turns_never_fetch(self):
        self.failure = httpx.ConnectError("offline")
        for changes in [update(("typeId", "REMOVE", None)), update(("bedrooms", "SET", 2))]:
            self.assertEqual(await self.resolver.resolve_update(changes), changes)
        self.assertEqual(self.requests, [])
        engine = self.engine(update(("typeId", "REMOVE", None)))
        async with engine.store.transaction(None) as state:
            state.criteria.typeId = 47
            state.unresolvedPropertyType = "loft"
            state.awaitingField = "typeId"
            cid = state.conversationId
        result = await engine.process(ConversationRequest(conversationId=cid, message="Peu importe le type"))
        self.assertIsNone(result.criteria.typeId)
        self.assertIsNone(result.unresolvedPropertyType)
        self.assertIsNone(result.awaitingField)
        self.assertEqual(result.status, "SEARCH_READY")
        self.assertEqual(self.requests, [])

    async def test_unavailable_without_cache_preserves_state(self):
        engine = self.engine(update(("neighborhood", "SET", "Ouakam"), label="villa"))
        async with engine.store.transaction(None) as state:
            state.criteria = PropertyFilterRequest(typeId=47, neighborhood="Mermoz")
            cid = state.conversationId
        self.failure = httpx.ConnectError("private backend detail")
        response = await engine.process(ConversationRequest(conversationId=cid, message="Villa à Ouakam"))
        self.assertEqual(response.status, "ERROR")
        self.assertEqual(response.criteria.typeId, 47)
        self.assertEqual(response.criteria.neighborhood, "Mermoz")
        self.assertIsNone(response.unresolvedPropertyType)
        self.assertNotIn("private", response.message)
        async with engine.store.transaction(cid) as state:
            self.assertEqual(state.criteria, response.criteria)

    async def test_valid_cache_works_during_outage(self):
        await self.resolver.resolve_update(update(label="appartement"))
        self.failure = httpx.ConnectError("offline")
        result = await self.resolver.resolve_update(update(label="villa"))
        self.assertEqual(result.operations[0].value, 83)
        self.assertEqual(len(self.requests), 1)

    async def test_cache_expiry_fetches_new_ids(self):
        await self.resolver.resolve_update(update(label="appartement"))
        self.now = 300
        self.payload = [{"id": 900, "label": "Appartement"}]
        result = await self.resolver.resolve_update(update(label="appartement"))
        self.assertEqual(result.operations[0].value, 900)
        self.assertEqual(len(self.requests), 2)

    async def test_expired_cache_not_used_on_failure(self):
        await self.resolver.resolve_update(update(label="appartement"))
        self.now = 300
        self.failure = httpx.ConnectError("offline")
        with self.assertRaises(PropertyTypesUnavailable):
            await self.resolver.resolve_update(update(label="villa"))

    async def test_invalid_backend_response(self):
        for response in [httpx.Response(503), httpx.Response(302), httpx.Response(200, text="not json"),
                         httpx.Response(200, json={"content": CATALOG}),
                         httpx.Response(200, json=[{"id": 1, "libelle": "Appartement"}]),
                         httpx.Response(200, json=[{"id": True, "label": "Appartement"}]),
                         httpx.Response(200, json=[{"id": -1, "label": "Appartement"}]),
                         httpx.Response(200, json=[{"id": 1, "label": " "}]),
                         httpx.Response(200, json=[{"id": 1, "label": "A"}, {"id": 1, "label": "B"}])]:
            with self.subTest(response=response):
                self.failure = response
                with self.assertRaises(PropertyTypesUnavailable):
                    await self.resolver.resolve_update(update(label="appartement"))
        self.assertIsNone(self.resolver._cache)

    async def test_timeout_is_controlled(self):
        self.failure = httpx.ReadTimeout("timeout")
        with self.assertRaises(PropertyTypesUnavailable):
            await self.resolver.resolve_update(update(label="appartement"))

    async def test_total_deadline_is_controlled(self):
        async def hanging(request):
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(hanging)) as client:
            settings = Settings(_env_file=None, property_types_timeout_seconds=0.01)
            resolver = PropertyTypeResolver(client, settings)
            with self.assertRaises(PropertyTypesUnavailable):
                await resolver.resolve_update(update(label="appartement"))

    async def test_concurrent_cache_refresh_only_once(self):
        results = await asyncio.gather(*[self.resolver.resolve_update(update(label="appartement")) for _ in range(5)])
        self.assertTrue(all(result.operations[0].value == 47 for result in results))
        self.assertEqual(len(self.requests), 1)

    async def test_empty_catalog_clarifies_and_is_cached(self):
        self.payload = []
        for _ in range(2):
            result = await self.resolver.resolve_update(update(label="loft"))
            self.assertEqual(result.operations, [])
            self.assertIsNotNone(result.clarification)
        self.assertEqual(len(self.requests), 1)

    async def test_explicit_id_also_must_exist_in_backend(self):
        known = await self.resolver.resolve_update(update(("typeId", "SET", 47)))
        self.assertEqual(known.operations[0].value, 47)
        unknown = await self.resolver.resolve_update(update(("typeId", "SET", 999)))
        self.assertEqual(unknown.operations, [])
        self.assertIsNotNone(unknown.clarification)

    async def test_two_conversations_keep_separate_states(self):
        engine = self.engine(update(label="appartement"), update(label="villa"), update(("bedrooms", "SET", 2)))
        first = await engine.process(ConversationRequest(message="Appartement"))
        second = await engine.process(ConversationRequest(message="Villa"))
        followup = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Deux chambres"))
        self.assertEqual(followup.criteria.typeId, 47)
        self.assertNotEqual(first.conversationId, second.conversationId)
        async with engine.store.transaction(second.conversationId) as state:
            self.assertEqual(state.criteria.typeId, 83)
            self.assertIsNone(state.criteria.bedrooms)

    async def test_merge_failure_after_resolution_is_atomic(self):
        engine = self.engine(update(("maxRent", "SET", 100)), update(("minRent", "SET", 200), label="villa"))
        first = await engine.process(ConversationRequest(message="Maximum 100"))
        failed = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Villa minimum 200"))
        self.assertEqual(failed.status, "ERROR")
        self.assertEqual(failed.criteria, first.criteria)


class ConversationRegressionTests(ResolverFixture):
    # Rebuild the previous conversation coverage: its untracked test file is absent.
    async def test_requested_multiturn_cases(self):
        for initial, changes, expected in [
            (update(("neighborhood", "SET", "Mermoz")), update(("bedrooms", "SET", 2)), {"neighborhood": "Mermoz", "bedrooms": 2}),
            (update(("neighborhood", "SET", "Mermoz"), ("maxRent", "SET", 300000)), update(("maxRent", "SET", 350000)), {"neighborhood": "Mermoz", "maxRent": 350000}),
            (update(("neighborhood", "SET", "Mermoz"), ("maxRent", "SET", 350000)), update(("neighborhood", "SET", "Ouakam")), {"neighborhood": "Ouakam", "maxRent": 350000}),
            (update(("neighborhood", "SET", "Mermoz"), ("maxRent", "SET", 350000)), update(("maxRent", "REMOVE", None)), {"neighborhood": "Mermoz", "maxRent": None}),
            (update(("neighborhood", "SET", "Mermoz"), ("bedrooms", "SET", 2)), update(("bedrooms", "SET", 3), ("maxRent", "SET", 400000)), {"neighborhood": "Mermoz", "bedrooms": 3, "maxRent": 400000}),
        ]:
            engine = self.engine(initial, changes)
            first = await engine.process(ConversationRequest(message="Premier tour"))
            second = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Deuxième tour"))
            self.assertEqual(second.status, "SEARCH_READY")
            for key, value in expected.items():
                self.assertEqual(getattr(second.criteria, key), value)

    async def test_unknown_and_invalid_conversation_id(self):
        engine = self.engine()
        result = await engine.process(ConversationRequest(conversationId="missing", message="Mermoz"))
        self.assertEqual(result.status, "INVALID_INPUT")
        self.assertEqual(self.provider.calls, [])
        with self.assertRaises(ValidationError):
            ConversationRequest(conversationId="invalid !", message="Mermoz")

    async def test_short_answer_after_clarification(self):
        engine = self.engine(update(clarification={"field": "maxRent", "message": "Quel budget ?"}),
                             update(("bedrooms", "SET", 2)), update(("maxRent", "SET", 350000)))
        first = await engine.process(ConversationRequest(message="Moins de 300"))
        unrelated = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Deux chambres"))
        self.assertEqual(unrelated.awaitingField, "maxRent")
        last = await engine.process(ConversationRequest(conversationId=first.conversationId, message="350000"))
        self.assertEqual(last.criteria.maxRent, 350000)
        self.assertEqual(last.status, "SEARCH_READY")
        self.assertIsNone(last.awaitingField)
        self.assertEqual(json.loads(self.provider.calls[-1][0])["awaitingField"], "maxRent")

    async def test_invalid_llm_and_fallback(self):
        engine = self.engine(update(("maxRent", "SET", 350000)), "invalid json")
        first = await engine.process(ConversationRequest(message="350000 maximum"))
        failed = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Modifier"))
        self.assertEqual(failed.status, "ERROR")
        self.assertEqual(failed.criteria, first.criteria)
        engine.parser.providers = (Provider('{"operations":[{"field":"budgetMax","operation":"SET","value":1}]}'),
                                   Provider(update(("bedrooms", "SET", 2))))
        recovered = await engine.process(ConversationRequest(conversationId=first.conversationId, message="Deux chambres"))
        self.assertEqual(recovered.criteria.bedrooms, 2)
        self.assertEqual(recovered.criteria.maxRent, 350000)

    async def test_expiration_rollback_and_copy_isolation(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store = InMemoryConversationStore(ttl_seconds=10, clock=lambda: now)
        async with store.transaction(None) as state:
            cid = state.conversationId
            state.criteria.bedrooms = 2
        state.criteria.bedrooms = 99
        with self.assertRaises(RuntimeError):
            async with store.transaction(cid) as state:
                self.assertEqual(state.criteria.bedrooms, 2)
                state.criteria.bedrooms = 5
                raise RuntimeError()
        async with store.transaction(cid) as state:
            self.assertEqual(state.criteria.bedrooms, 2)
        now += timedelta(seconds=11)
        with self.assertRaises(ConversationNotFound):
            async with store.transaction(cid):
                pass

    async def test_concurrent_turns_are_serialized(self):
        store = InMemoryConversationStore()
        async with store.transaction(None) as state:
            cid = state.conversationId
        entered, release = asyncio.Event(), asyncio.Event()
        async def first():
            async with store.transaction(cid) as state:
                entered.set()
                await release.wait()
                state.criteria.bedrooms = 2
        async def second():
            await entered.wait()
            async with store.transaction(cid) as state:
                self.assertEqual(state.criteria.bedrooms, 2)
                state.criteria.maxRent = 350000
        tasks = [asyncio.create_task(first()), asyncio.create_task(second())]
        await entered.wait()
        release.set()
        await asyncio.gather(*tasks)
        async with store.transaction(cid) as state:
            self.assertEqual(state.criteria.bedrooms, 2)
            self.assertEqual(state.criteria.maxRent, 350000)

    async def test_four_turns_preserve_and_remove(self):
        engine = self.engine(update(("neighborhood", "SET", "Mermoz")),
                             update(("bedrooms", "SET", 2), ("maxRent", "SET", 350000)),
                             update(("neighborhood", "SET", "Ouakam")), update(("maxRent", "REMOVE", None)))
        cid = None
        for message in ["Mermoz", "Deux chambres et maximum 350000", "Ouakam", "Enlève la limite de prix"]:
            result = await engine.process(ConversationRequest(conversationId=cid, message=message))
            cid = result.conversationId
            self.assertEqual(result.status, "SEARCH_READY")
        self.assertEqual(result.criteria.neighborhood, "Ouakam")
        self.assertEqual(result.criteria.bedrooms, 2)
        self.assertIsNone(result.criteria.maxRent)


class ValidationTests(unittest.TestCase):
    def test_criteria_and_operation_validation(self):
        for values in [{"typeId": 0}, {"typeId": True}, {"minRent": -1}, {"maxRent": "300"},
                       {"bedrooms": True}, {"maxRent": 1.5}, {"furnished": "false"},
                       {"minRent": 2, "maxRent": 1}, {"minArea": 2, "maxArea": 1},
                       {"minArea": float("nan")}, {"availableBefore": "2026-02-30"},
                       {"minSharedHousingCapacity": 1}, {"propertyType": "villa"}]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                PropertyFilterRequest.model_validate(values)
        for op in [{"field": "maxRent", "operation": "SET"}, {"field": "budgetMax", "operation": "SET", "value": 1},
                   {"field": "maxRent", "operation": "REMOVE", "value": 1}]:
            with self.assertRaises(ValidationError):
                CriteriaOperation.model_validate(op)

    def test_merger_rejects_bypassed_validation(self):
        invalid = CriteriaUpdate.model_construct(operations=[CriteriaOperation.model_construct(field="unknown", operation="SET", value=1)])
        with self.assertRaises(ValidationError):
            CriteriaMerger().merge(PropertyFilterRequest(), invalid)

    def test_atomic_ranges_removes_and_shared_housing(self):
        original = PropertyFilterRequest(minRent=100, maxRent=200, furnished=True, sharedHousingAllowed=True, minSharedHousingCapacity=3)
        merged = CriteriaMerger().merge(original, update(("minRent", "SET", 300), ("maxRent", "SET", 400),
                                                        ("furnished", "REMOVE", None), ("sharedHousingAllowed", "SET", False)))
        self.assertEqual(merged.maxRent, 400)
        self.assertIsNone(merged.furnished)
        self.assertIsNone(merged.minSharedHousingCapacity)
        self.assertEqual(original.maxRent, 200)


class RouteTests(unittest.TestCase):
    def test_startup_openapi_and_existing_routes(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            paths = client.get("/openapi.json").json()["paths"]
            for path in ["/text", "/audio", "/conversation", "/conversation/audio"]:
                self.assertIn("/api/v1/search" + path, paths)
            app.state.parser = SearchParserService(Provider('{"propertyType":"appartement","maxRent":350000}', '{"propertyType":"studio"}'), Provider())
            app.state.transcriber = AsyncMock()
            app.state.transcriber.transcribe.return_value = "Un studio"
            text = client.post("/api/v1/search/text", json={"query": "Appartement"})
            self.assertEqual(text.status_code, 200)
            self.assertEqual(text.json()["criteria"]["maxRent"], "350000")
            self.assertEqual(text.json()["criteria"]["propertyType"], "appartement")
            audio = client.post("/api/v1/search/audio", files={"file": ("a.wav", b"audio", "audio/wav")})
            self.assertEqual(audio.json()["criteria"]["propertyType"], "studio")

    def test_text_audio_resolution_and_stt_error_share_engine(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=CATALOG)
        with TestClient(app) as client:
            http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                resolver = PropertyTypeResolver(http_client, Settings(_env_file=None))
                parser = SearchParserService(Provider(update(("neighborhood", "SET", "Mermoz"), label="appartement"),
                                                     update(("neighborhood", "SET", "Mermoz"), label="appartement")), Provider())
                app.state.conversation = ConversationService(parser, InMemoryConversationStore(), resolver)
                app.state.transcriber = AsyncMock()
                app.state.transcriber.transcribe.return_value = "  Appartement à Mermoz  "
                first = client.post("/api/v1/search/conversation", json={"message": "Appartement à Mermoz"}).json()
                second = client.post("/api/v1/search/conversation/audio", files={"file": ("a.wav", b"audio", "audio/wav")}).json()
                self.assertEqual(first["criteria"], second["criteria"])
                self.assertEqual(first["criteria"]["typeId"], 47)
                self.assertEqual(second["transcription"], "  Appartement à Mermoz  ")
                self.assertEqual(len(requests), 1)
                app.state.transcriber.transcribe.side_effect = TranscriptionException()
                failed = client.post("/api/v1/search/conversation/audio", data={"conversationId": first["conversationId"]},
                                     files={"file": ("a.wav", b"audio", "audio/wav")}).json()
                self.assertEqual(failed["status"], "ERROR")
                self.assertEqual(failed["criteria"], first["criteria"])
            finally:
                asyncio.run(http_client.aclose())

    def test_invalid_input_and_audio_validation(self):
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/v1/search/conversation", json={"message": " "}).status_code, 422)
            for path in ["/api/v1/search/audio", "/api/v1/search/conversation/audio"]:
                self.assertEqual(client.post(path, files={"file": ("a.wav", b"", "audio/wav")}).status_code, 400)
                self.assertEqual(client.post(path, files={"file": ("a.txt", b"hello", "text/plain")}).status_code, 415)


if __name__ == "__main__":
    unittest.main()
