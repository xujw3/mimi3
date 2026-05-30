import json
import unittest

from mimo2api.responses_converter import ResponsesStreamConverter, convert_request, convert_response


class ResponsesConverterTest(unittest.TestCase):
    def test_convert_request_preserves_reasoning_and_tool_history(self):
        req = {
            "model": "mimo-v2.5",
            "instructions": "system prompt",
            "input": [
                {"type": "reasoning", "reasoning_content": "thinking"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]},
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": {"q": "x"}},
                {"type": "function_call_output", "call_id": "call_1", "output": {"ok": True}},
            ],
            "max_output_tokens": 123,
        }

        chat_req = convert_request(req)

        self.assertEqual(chat_req["max_tokens"], 123)
        self.assertEqual(chat_req["messages"][0]["role"], "system")
        assistant_msg = chat_req["messages"][1]
        self.assertEqual(assistant_msg["role"], "assistant")
        self.assertEqual(assistant_msg["reasoning_content"], "thinking")
        self.assertEqual(assistant_msg["tool_calls"][0]["function"]["name"], "lookup")
        self.assertEqual(chat_req["messages"][2]["role"], "tool")

    def test_convert_response_maps_usage_and_text(self):
        chat_resp = {
            "model": "mimo-v2.5",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }

        resp = convert_response(chat_resp)

        self.assertEqual(resp["model"], "mimo-v2.5")
        self.assertEqual(resp["usage"]["input_tokens"], 1)
        self.assertEqual(resp["output"][0]["content"][0]["text"], "hello")

    def test_stream_converter_emits_completed_event(self):
        converter = ResponsesStreamConverter(model="mimo-v2.5")
        chunk = "data: " + json.dumps({
            "choices": [{"delta": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        })

        events = converter.process_chunk(chunk)
        events.extend(converter.process_chunk("data: [DONE]"))

        joined = "".join(events)
        self.assertIn("response.output_text.delta", joined)
        self.assertIn("response.completed", joined)
        self.assertIn('"total_tokens": 3', joined)


if __name__ == "__main__":
    unittest.main()
