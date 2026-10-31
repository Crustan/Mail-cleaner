from mail_cleaner import config
from mail_cleaner.triage import keep_senders


def row(sender: str, verdict: str = "keep", subject: str = "Hello") -> dict:
    return {
        "sender_email": sender,
        "sender_domain": sender.rsplit("@", 1)[-1],
        "verdict": verdict,
        "subject": subject,
        "message_id": f"{sender}-{subject}",
    }


def test_aggregates_keeps_by_volume_with_min(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    rows = (
        [row("big@a.se", subject=f"s{i}") for i in range(5)]
        + [row("small@b.se")] * 2
        + [row("ad@c.se", verdict="ad")] * 9
    )
    result = keep_senders(rows, min_count=3)
    assert [e["sender"] for e in result] == ["big@a.se"]
    assert result[0]["count"] == 5
    assert len(result[0]["subjects"]) == 3  # last three distinct subjects


def test_already_ruled_senders_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "protected_senders.txt").write_text("vip@a.se\n")
    (tmp_path / "quarantine_senders.txt").write_text("b.se\n")
    rows = [row("vip@a.se")] * 4 + [row("news@b.se")] * 4 + [row("new@c.se")] * 4
    assert [e["sender"] for e in keep_senders(rows, min_count=3)] == ["new@c.se"]
