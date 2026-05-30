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

    async def send_text(self, payload: str) -> None:
        self.sent_payloads.append(payload)


class WebServiceCoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state.active_clients.clear()
        state.pending_queues.clear()
        state.ws_to_req_ids.clear()
        state.req_id_to_ws_id.clear()
        state.req_id_timestamps.clear()
        state.ws_node_labels.clear()
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

    async def test_validate_model_mapping_rejects_invalid_values(self):
        valid_mapping, error = web_service.validate_model_mapping({"gpt-5.5": "mimo-v2.5-pro"})
        self.assertIsNone(error)
        self.assertEqual(valid_mapping, {"gpt-5.5": "mimo-v2.5-pro"})

        invalid_mapping, error = web_service.validate_model_mapping({"gpt-5.5": 123})
        self.assertIsNone(invalid_mapping)
        self.assertIn("字符串", error)


if __name__ == "__main__":
    unittest.main()
