from mail_cleaner import config
from mail_cleaner.gmail_client import iter_messages
from mail_cleaner.report import resume_ids


def row(
    message_id: str,
    signals: list[str],
    verdict: str = "ad",
    sender: str = "a@shop.example",
) -> dict:
    return {
        "message_id": message_id,
        "signals": signals,
        "verdict": verdict,
        "sender_email": sender,
        "sender_domain": sender.rsplit("@", 1)[-1],
    }


def test_resume_ids_skips_decided_but_retries_llm_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    rows = [
        row("ok1", ["header:list-unsubscribe"]),
        row("ok2", ["llm:ad:newsletter"]),
        row("failed", ["header:list-unsubscribe", "llm:unsure:llm-error:APIConnectionError"]),
    ]
    assert resume_ids(rows) == {"ok1", "ok2"}


def test_resume_reevaluates_keeps_hit_by_new_sender_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "quarantine_senders.txt").write_text("shop.example\n")
    rows = [
        row("kept", [], verdict="keep", sender="news@shop.example"),
        row("done", [], verdict="ad", sender="news@shop.example"),
        row("other", [], verdict="keep", sender="x@other.example"),
    ]
    # "kept" is re-evaluated (new rule would flip it); the ad row and the
    # unaffected keep stay skipped.
    assert resume_ids(rows) == {"done", "other"}


def test_resume_reevaluates_fresh_notification_keeps(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    rows = [
        row("fresh", [], verdict="keep", sender="notifications@github.com"),
        row("expired", [], verdict="expired", sender="notifications@github.com"),
    ]
    assert resume_ids(rows) == {"expired"}


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self, num_retries=0):
        return self.payload


class FakeMessages:
    def __init__(self, pages, metas):
        self.pages = pages
        self.metas = metas
        self.get_calls = []

    def list(self, userId, q, pageToken, maxResults):
        return FakeRequest(self.pages[pageToken])

    def get(self, userId, id, format, metadataHeaders):
        self.get_calls.append(id)
        return FakeRequest(self.metas[id])


class FakeService:
    def __init__(self, messages):
        self._messages = messages

    def users(self):
        return self

    def messages(self):
        return self._messages


def make_meta(msg_id: str) -> dict:
    return {
        "id": msg_id,
        "threadId": "t",
        "internalDate": "0",
        "labelIds": [],
        "snippet": "",
        "payload": {"headers": [{"name": "From", "value": f"a@{msg_id}.se"}]},
    }


def test_iter_messages_never_fetches_skipped_ids():
    messages = FakeMessages(
        pages={
            None: {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "p2"},
            "p2": {"messages": [{"id": "m3"}]},
        },
        metas={m: make_meta(m) for m in ("m1", "m2", "m3")},
    )
    service = FakeService(messages)

    yielded = list(iter_messages(service, days=7, skip_ids={"m1", "m3"}))

    assert messages.get_calls == ["m2"]
    assert [m.message_id for m in yielded] == ["m2"]
