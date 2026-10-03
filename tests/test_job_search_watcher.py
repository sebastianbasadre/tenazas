import importlib
import sys
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1] / "workspace" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

watcher = importlib.import_module("job_search_watcher")


def test_format_notification_contains_match_date_and_link():
    message = watcher._format_notification(
        [
            {
                "title": "Technical Account Manager",
                "company": "Example",
                "match_score": 71,
                "posted_date": "2026-06-25",
                "url": "https://example.com/job/1",
            }
        ]
    )
    assert "71%" in message
    assert "2026-06-25" in message
    assert "https://example.com/job/1" in message


def test_run_once_notifies_only_new_ids(monkeypatch):
    old = {"id": "old", "title": "Old", "match_score": 70}
    new = {
        "id": "new",
        "title": "New",
        "company": "Example",
        "match_score": 75,
        "posted_date": "2026-06-25",
        "url": "https://example.com/new",
    }
    calls = {"loads": 0, "messages": []}

    def fake_load():
        calls["loads"] += 1
        return [old] if calls["loads"] == 1 else [new, old]

    monkeypatch.setattr(watcher.agent_job_search, "_load_opportunities", fake_load)
    monkeypatch.setattr(watcher.agent_job_search, "discover", lambda: {"added": 1})
    monkeypatch.setattr(watcher, "load_config", lambda: {})
    monkeypatch.setattr(watcher, "get_telegram_bot_token", lambda _cfg: "token")
    monkeypatch.setattr(watcher, "get_default_telegram_chat_id", lambda _cfg: "chat")
    monkeypatch.setattr(
        watcher,
        "send_message",
        lambda _token, _chat, text: calls["messages"].append(text),
    )
    monkeypatch.setattr(
        watcher.agent_job_search,
        "prepare_tailored_cv_for_id",
        lambda _id: {"path": "/tmp/cv.pdf", "filename": "cv.pdf", "score": 75},
    )
    monkeypatch.setattr(
        watcher,
        "send_document",
        lambda _token, _chat, path, caption="": (
            calls.setdefault("documents", []).append((path, caption))
            or {"ok": True, "result": {"document": {"file_id": "file-1"}}}
        ),
    )
    monkeypatch.setattr(watcher, "heartbeat", lambda *_args: None)
    monkeypatch.setattr(watcher, "_log", lambda *_args: None)

    result = watcher.run_once()
    assert result["new"] == 1
    assert result["notified"] is True
    assert len(calls["messages"]) == 1
    assert len(calls["documents"]) == 1
    assert calls["documents"][0][1].startswith("CV tailor-made adjunto:")
    assert any("Aplicar: https://example.com/new" in message for message in calls["messages"])


def test_exactly_70_does_not_generate_cv(monkeypatch):
    old = {"id": "old"}
    new = {
        "id": "new",
        "title": "Product Manager",
        "company": "Example",
        "match_score": 70,
        "posted_date": "2026-06-25",
        "url": "https://example.com/new",
    }
    loads = iter([[old], [new, old]])
    documents = []
    monkeypatch.setattr(watcher.agent_job_search, "_load_opportunities", lambda: next(loads))
    monkeypatch.setattr(watcher.agent_job_search, "discover", lambda: {"added": 1})
    monkeypatch.setattr(watcher, "load_config", lambda: {})
    monkeypatch.setattr(watcher, "get_telegram_bot_token", lambda _cfg: "token")
    monkeypatch.setattr(watcher, "get_default_telegram_chat_id", lambda _cfg: "chat")
    monkeypatch.setattr(watcher, "send_message", lambda *_args: None)
    monkeypatch.setattr(watcher, "send_document", lambda *args, **kwargs: documents.append(args))
    monkeypatch.setattr(watcher, "heartbeat", lambda *_args: None)
    monkeypatch.setattr(watcher, "_log", lambda *_args: None)
    watcher.run_once()
    assert documents == []
