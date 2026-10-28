from mail_cleaner import config


def test_add_protected_senders_dedupes_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    added = config.add_protected_senders({"News@Shop.example", "a@b.se"})
    assert added == {"news@shop.example", "a@b.se"}
    assert config.load_protected_senders() == {"news@shop.example", "a@b.se"}

    # Second add of the same sender is a no-op
    assert config.add_protected_senders({"a@b.se"}) == set()
    text = (tmp_path / "protected_senders.txt").read_text()
    assert text.count("a@b.se") == 1
