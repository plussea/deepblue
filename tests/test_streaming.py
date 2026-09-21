import io
import json
import unittest

from deepblue.llm import DeepSeekClient, ModelError


def frame(delta=None, finish=None, **extra):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}


def stream(*chunks, done=True):
    data = b": keepalive\n\n"
    data += b"".join(("data: " + json.dumps(chunk, ensure_ascii=False) + "\r\n\r\n").encode("utf-8") for chunk in chunks)
    if done:
        data += b"data: [DONE]\n\n"
    return io.BytesIO(data)


class StreamingTests(unittest.TestCase):
    def test_text_deltas_and_usage_on_final_choice(self):
        emitted = []
        result = DeepSeekClient._read_stream(stream(
            frame({"role": "assistant", "content": "深"}), frame({"content": "蓝"}),
            frame(finish="stop", usage={"total_tokens": 12}),
        ), emitted.append)
        self.assertEqual(emitted, ["深", "蓝"])
        self.assertEqual(result.message["content"], "深蓝")
        self.assertEqual(result.usage["total_tokens"], 12)

    def test_interleaved_tool_arguments_and_separate_usage(self):
        result = DeepSeekClient._read_stream(stream(
            frame({"tool_calls": [{"index": 0, "id": "a", "type": "function",
                                   "function": {"name": "write", "arguments": '{"path":"文'}}]}),
            frame({"tool_calls": [{"index": 1, "id": "b", "type": "function",
                                   "function": {"name": "read", "arguments": '{"path":"x"}'}}]}),
            frame({"tool_calls": [{"index": 0, "function": {"arguments": '件","content":"ok"}'}}]}),
            frame(finish="tool_calls"), {"choices": [], "usage": {"total_tokens": 20}},
        ), lambda text: None)
        calls = result.message["tool_calls"]
        self.assertEqual([call["id"] for call in calls], ["a", "b"])
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["path"], "文件")
        self.assertEqual(result.usage["total_tokens"], 20)

    def test_multiline_sse_data(self):
        raw = b'data: {"choices":\ndata: [{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        self.assertEqual(DeepSeekClient._read_stream(io.BytesIO(raw), lambda text: None).message["content"], "ok")

    def test_abrupt_eof_even_after_finish_is_not_success(self):
        with self.assertRaisesRegex(ModelError, "DONE"):
            DeepSeekClient._read_stream(stream(frame({"content": "partial"}, "stop"), done=False), lambda text: None)

    def test_done_without_finish_or_duplicate_ids_is_rejected(self):
        for chunks in ([frame({"content": "partial"})], [frame({"tool_calls": [
            {"index": n, "id": "same", "function": {"name": "read", "arguments": "{}"}} for n in range(2)
        ]}, "tool_calls")]):
            with self.subTest(chunks=chunks), self.assertRaises(ModelError):
                DeepSeekClient._read_stream(stream(*chunks), lambda text: None)

    def test_data_after_finish_and_malformed_chunks_are_rejected(self):
        for chunks in ([frame({"content": "a"}, "stop"), frame({"content": "b"})],
                       [{"error": {"message": "private upstream data"}}],
                       [frame({"tool_calls": [{"index": -1}]}, "tool_calls")],
                       [frame({"content": 123}, "stop")]):
            with self.subTest(chunks=chunks), self.assertRaises(ModelError):
                DeepSeekClient._read_stream(stream(*chunks), lambda text: None)

    def test_truncation_reason_preserved_for_agent(self):
        result = DeepSeekClient._read_stream(stream(frame({"tool_calls": [
            {"index": 0, "id": "x", "function": {"name": "write", "arguments": '{"path":"x"'}}
        ]}, "length")), lambda text: None)
        self.assertEqual(result.finish_reason, "length")
