"""Refus 429 d'Azure OpenAI : attente puis nouvel essai, sans faire échouer le traitement."""

import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

from pseudo.detection import llm as llm_module
from pseudo.detection.llm import AzureOpenAIJsonAgent


class RateLimited(Exception):
    """Imite openai.RateLimitError : status_code 429 et en-têtes de la réponse."""

    def __init__(self, headers=None):
        super().__init__("429")
        self.status_code = 429
        self.response = NS(headers=headers or {})


def ok_response():
    """Réponse Chat Completions minimale."""
    message = NS(content=json.dumps({"entities": []}), refusal=None)
    return NS(choices=[NS(message=message, finish_reason="stop")])


class FakeClient:
    """Client Azure OpenAI simulé : lève les erreurs données, puis répond normalement."""

    def __init__(self, errors=(), delay=0.0):
        self.errors = list(errors)
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.delay = delay
        self._lock = threading.Lock()
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        """Appel simulé : compte les appels simultanés."""
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.errors:
                raise self.errors.pop(0)
            return ok_response()
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def sleeps(monkeypatch):
    """Remplace time.sleep du module : note les attentes sans attendre."""
    waits = []
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: waits.append(s))
    return waits


def run(agent):
    """Un appel ordinaire de l'agent."""
    return agent.run("prompt", "texte", {"type": "object"})


def test_429_waits_the_delay_given_by_azure_then_succeeds(sleeps):
    client = FakeClient([RateLimited({"retry-after": "7"}), RateLimited({"retry-after-ms": "2500"})])
    agent = AzureOpenAIJsonAgent(client, "pseudo-llm")
    assert run(agent) == {"entities": []}
    assert client.calls == 3
    assert 7 <= sleeps[0] <= 10 and 2.5 <= sleeps[1] <= 5.5  # délai d'Azure + un peu de hasard


def test_429_without_header_waits_longer_each_time(sleeps):
    client = FakeClient([RateLimited(), RateLimited(), RateLimited()])
    assert run(AzureOpenAIJsonAgent(client, "pseudo-llm")) == {"entities": []}
    assert [round(s // 5) for s in sleeps] == [1, 2, 4]  # 5 s, 10 s, 20 s (+ hasard)


def test_429_gives_up_after_the_maximum_wait(sleeps):
    client = FakeClient([RateLimited({"retry-after": "60"})] * 10)
    agent = AzureOpenAIJsonAgent(client, "pseudo-llm", rate_limit_max_wait=100)
    with pytest.raises(RateLimited):
        run(agent)
    assert len(sleeps) == 1  # un 2e délai de 60 s dépasserait les 100 s autorisées


def test_other_errors_are_not_retried(sleeps):
    error = RuntimeError("500")
    client = FakeClient([error])
    with pytest.raises(RuntimeError):
        run(AzureOpenAIJsonAgent(client, "pseudo-llm"))
    assert client.calls == 1 and sleeps == []


def test_concurrent_calls_are_limited_per_instance():
    client = FakeClient(delay=0.05)
    agent = AzureOpenAIJsonAgent(client, "pseudo-llm", max_concurrent_calls=2)
    threads = [threading.Thread(target=run, args=(agent,)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert client.calls == 6 and client.max_active == 2
