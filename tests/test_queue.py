import json

from pokebot import vault
from pokebot.queue import GitHubQueue

TOKEN = "123:abc"


class R:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._p, self.content = status, payload, content

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _fake(monkeypatch, files, statuses=None):
    import pokebot.queue as qmod
    monkeypatch.setattr("pokebot.config.TELEGRAM_BOT_TOKEN", TOKEN)
    calls = []

    def fake_get(url, **kw):
        calls.append(("GET", url))
        if url.endswith("/contents/queue"):
            return R(200, [{"type": "file", "name": p.split("/")[1], "path": p, "sha": "s" + p, "download_url": "https://raw/" + p}
                           for p in files])
        path = url.split("https://raw/")[1]
        return R((statuses or {}).get(path, 200), content=files[path])

    def fake_delete(url, **kw):
        calls.append(("DELETE", url.rsplit("/", 1)[1]))
        return R(200)

    monkeypatch.setattr(qmod.requests, "get", fake_get)
    monkeypatch.setattr(qmod.requests, "delete", fake_delete)
    return calls


def test_drain_executes_in_order_and_deletes(monkeypatch):
    files = {"queue/2-b.json": vault.seal(json.dumps({"chat_id": 7, "text": "/cerca"}).encode(), TOKEN),
             "queue/1-a.json": json.dumps({"chat_id": 7, "text": "/rimuovi 145"}).encode()}  # vecchio formato in chiaro
    calls = _fake(monkeypatch, files)
    handled = []
    n, want = GitHubQueue(token="t", repo="o/r").drain(lambda p: handled.append(p["text"]) or p["text"] == "/cerca")
    assert n == 2 and want is True
    assert handled == ["/rimuovi 145", "/cerca"]  # ordine per nome = ordine di arrivo
    assert [c for c in calls if c[0] == "DELETE"] == [("DELETE", "1-a.json"), ("DELETE", "2-b.json")]


def test_unreadable_now_is_kept_for_next_run(monkeypatch):
    pay = vault.seal(json.dumps({"chat_id": 7, "text": "/pagamento", "payment": {"x": 1}}).encode(), TOKEN)
    files = {"queue/1-a.json": pay, "queue/2-b.json": vault.seal(b'{"chat_id": 7, "text": "/cerca"}', TOKEN)}
    calls = _fake(monkeypatch, files, statuses={"queue/1-a.json": 502})
    handled = []
    assert GitHubQueue(token="t", repo="o/r").drain(lambda p: handled.append(p)) == (0, False)
    assert handled == [] and not [c for c in calls if c[0] == "DELETE"]  # né il pagamento né quelli dopo si perdono


def test_drain_without_branch_or_token(monkeypatch):
    import pokebot.queue as qmod
    monkeypatch.setattr(qmod.requests, "get", lambda url, **kw: R(404))
    assert GitHubQueue(token="t", repo="o/r").drain(lambda p: True) == (0, False)
    assert GitHubQueue(token="", repo="").drain(lambda p: True) == (0, False)


def test_vault_roundtrip_and_plaintext_passthrough():
    blob = vault.seal(b"ciao", TOKEN)
    assert vault.is_sealed(blob) and vault.unseal(blob, TOKEN) == b"ciao"
    assert vault.unseal(b"{}", TOKEN) == b"{}"


def test_deferred_ack_and_unreadable_files(monkeypatch, tmp_path):
    import time
    other = vault.seal(b'{"chat_id": 7, "text": "/stato"}', "999:altro")  # cifrato con un altro token, appena arrivato
    files = {f"queue/{int(time.time() * 1000)}-a.json": other, "queue/9999999999999-b.json": vault.seal(b'{"chat_id": 7, "text": "/stato"}', TOKEN)}
    calls = _fake(monkeypatch, files)
    q = GitHubQueue(token="t", repo="o/r")
    done = tmp_path / "done.json"
    handled = []
    assert q.drain(lambda p: handled.append(p["text"]), done_file=str(done)) == (1, False)
    assert handled == ["/stato"] and not [c for c in calls if c[0] == "DELETE"]  # niente cancellato prima del salvataggio
    q.ack(json.loads(done.read_text()))
    assert [c for c in calls if c[0] == "DELETE"] == [("DELETE", "9999999999999-b.json")]  # quello illeggibile resta (per ora)
