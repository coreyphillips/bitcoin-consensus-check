"""Compare two Bitcoin Core binaries against the same frozen regtest scenarios."""

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from chain_replay import run_chain

SPEC_COMMIT = "30c6d3fc807236a36b890a48c2d296e1149f971f"
CORE_COMMIT = "f0de87f040621468b956f01f526a43a010310501"
CORPUS = Path("conformance/vectors/blocks")
RECORDER = Path("conformance/recorder")
EXIT = {"agreement": 0, "control matched": 0, "differences": 1, "inconclusive": 2}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as reader:
        for part in iter(lambda: reader.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def snapshot(repo, dest):
    # Read from committed objects so local modifications cannot change the baseline.
    archive = dest / "spec.tar"
    members = [str(CORPUS)] + [str(RECORDER / name) for name in (
        "pins.json", "core_chain_harness.py", "replay.py", "blocklib.py")]
    with archive.open("wb") as out:
        subprocess.run(["git", "--no-replace-objects", "-C", str(repo), "archive", "--format=tar",
                        SPEC_COMMIT, "--", *members], stdout=out, check=True)
    with tarfile.open(archive) as tar:
        for member in tar:
            target = dest / member.name
            if dest not in target.resolve().parents:
                raise ValueError("unexpected archive path")
            if member.isdir():
                target.mkdir(exist_ok=True, parents=True)
            elif member.isfile():
                target.parent.mkdir(exist_ok=True, parents=True)
                with target.open("xb") as write, tar.extractfile(member) as read:
                    shutil.copyfileobj(read, write)
            else:
                raise ValueError(f"unexpected archive member {member.name}")
    pin = json.loads((dest / RECORDER / "pins.json").read_text())["core"]
    if (pin["commit"], pin["version"]) != (CORE_COMMIT, "v32.1"):
        raise ValueError("spec baseline does not match Core v32.1")


def identify(binary, datadir):
    datadir.mkdir()
    proc = subprocess.run([str(binary), "-version", f"-datadir={datadir}"],
                          text=True, capture_output=True, timeout=30, check=True)
    match = re.search(r"^Bitcoin Core daemon version (v[\w.+-]+)(?:\s|$)", proc.stdout, re.MULTILINE)
    if not match:
        raise ValueError(f"{binary.name} did not report a Bitcoin Core daemon version")
    return {"version": match.group(1), "sha256": digest(binary)}


def scenario_results(baseline, candidate):
    rows = {}
    status = "agreement"
    for name, control in baseline.items():
        other = candidate[name]
        left, right = control["decisions"], other["decisions"]
        shared = left.keys() & right.keys()
        rows[name] = row = {
            "baseline": {k: v for k, v in control.items() if k != "decisions"},
            "candidate": {k: v for k, v in other.items() if k != "decisions"},
            "decisions_compared": len(shared),
            "decision_differences": [
                {"event": int(i), "baseline": left[i], "candidate": right[i]}
                for i in sorted(shared, key=int) if left[i] != right[i]],
        }
        if control["errors"] or other["errors"] or control["differences"] or not control["checks"]:
            status = "inconclusive"
        elif control["checks"] != other["checks"] or left.keys() != right.keys():
            row["coverage_error"] = "the binaries did not complete the same checks and events"
            status = "inconclusive"
        elif status != "inconclusive" and (other["differences"] or row["decision_differences"]):
            status = "differences"
    return status, rows


def render(report):
    lines = ["# Bitcoin Core consensus comparison", "", f"**Result:** {report['status']}", "",
             f"- Specification commit: `{SPEC_COMMIT}`", f"- Core baseline commit: `{CORE_COMMIT}`",
             f"- Run started: {report['started_utc']}", f"- Platform: {report['platform']}"]
    for role in ("baseline", "candidate"):
        if role in report:
            entry = report[role]
            lines.append(f"- {role.capitalize()}: {entry['version']}, SHA256 `{entry['sha256']}`")
    if report.get("error"):
        lines.extend(["", "Run error: " + report["error"]])
    if "available" in report:
        lines.extend(["", f"Selected {len(report['selected'])} of {report['available']} frozen scenarios."])
    if "rows" in report:
        totals = report["totals"]
        lines.extend(["", f"Compared {totals['decisions']:,} block and header dispositions "
                      f"with {totals['decision_differences']:,} differences. The candidate completed "
                      f"{totals['candidate_tip']:,} normative tip checks and "
                      f"{totals['candidate_utxo']:,} normative UTXO checks."])
        lines.extend(["", "| Scenario | Baseline checks | Candidate checks | Decisions compared | Decision differences | Baseline checkpoint differences | Candidate checkpoint differences | Errors |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|"])
        for name, row in report["rows"].items():
            base, cand = row["baseline"], row["candidate"]
            errors = len(base["errors"]) + len(cand["errors"]) + int("coverage_error" in row)
            lines.append(f"| {name.removesuffix('.jsonl.gz')} | {base['checks']} | {cand['checks']} | "
                         f"{row['decisions_compared']} | {len(row['decision_differences'])} | "
                         f"{len(base['differences'])} | {len(cand['differences'])} | {errors} |")
    lines.extend(["", "Checks count normative tip and UTXO comparisons. Decisions compare the two binaries' "
                  "observed dispositions for original block and header events. The pinned RPC harness models "
                  "some peer handling. Secondary chain tips and nonnormative observations are diagnostics. "
                  "report.json contains the event indices, differences and errors.", ""])
    return "\n".join(lines)


def compare(args, report, tmp):
    spec = tmp / "spec"
    spec.mkdir()
    snapshot(args.spec, spec)
    files = sorted((spec / CORPUS).glob("*.jsonl.gz"))
    known = {p.name.removesuffix(".jsonl.gz"): p for p in files}
    if not files:
        raise ValueError("empty block corpus")
    if args.scenario:
        if len(set(args.scenario)) != len(args.scenario) or set(args.scenario) - known.keys():
            raise ValueError("choose distinct scenarios from: " + ", ".join(known))
        files = [known[name] for name in sorted(args.scenario)]
    report.update(available=len(known), selected=[p.name for p in files])
    for role in ("baseline", "candidate"):
        report[role] = identify(getattr(args, role), tmp / role)
    if report["baseline"]["version"] not in ("v32.1", "v32.1.0"):
        raise ValueError("baseline must identify itself as the pinned Core v32.1 release")
    identical = report["baseline"]["sha256"] == report["candidate"]["sha256"]
    if identical != args.self_check:
        raise ValueError("identical binaries require --self-check, which only accepts identical binaries")
    results = {}
    for role in ("baseline", "candidate"):
        print(f"Replaying {len(files)} scenarios on {role} {report[role]['version']}...", flush=True)
        results[role] = run_chain(spec, getattr(args, role), files, args.jobs)
    status, rows = scenario_results(results["baseline"], results["candidate"])
    totals = {"decisions": sum(r["decisions_compared"] for r in rows.values()),
              "decision_differences": sum(len(r["decision_differences"]) for r in rows.values())}
    for role in ("baseline", "candidate"):
        for field in ("tip", "utxo"):
            totals[f"{role}_{field}"] = sum(r[role]["checkpoint_counts"][field] for r in rows.values())
    report.update(status="control matched" if identical and status == "agreement" else status,
                  rows=rows, totals=totals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="local bitcoin-consensus-spec Git checkout")
    parser.add_argument("--baseline", type=Path, required=True, help="verified Core v32.1 bitcoind")
    parser.add_argument("--candidate", type=Path, required=True, help="second bitcoind to check")
    parser.add_argument("--output", type=Path, required=True, help="a new report directory")
    parser.add_argument("--scenario", action="append", help="exact scenario name without .jsonl.gz, repeatable")
    parser.add_argument("--jobs", type=int, default=2, help="concurrent scenarios per binary (default 2)")
    parser.add_argument("--self-check", action="store_true", help="explicitly exercise a single binary in both roles")
    args = parser.parse_args()
    if args.jobs <= 0:
        parser.error("--jobs must be positive")
    args.baseline, args.candidate = args.baseline.resolve(), args.candidate.resolve()
    output = args.output.absolute()
    if output.exists():
        parser.error("--output must be a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"format": 1, "status": "inconclusive", "spec_commit": SPEC_COMMIT,
              "baseline_core_commit": CORE_COMMIT, "started_utc": datetime.now(timezone.utc).isoformat(),
              "platform": f"{platform.system()} {platform.release()} {platform.machine()}"}
    with tempfile.TemporaryDirectory(prefix=".compare-", dir=output.parent) as directory:
        work = Path(directory).resolve()
        try:
            compare(args, report, work)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            report["error"] = str(exc)
        staged = work / "report"
        staged.mkdir()
        (staged / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
        (staged / "report.md").write_text(render(report), encoding="utf8")
        staged.rename(output)
    print(f"{report['status']}: {output / 'report.md'}")
    return EXIT[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
