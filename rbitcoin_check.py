"""Run the pinned spec's existing vector suites against native rbitcoin code."""

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from compare import CORE_COMMIT, SPEC_COMMIT, digest


SCRIPT_SUITES = ("script-tests", "script-assets")
VECTOR_SUITES = ("script-tests", "tx-valid", "tx-invalid", "sighash", "bip341", "bip340",
                 "script-assets", "custom")
ADAPTER = Path(__file__).resolve().parent / "adapters" / "rbitcoin"
COLUMNS = ("checks", "passed", "failed", "diagnostic_differences", "diagnostics_checked",
           "unsupported")


def revision(repo, inputs, pin=None):
    git = ["git", "--no-replace-objects", "-C", repo]
    rev = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    if pin is not None and rev != pin:
        raise ValueError(f"spec checkout must be at {pin}, found {rev}")
    changes = subprocess.check_output(
        [*git, "status", "--porcelain", "--untracked-files=all", "--", *inputs], text=True)
    if changes.strip():
        raise ValueError(f"modified or untracked build or fixture inputs in {repo.name}")
    return rev


def build(manifest, binary, targets, offline, package=None, env=None):
    command = ["cargo", "build", "--manifest-path", manifest, "--release", "--locked",
               "--target-dir", targets]
    if offline:
        command.append("--offline")
    if package:
        command.extend(["-p", package])
    subprocess.run(command, cwd=manifest.parent, env=env, check=True, stdout=sys.stderr)
    return targets / "release" / binary


def check_info(harness, rev):
    result = subprocess.run([harness], input='{"id":1,"op":"info"}\n', capture_output=True,
                            check=True, text=True, timeout=30)
    info = json.loads(result.stdout)
    version = info.get("version", "")
    if (info.get("id") != 1 or info.get("ok") is not True
            or not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{7,40}", version)
            or not rev.startswith(version) or "rbitcoin" not in info.get("name", "").lower()):
        raise ValueError("rbitcoin harness info must identify its build using the checkout commit")
    return {k: info[k] for k in ("name", "version", "spec_version", "ops")}


def score(runner, harness, spec, suites):
    cmd = [runner, "--suite", ",".join(suites), "--impl", shlex.quote(str(harness)),
           "--vectors", "conformance/vectors", "--verbose"]
    rows, failures, skipped_rows = {}, [], []
    status = None
    # The upstream verbose output can include many thousands of diagnostic error names.
    # Keep their counters and the first few primary failures, independently of spelling.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        result = subprocess.run(cmd, stdout=log, cwd=spec)
        log.seek(0)
        for line in log:
            fields = line.split()
            if line.startswith("FAIL ") and len(failures) < 12:
                failures.append(line.strip())
            elif line.startswith("RESULT: "):
                status = line.strip().split()[-1]
            elif line.startswith("  ") and " x " in line and ":" in line:
                skipped_rows.append(line.strip())
            elif fields and fields[0] in suites:
                suite = fields[0]
                if suite in rows:
                    raise ValueError("duplicate suite in runner output")
                if len(fields) == 9 and all(s.isdecimal() for s in fields[2:8]):
                    rows[suite] = dict(zip(COLUMNS, (int(value) for value in fields[2:8])))
                else:
                    rows[suite] = {"error": " ".join(fields[2:])}
            elif line.strip().startswith("error:") and rows:
                rows[next(reversed(rows))]["error"] = line.strip()
    if set(rows) != set(suites) or (status, result.returncode) not in {("PASS", 0), ("FAIL", 1)}:
        raise ValueError("runner exited without a complete, consistent summary")
    if status == "PASS" and any(not row.get("checks") or row.get("error")
                                or row["passed"] != row["checks"] or row["unsupported"]
                                for row in rows.values()):
        raise ValueError("empty or incomplete suite in passing summary")
    return {"status": status.lower(), "suites": rows, "failure_samples": failures,
            "rows_omitted_by_upstream_rule": skipped_rows, "upstream_exit": result.returncode}


def run(args, work):
    spec, rbitcoin = args.spec.resolve(), args.rbitcoin.resolve()
    # Scope this check to inputs: an unrelated untracked personal file can coexist
    # with a clean build. The runner lives in the same workspace as reference.
    revision(spec, ["Cargo.toml", "Cargo.lock", "conformance/runner", "conformance/vectors",
                    "reference", "spec", ".cargo", "rust-toolchain", "rust-toolchain.toml"], SPEC_COMMIT)
    rev = revision(rbitcoin, ["Cargo.toml", "Cargo.lock", "crates", ".cargo", "rust-toolchain.toml"])
    suites = args.suite or (VECTOR_SUITES if args.harness else SCRIPT_SUITES)
    if len(set(suites)) != len(suites):
        raise ValueError("duplicate suite")
    runner = build(spec / "Cargo.toml", "bcs-conformance", work / "runner-output",
                   args.offline, package="conformance-runner")
    if args.harness:
        harness = args.harness.resolve()
    else:
        adapter = work / "adapter"
        shutil.copytree(ADAPTER, adapter)
        (work / "rbitcoin").symlink_to(rbitcoin, target_is_directory=True)
        harness = build(adapter / "Cargo.toml", "rbitcoin-script-adapter", work / "adapter-output",
                        args.offline, env=dict(os.environ, RBITCOIN_REV=rev))
    info = check_info(harness, rev)
    report = {"kind": "rbitcoin-vector-check", "spec_revision": SPEC_COMMIT,
              "core_fixture_baseline": CORE_COMMIT, "rbitcoin_revision": rev,
              "platform": f"{platform.system()} {platform.machine()}",
              "rustc": subprocess.check_output(["rustc", "--version"], text=True).strip(),
              "adapter": "external" if args.harness else "bundled",
              "adapter_sha256": digest(harness), "runner_sha256": digest(runner), "adapter_info": info,
              "not_run": [s for s in (*VECTOR_SUITES, "corpus", "headers-live", "interop")
                          if s not in suites]}
    report.update(score(runner, harness, spec, suites))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path, help="pinned bitcoin-consensus-spec checkout")
    parser.add_argument("--rbitcoin", required=True, type=Path, help="rbitcoin source checkout")
    parser.add_argument("--harness", type=Path, help="prebuilt rbitcoin protocol v1 vector adapter")
    parser.add_argument("--suite", action="append", choices=VECTOR_SUITES, help="repeat to select suites")
    parser.add_argument("--offline", action="store_true", help="use only locally cached Cargo dependencies")
    parser.add_argument("--report", type=Path, help="write JSON here (defaults to stdout)")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="rbitcoin-check-") as work:
            report = run(args, Path(work))
        rc = report["upstream_exit"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        report = {"kind": "rbitcoin-vector-check", "status": "inconclusive", "error": str(error)}
        rc = 2
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
        print(f"{report['status']}: {args.report}", file=sys.stderr)
    else:
        print(output, end="")
    return rc


if __name__ == "__main__":
    sys.exit(main())
