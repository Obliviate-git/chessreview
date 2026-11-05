import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from chessreview.coach import api_explain
from chessreview.settings import Settings


def test_openai_compatible_stream_and_usage():
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["authorization"] = self.headers.get("Authorization")
            received["payload"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for text in ("这一步", "可以防守。"):
                chunk = {"choices": [{"delta": {"content": text}}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b'data: {"choices":[],"usage":{"total_tokens":42}}\n\ndata: [DONE]\n\n')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings(provider="api")
    settings.api_profiles["自定义"] = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "test-model"}
    settings.get_key = lambda: "test-placeholder"
    updates = []
    try:
        answer, usage = api_explain(settings, {"当前FEN": "test"}, [], "为什么", updates.append, threading.Event())
        assert answer == "这一步可以防守。"
        assert usage["total_tokens"] == 42
        assert received["path"] == "/v1/chat/completions"
        assert received["authorization"] == "Bearer test-placeholder"
        assert received["payload"]["model"] == "test-model"
    finally:
        server.shutdown()
        server.server_close()
