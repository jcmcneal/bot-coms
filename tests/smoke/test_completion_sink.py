from __future__ import annotations

import json
import sys

from bot_coms import Client, Worker
from bot_coms.atomic import read_json
from bot_coms.notify import completion_sink_argv, source_argv


class _ZeroRng:
    def uniform(self, _a: float, _b: float) -> float:
        return 0.0


def _response_to_b(
    a: Client,
    b: Client,
    *,
    headers: dict[str, str] | None = None,
    correlation_id: str = "async-child",
):
    request = b.send(
        "a",
        "request",
        {"task": "delegated"},
        reply_to="b",
        correlation_id=correlation_id,
        headers=headers,
    )
    claimed = a.claim(msg_id=request.id)
    assert claimed is not None
    a.ack(claimed, result={"ok": True, "detail": "finished"})
    result = b.poll_result(correlation_id)
    assert result is not None
    response = b.claim(msg_id=result.id)
    assert response is not None
    assert response.envelope.type == "response"
    return response


def _write_recorder(tmp_path):
    recorder = tmp_path / "completion.json"
    script = tmp_path / "record_completion.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({\n"
        "    'args': sys.argv[2:],\n"
        "    'envelope': json.load(sys.stdin),\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return script, recorder


def test_completion_sink_delivers_full_response_and_acks_after_success(
    clients, tmp_path, monkeypatch
) -> None:
    a, b = clients
    script, recorder = _write_recorder(tmp_path)
    source = f"custom:group 7;$(touch {tmp_path / 'should-not-exist'})"
    monkeypatch.setenv(
        "BOT_COMS_COMPLETION_SINK_ARGV",
        json.dumps(
            [
                sys.executable,
                str(script),
                str(recorder),
                "{route}",
                "{source}",
            ]
        ),
    )
    response = _response_to_b(
        a,
        b,
        headers={"source": source, "trace": "trace-123"},
        correlation_id="corr-full-envelope",
    )
    expected = response.envelope.to_dict()

    assert Worker(b, completion_sink_argv).handle_one(response)

    recorded = json.loads(recorder.read_text(encoding="utf-8"))
    assert recorded == {
        "args": ["b", source],
        "envelope": expected,
    }
    assert recorded["envelope"]["correlation_id"] == "corr-full-envelope"
    assert recorded["envelope"]["headers"] == {
        "source": source,
        "trace": "trace-123",
    }
    assert (b.paths.acked / f"{response.envelope.id}.json").is_file()
    assert not (tmp_path / "should-not-exist").exists()


def test_completion_sink_skips_non_responses_and_internal_responses(
    clients, tmp_path
) -> None:
    a, b = clients
    request = a.send("b", "request", {"task": "not-a-completion"})
    claimed_request = b.claim(msg_id=request.id)
    assert claimed_request is not None

    worker = Worker(b, completion_sink_argv)
    assert not worker.handle_one(claimed_request)
    assert (b.paths.inbox / f"{request.id}.json").is_file()

    internal = _response_to_b(
        a,
        b,
        headers={"source": "custom:group-1", "delivery": "internal"},
        correlation_id="corr-internal",
    )
    assert not worker.handle_one(internal)
    assert (b.paths.inbox / f"{internal.envelope.id}.json").is_file()
    assert not list(tmp_path.glob("completion*"))


def test_completion_sink_failure_retries_then_acks(clients, tmp_path, monkeypatch) -> None:
    a, b = clients
    b.config.retry.rng = _ZeroRng()  # type: ignore[assignment]
    counter = tmp_path / "attempts"
    script = tmp_path / "fail_once.py"
    script.write_text(
        "import pathlib, sys\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "count = int(path.read_text() if path.exists() else '0') + 1\n"
        "path.write_text(str(count))\n"
        "sys.stdin.read()\n"
        "raise SystemExit(17 if count == 1 else 0)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "BOT_COMS_COMPLETION_SINK_ARGV",
        json.dumps([sys.executable, str(script), str(counter)]),
    )
    response = _response_to_b(a, b, correlation_id="corr-retry-success")
    worker = Worker(b, completion_sink_argv)

    assert worker.handle_one(response)
    assert counter.read_text(encoding="utf-8") == "1"
    assert not (b.paths.acked / f"{response.envelope.id}.json").exists()
    retried_data = read_json(b.paths.inbox / f"{response.envelope.id}.json")
    assert retried_data["attempt"] == 1

    retried = b.claim(msg_id=response.envelope.id)
    assert retried is not None
    assert worker.handle_one(retried)
    assert counter.read_text(encoding="utf-8") == "2"
    assert (b.paths.acked / f"{response.envelope.id}.json").is_file()


def test_completion_sink_failures_reach_worker_dead_letter(
    clients, tmp_path, monkeypatch
) -> None:
    a, b = clients
    b.config.retry.max_attempts = 2
    b.config.retry.rng = _ZeroRng()  # type: ignore[assignment]
    script = tmp_path / "always_fail.py"
    script.write_text(
        "import sys\nsys.stdin.read()\nraise SystemExit(23)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "BOT_COMS_COMPLETION_SINK_ARGV",
        json.dumps([sys.executable, str(script)]),
    )
    response = _response_to_b(a, b, correlation_id="corr-dead-letter")
    worker = Worker(b, completion_sink_argv)

    assert worker.handle_one(response)
    retried = b.claim(msg_id=response.envelope.id)
    assert retried is not None
    assert worker.handle_one(retried)

    dead = read_json(b.paths.dead_letter / f"{response.envelope.id}.json")
    assert dead["reason"] == "max_attempts"
    assert dead["envelope"]["attempt"] == 2
    assert "completion sink argv failed (exit 23)" in dead["last_error"]
    assert not (b.paths.acked / f"{response.envelope.id}.json").exists()


def test_legacy_source_argv_remains_payload_only(
    clients, tmp_path, monkeypatch
) -> None:
    a, b = clients
    _script, recorder = _write_recorder(tmp_path)
    legacy_script = tmp_path / "record_legacy.py"
    legacy_script.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({\n"
        "    'source': sys.argv[2],\n"
        "    'payload': json.load(sys.stdin),\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "BOT_COMS_NOTIFY_ARGV",
        json.dumps(
            [sys.executable, str(legacy_script), str(recorder), "{source}"]
        ),
    )
    response = _response_to_b(
        a,
        b,
        headers={"source": "discord:legacy-group"},
        correlation_id="corr-legacy",
    )

    assert Worker(b, source_argv).handle_one(response)
    assert json.loads(recorder.read_text(encoding="utf-8")) == {
        "source": "discord:legacy-group",
        "payload": {"ok": True, "detail": "finished"},
    }
