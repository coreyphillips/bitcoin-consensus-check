"""Replay frozen BCS block scenarios against a local Bitcoin Core binary."""

import gzip
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SCENARIO_TIMEOUT = 3600
HEADER_OUTCOMES = {"accepted", "orphan", "invalid", "temporary"}
BLOCK_OUTCOMES = HEADER_OUTCOMES | {"duplicate", "mutated", "inconclusive"}


def empty_result():
    return {"errors": [], "checks": 0, "checkpoint_counts": {"tip": 0, "utxo": 0},
            "differences": [], "diagnostics": {}, "decisions": {}}


def verify_counts(meta, events):
    if meta.get("format") != "bcs-block-corpus" or meta.get("format_version") != 1:
        raise ValueError("unsupported corpus format or version")
    if not events or events[0]["type"] != "reset" or events[-1]["type"] != "check":
        raise ValueError("expected initial reset and final checkpoint")
    kinds = Counter(event["type"] for event in events)
    checks = [e for e in events if e["type"] == "check"]
    if any(type(e["normative"]) is not bool for e in checks):
        raise ValueError("malformed normative marker")
    counts = {"events": len(events), "blocks": kinds["block"], "headers": kinds["headers"],
              "invalidate": kinds["invalidate"], "restart": kinds["restart"], "checks": len(checks),
              "normative_checks": sum(e["normative"] for e in checks)}
    if not counts["normative_checks"] or any(meta["counts"].get(k) != v for k, v in counts.items()):
        raise ValueError(f"corpus counts disagree or no normative checkpoints: {counts}")


def request_for(event, base, upstream):
    if event["type"] == "block":
        if "blob" in event:
            blob = (base / event["blob"]).resolve()
            if base.resolve() not in blob.parents:
                raise ValueError("block blob outside the corpus")
            with gzip.open(blob, "rb") as reader:
                raw = reader.read()
            if len(raw) != event["size"] or hashlib.sha256(raw).hexdigest() != event["sha256"]:
                raise ValueError("block blob size or digest mismatch")
            event = dict(event, hex=raw.hex())
        else:
            raw = bytes.fromhex(event["hex"])
        header_digest = hashlib.sha256(hashlib.sha256(raw[:80]).digest()).digest()[::-1].hex()
        if len(raw) < 80 or header_digest != event["hash"]:
            raise ValueError("block header hash mismatch")
    return upstream.event_request(event, str(base))


def apply_event(harness, request):
    reply = harness.handle(request)
    op = request["op"]
    if not isinstance(reply, dict) or reply.get("ok") is False:
        raise ValueError(f"{op} did not return a usable response: {reply}")
    if op == "chain_invalidate":
        if "tip" not in reply or "height" not in reply:
            raise ValueError("malformed chain_invalidate response")
        return None
    outcome = reply.get("result")
    allowed = HEADER_OUTCOMES if op == "chain_process_headers" else BLOCK_OUTCOMES
    if outcome not in allowed:
        raise ValueError(f"unexpected {op} disposition: {outcome!r}")
    if reply.get("reason") == "unrequested-header-only":
        return "header-only"
    return outcome


def compare_check(harness, event, index, result):
    normative = event["normative"]
    tip = harness.handle({"op": "chain_tip"})
    expected = {k: event[k] for k in ("tip", "height")}
    actual = {k: tip[k] for k in expected}
    diagnostics = result["diagnostics"]

    def compare(field, want, got, required):
        if required:
            result["checks"] += 1
            result["checkpoint_counts"][field] += 1
            if want != got:
                result["differences"].append({"event": index, "check": event["n"], "field": field,
                                              "expected": want, "actual": got})
        else:
            label = field + ("_matched" if want == got else "_different")
            diagnostics[label] = diagnostics.get(label, 0) + 1

    compare("tip", expected, actual, normative)
    if "utxo" in event:
        response = harness.handle({"op": "utxo_hash"})
        keys = ("hash_serialized_3", "txouts")
        compare("utxo", {k: event["utxo"][k] for k in keys}, {k: response[k] for k in keys}, normative)
    if "tips" in event:
        def branches(tips):
            return sorted((t["hash"], t["height"], t["status"]) for t in tips)
        compare("chain_tips", branches(event["tips"]), branches(harness.handle({"op": "chain_tips"})["tips"]), False)


def replay_events(path, meta, events, harness, upstream, result):
    verify_counts(meta, events)
    prefix = []
    for index, event in enumerate(events):
        kind = event["type"]
        try:
            if kind in ("reset", "restart"):
                if kind == "reset":
                    network, genesis = event["network"], event["genesis"]
                    prefix = []
                reply = harness.handle({"op": "chain_reset", "network": network, "options": event["options"]})
                if reply["genesis"] != genesis:
                    raise ValueError("unexpected genesis")
                for earlier in prefix:
                    apply_event(harness, request_for(earlier, path.parent, upstream))
            elif kind in ("block", "headers", "invalidate"):
                disposition = apply_event(harness, request_for(event, path.parent, upstream))
                if disposition is not None:
                    result["decisions"][str(index)] = disposition
                prefix.append(event)
            elif kind == "check":
                compare_check(harness, event, index, result)
            else:
                raise ValueError(f"unknown event type {kind!r}")
        except Exception as exc:
            raise RuntimeError(f"event {index} ({kind}): {exc}") from exc


def run_worker(spec_dir, path):
    # Import the very same harness and event mapper used to verify the upstream corpus.
    sys.path.insert(0, str(spec_dir / "conformance" / "recorder"))
    import core_chain_harness as core
    import replay as upstream

    class CheckedNode(core.Node):
        def classify(self, submit_result):
            # Preserve BIP22's inconclusive reply when comparing two binaries.
            if submit_result == "inconclusive":
                return "inconclusive", None
            return super().classify(submit_result)

        def call(self, method, *params):
            try:
                reply = super().call(method, *params)
            except core.RPCError as exc:
                # submitheader signals invalid headers with -25. Core can signal
                # undecodable block/header bytes with -22. Other RPC failures
                # must not be relabeled as a negative consensus decision.
                allowed = {"submitheader": (-22, -25), "submitblock": (-22,)}
                if method in allowed and exc.code not in allowed[method]:
                    raise RuntimeError(f"{method} RPC error {exc.code}: {exc.message}") from exc
                raise
            if method == "submitblock" and reply is not None and not isinstance(reply, str):
                raise ValueError(f"unexpected submitblock response: {reply!r}")
            if method == "submitheader" and reply is not None:
                raise ValueError(f"unexpected submitheader response: {reply!r}")
            return reply

    core.Node = CheckedNode
    result = empty_result()
    harness = core.Harness()
    try:
        meta, events = upstream.load_corpus(str(path))
        replay_events(path, meta, events, harness, upstream, result)
    except Exception as exc:
        result["errors"].append(str(exc))
    finally:
        if harness.node is not None:
            harness.node.stop()
    return result


def run_scenario(spec_dir, binary, path):
    result = empty_result()
    try:
        with tempfile.TemporaryDirectory(prefix="consensus-chain-") as work:
            env = dict(os.environ, TMPDIR=work, BCS_BITCOIND=str(binary.resolve()), PYTHONDONTWRITEBYTECODE="1")
            env.pop("BCS_HARNESS_KEEP", None)
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(spec_dir.resolve()),
                                     str(path.resolve())], env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(timeout=SCENARIO_TIMEOUT)
                if proc.returncode:
                    raise RuntimeError(f"worker exited {proc.returncode}: {stderr[-2000:].decode(errors='replace')}")
                result = json.loads(stdout)
            finally:
                # Also reap daemon children if their constructor failed before
                # the upstream harness took ownership of the Node.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
    except Exception as exc:
        result["errors"].append(str(exc))
    return result


def run_chain(spec_dir: Path, binary: Path, scenarios: list[Path], jobs: int) -> dict:
    if not scenarios or jobs < 1 or len({p.name for p in scenarios}) != len(scenarios):
        raise ValueError("select distinct scenario names and a positive number of jobs")
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = pool.map(lambda path: run_scenario(spec_dir, binary, path), scenarios)
        return {path.name: result for path, result in zip(scenarios, results)}


if __name__ == "__main__":
    print(json.dumps(run_worker(Path(sys.argv[1]), Path(sys.argv[2]))))
