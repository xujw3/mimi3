import asyncio
import time
import unittest
from types import SimpleNamespace

from mimo2api.gateway_state import GatewayState, state
from mimo2api import web_service


class FakeWebSocket:
    def __init__(self):
        self.client = SimpleNamespace(host="test-node", port=12345)
        self.sent_payloads = []
        self.closed = False
        self.close_code = None

    async def send_text(self, payload: str) -> None:
        self.sent_payloads.append(payload)

    async def accept(self):
        pass

    async def close(self, code: int = 1000):
        self.closed = True
        self.close_code = code


class WebServiceCoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state.active_clients.clear()
        state.pending_queues.clear()
        state.ws_to_req_ids.clear()
        state.req_id_to_ws_id.clear()
        state.req_id_timestamps.clear()
        state.ws_node_labels.clear()
        state.ws_node_ids.clear()
        state.ws_node_generations.clear()
        state.ws_node_managed.clear()
        state.node_id_to_ws.clear()
        state.node_latest_generations.clear()
        state.client_cooldowns.clear()
        state.current_client_index = 0
        state.metrics = GatewayState._default_metrics()
        state.metrics_started_at = time.time()
        state.recent_errors.clear()

    async def test_dispatch_timeout_cleans_pending_queue(self):
        old_timeout = web_service.NODE_RESPONSE_TIMEOUT
        web_service.NODE_RESPONSE_TIMEOUT = 0.01
        ws = FakeWebSocket()
        state.active_clients.append(ws)
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await web_service.dispatch_to_node(
                    method="POST",
                    path="/v1/chat/completions",
                    body="{}",
                    log_label="test",
                    attempt_number=1,
                )
        finally:
            web_service.NODE_RESPONSE_TIMEOUT = old_timeout
            state.active_clients.clear()

        self.assertEqual(state.pending_queues, {})
        self.assertEqual(state.req_id_to_ws_id, {})
        self.assertEqual(state.ws_to_req_ids, {})
        self.assertEqual(state.active_clients, [])
        self.assertTrue(ws.closed)
        self.assertEqual(ws.close_code, 1011)
        self.assertEqual(len(ws.sent_payloads), 1)

    async def test_gateway_unavailable_response_records_503_metrics(self):
        response = web_service.gateway_unavailable_response(
            "/v1/chat/completions",
            time.monotonic(),
            is_streaming=False,
        )

        self.assertEqual(response.status_code, 503)
        route_metrics = state.metrics["routes"]["/v1/chat/completions"]
        self.assertEqual(state.metrics["requests_total"], 1)
        self.assertEqual(state.metrics["requests_failed"], 1)
        self.assertEqual(route_metrics["status_codes"]["503"], 1)

    async def test_routable_clients_prefer_managed_nodes(self):
        legacy_ws = FakeWebSocket()
        managed_ws = FakeWebSocket()
        state.active_clients.extend([legacy_ws, managed_ws])
        state.ws_node_managed[id(legacy_ws)] = False
        state.ws_node_managed[id(managed_ws)] = True

        self.assertEqual(web_service.routable_clients(), [managed_ws])
        self.assertIs(web_service.get_next_client(), managed_ws)
        self.assertEqual(web_service.get_available_client_count(), 1)

    async def test_unregister_client_cleans_metadata_and_orphans(self):
        ws = FakeWebSocket()
        queue = asyncio.Queue()
        state.active_clients.append(ws)
        state.ws_node_labels[id(ws)] = "account:u1"
        state.ws_node_ids[id(ws)] = "account:u1"
        state.ws_node_generations[id(ws)] = 123
        state.ws_node_managed[id(ws)] = True
        state.node_id_to_ws["account:u1"] = ws
        state.node_latest_generations["account:u1"] = 123
        state.pending_queues["req-1"] = queue
        state.req_id_to_ws_id["req-1"] = id(ws)
        state.ws_to_req_ids[id(ws)] = {"req-1"}
        state.req_id_timestamps["req-1"] = time.time()

        orphan_ids = web_service.unregister_client(ws, error_body="test disconnect")

        self.assertEqual(orphan_ids, {"req-1"})
        self.assertEqual(state.active_clients, [])
        self.assertEqual(state.pending_queues, {})
        self.assertEqual(state.req_id_to_ws_id, {})
        self.assertEqual(state.ws_to_req_ids, {})
        self.assertNotIn("account:u1", state.node_id_to_ws)
        self.assertEqual(state.node_latest_generations["account:u1"], 123)
        self.assertEqual(queue.get_nowait(), {"type": "error", "body": "test disconnect"})

    async def test_duplicate_same_generation_is_rejected(self):
        existing_ws = FakeWebSocket()
        incoming_ws = FakeWebSocket()
        state.active_clients.append(existing_ws)
        state.ws_node_labels[id(existing_ws)] = "account:u1"
        state.ws_node_ids[id(existing_ws)] = "account:u1"
        state.ws_node_generations[id(existing_ws)] = 123
        state.ws_node_managed[id(existing_ws)] = True
        state.node_id_to_ws["account:u1"] = existing_ws
        state.node_latest_generations["account:u1"] = 123

        admitted = await web_service.ensure_node_admissible(incoming_ws, "account:u1", 123, True)

        self.assertFalse(admitted)
        self.assertEqual(state.active_clients, [existing_ws])
        self.assertFalse(existing_ws.closed)
        self.assertTrue(incoming_ws.closed)
        self.assertEqual(incoming_ws.close_code, 1013)

    async def test_newer_generation_replaces_existing_node(self):
        existing_ws = FakeWebSocket()
        incoming_ws = FakeWebSocket()
        state.active_clients.append(existing_ws)
        state.ws_node_labels[id(existing_ws)] = "account:u1"
        state.ws_node_ids[id(existing_ws)] = "account:u1"
        state.ws_node_generations[id(existing_ws)] = 123
        state.ws_node_managed[id(existing_ws)] = True
        state.node_id_to_ws["account:u1"] = existing_ws
        state.node_latest_generations["account:u1"] = 123

        admitted = await web_service.ensure_node_admissible(incoming_ws, "account:u1", 124, True)

        self.assertTrue(admitted)
        self.assertEqual(state.active_clients, [])
        self.assertTrue(existing_ws.closed)
        self.assertEqual(existing_ws.close_code, 1008)
        self.assertEqual(state.node_latest_generations["account:u1"], 124)

    async def test_validate_model_mapping_rejects_invalid_values(self):
        valid_mapping, error = web_service.validate_model_mapping({"gpt-5.5": "mimo-v2.5-pro"})
        self.assertIsNone(error)
        self.assertEqual(valid_mapping, {"gpt-5.5": "mimo-v2.5-pro"})

        invalid_mapping, error = web_service.validate_model_mapping({"gpt-5.5": 123})
        self.assertIsNone(invalid_mapping)
        self.assertIn("字符串", error)


if __name__ == "__main__":
    unittest.main()
