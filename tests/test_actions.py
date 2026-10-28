from mail_cleaner.actions import describe_plan, plan


ROWS = [
    {"message_id": "1", "sender_email": "a@x.se", "verdict": "ad"},
    {"message_id": "2", "sender_email": "a@x.se", "verdict": "ad"},
    {"message_id": "3", "sender_email": "b@y.se", "verdict": "expired"},
    {"message_id": "4", "sender_email": "c@z.se", "verdict": "keep"},
    {"message_id": "5", "sender_email": "d@w.se", "verdict": "protected"},
    {"message_id": "6", "sender_email": "e@v.se", "verdict": "unsure"},
]


def test_plan_only_actionable_verdicts():
    planned = plan(ROWS, {"ad", "expired"}, max_actions=None)
    assert [row["message_id"] for row in planned] == ["1", "2", "3"]


def test_plan_respects_max():
    assert len(plan(ROWS, {"ad", "expired"}, max_actions=2)) == 2


def test_plan_single_verdict():
    planned = plan(ROWS, {"expired"}, max_actions=None)
    assert [row["message_id"] for row in planned] == ["3"]


def test_describe_plan_groups_by_sender():
    text = describe_plan(plan(ROWS, {"ad", "expired"}, None), trash=False)
    assert "3 messages" in text
    assert "a@x.se" in text and "(ad)" in text
    assert "trash" not in text
