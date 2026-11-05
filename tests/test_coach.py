import threading

from chessreview.coach import CodexClient


class RecordedClient(CodexClient):
    def start(self):
        pass

    def rpc(self, method, params, timeout=30):
        if method == "account/read":
            return {"account": {"type": "chatgpt"}}
        if method == "thread/start":
            return {"thread": {"id": "t"}}
        if method == "turn/start":
            self.pending = [
                {"method": "item/agentMessage/delta", "params": {"threadId": "t", "delta": "这步"}},
                {"method": "item/agentMessage/delta", "params": {"threadId": "t", "delta": "很好。"}},
                {"method": "thread/tokenUsage/updated", "params": {"threadId": "t", "tokenUsage": {"last": {"totalTokens": 123}}}},
                {"method": "item/completed", "params": {"threadId": "t", "item": {"type": "agentMessage", "phase": "final_answer", "text": "这步很好。"}}},
                {"method": "turn/completed", "params": {"threadId": "t", "turn": {"id": "u", "status": "completed"}}},
            ]
            return {"turn": {"id": "u"}}
        raise AssertionError(method)


def test_streamed_reply_is_not_duplicated_and_usage_is_preserved():
    client = RecordedClient()
    updates = []
    answer, usage = client.explain({}, [], "为什么", "gpt-6-luna", updates.append, threading.Event())
    assert answer == "这步很好。"
    assert updates == ["这步", "这步很好。"]
    assert usage["totalTokens"] == 123
