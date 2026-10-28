from mail_cleaner import config
from mail_cleaner.filters import candidates


def rows(sender: str, **verdicts) -> list[dict]:
    out = []
    for verdict, count in verdicts.items():
        out += [{"sender_email": sender, "verdict": verdict}] * count
    return out


def test_stable_ad_sender_qualifies(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert candidates(rows("ads@shop.se", ad=5)) == [("ads@shop.se", 5)]


def test_below_min_or_mixed_senders_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    log = rows("few@shop.se", ad=2) + rows("mixed@shop.se", ad=5, keep=1)
    assert candidates(log) == []


def test_unsure_does_not_disqualify_but_protected_does(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    log = rows("ads@shop.se", ad=4, unsure=1) + rows("prot@shop.se", ad=4, protected=1)
    assert candidates(log) == [("ads@shop.se", 4)]


def test_protected_list_excludes_sender_and_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    config.add_protected_senders({"ads@shop.se", "bank.se"})
    log = rows("ads@shop.se", ad=9) + rows("info@bank.se", ad=9)
    assert candidates(log) == []


def test_sorted_by_volume(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    log = rows("small@a.se", ad=3) + rows("big@b.se", ad=10)
    assert candidates(log) == [("big@b.se", 10), ("small@a.se", 3)]
