import json

from pokebot.queue import GitHubQueue


class R:
    def __init__(self, status, payload=None):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_drain_executes_in_order_and_deletes(monkeypatch):
    import pokebot.queue as qmod
    files = {"queue/2-b.json": {"chat_id": 7, "text": "/cerca"}, "queue/1-a.json": {"chat_id": 7, "text": "/rimuovi 145"}}
    calls = []

    def fake_get(url, **kw):
        calls.append(("GET", url))
        if url.endswith("/contents/queue"):
            return R(200, [{"type": "file", "name": p.split("/")[1], "path": p, "sha": "s" + p, "download_url": "https://raw/" + p} for p in files])
        return R(200, files[url.split("https://raw/")[1]])

    def fake_delete(url, **kw):
        calls.append(("DELETE", url, kw["json"]["sha"]))
        return R(200)

    monkeypatch.setattr(qmod.requests, "get", fake_get)
    monkeypatch.setattr(qmod.requests, "delete", fake_delete)
    handled = []
    q = GitHubQueue(token="t", repo="o/r")
    n, want = q.drain(lambda p: handled.append(p["text"]) or p["text"] == "/cerca")
    assert n == 2 and want is True
    assert handled == ["/rimuovi 145", "/cerca"]  # ordine per nome = ordine di arrivo
    assert [c for c in calls if c[0] == "DELETE"] == [("DELETE", "https://api.github.com/repos/o/r/contents/queue/1-a.json", "squeue/1-a.json"),
                                                      ("DELETE", "https://api.github.com/repos/o/r/contents/queue/2-b.json", "squeue/2-b.json")]


def test_drain_without_branch_or_token(monkeypatch):
    import pokebot.queue as qmod
    monkeypatch.setattr(qmod.requests, "get", lambda url, **kw: R(404))
    assert GitHubQueue(token="t", repo="o/r").drain(lambda p: True) == (0, False)
    assert GitHubQueue(token="", repo="").drain(lambda p: True) == (0, False)
