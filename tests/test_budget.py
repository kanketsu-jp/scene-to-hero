import json
import threading

from scene_to_hero.budget import Budget


def test_budget_limit_and_schema(tmp_path):
    b = Budget(tmp_path / "logs" / "cost.jsonl", 1)
    assert b.reserve("x", "ep", .5, "second", 2)[0]
    assert not b.reserve("x", "ep", .5, "second", 1)[0]
    rows = [json.loads(x) for x in (tmp_path / "logs" / "cost.jsonl").read_text().splitlines()]
    assert rows[0]["kind"] == "estimate" and rows[0]["unit"] == "second"
    b.record_result("x", "ep", 1, None, False)
    assert b.total() == 1


def test_budget_bad_lines_and_threads(tmp_path):
    path = tmp_path / "cost.jsonl"; path.write_text("bad\n{}\n")
    b = Budget(path, 1)
    assert b.total() == 0
    results = []
    threads = [threading.Thread(target=lambda: results.append(b.reserve("x", "ep", .1, "second", 1)[0])) for _ in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sum(results) <= 10
    assert b.total() <= 1 + 1e-9
