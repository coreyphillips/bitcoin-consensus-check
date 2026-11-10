#!/usr/bin/env python3
"""Compare Hornet's compact target and block work with two Core binaries at genesis."""

import argparse
import base64
import hashlib
import http.client
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from compare import identify


HORNET_COMMIT = "28dd9a6d66c2221a6a5c11cef937c47346435cf0"
GENESIS = {
    "main": ("000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f", "1d00ffff"),
    "signet": ("00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6", "1e0377ae"),
    "regtest": ("0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206", "207fffff"),
}
HEX256 = re.compile("[0-9a-f]{64}")
EXIT = {"agreement": 0, "differences": 1, "inconclusive": 2}


def build(hornet, compiler, work):
    # Only regular files from this exact upstream commit enter the build.
    archive = work / "hornet.tar"
    with archive.open("wb") as out:
        subprocess.run(["git", "--no-replace-objects", "-C", str(hornet), "archive", "--format=tar",
                        HORNET_COMMIT, "--", "src/hornetlib"], stdout=out, check=True, timeout=60)
    subprocess.run(["tar", "-xf", str(archive), "-C", str(work)], check=True, timeout=60)
    source = Path(__file__).resolve().parent / "adapters/hornet/compact_target.cpp"
    output = work / "compact-target"
    subprocess.run([compiler, "-std=c++23", "-O2", "-DHORNET_MAX_LOG_LEVEL=None", "-I", str(work / "src"),
                    str(source), "-o", str(output)], check=True, timeout=120)
    return output


class RPC:
    def __init__(self, datadir, network):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.cookie = datadir / ("" if network == "main" else network) / ".cookie"

    def call(self, method, *params):
        auth = base64.b64encode(self.cookie.read_bytes().strip()).decode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        try:
            conn.request("POST", "/", json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}),
                         {"Content-Type": "application/json", "Authorization": "Basic " + auth})
            response = conn.getresponse()
            data = json.loads(response.read())
            error = data.get("error")
            if isinstance(error, dict) and error.get("code") == -28:
                raise ConnectionError("Core is warming up")
            if response.status != 200 or data.get("error") or data.get("id") != 1:
                raise ValueError(f"{method} RPC failed: {data.get('error', response.status)}")
            return data["result"]
        finally:
            conn.close()


def core_header(binary, network, datadir):
    datadir.mkdir()
    rpc = RPC(datadir, network)
    args = [str(binary), "-chain=" + network, "-datadir=" + str(datadir), "-listen=0", "-listenonion=0",
            "-networkactive=0", "-dnsseed=0", "-fixedseeds=0", "-discover=0", "-connect=0", "-disablewallet",
            "-natpmp=0", "-server=1", "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", f"-rpcport={rpc.port}",
            "-printtoconsole=0"]
    with (datadir / "daemon-stderr.txt").open("wb") as log:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=log)
    try:
        deadline = time.monotonic() + 45
        while True:
            if proc.poll() is not None:
                detail = (datadir / "daemon-stderr.txt").read_text(errors="replace")[-1200:]
                raise ValueError(f"{network} bitcoind exited {proc.returncode}: {detail}")
            try:
                net = rpc.call("getnetworkinfo")
                break
            except (ConnectionError, FileNotFoundError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise ValueError(f"{network} RPC startup timed out")
                time.sleep(0.08)
        if net["networkactive"] is not False or net["connections"] != 0:
            raise ValueError("expected disabled P2P networking and zero peers")
        if rpc.call("getblockchaininfo")["chain"] != network:
            raise ValueError("unexpected chain reported by Core")
        genesis = rpc.call("getblockhash", 0)
        raw = rpc.call("getblockheader", genesis, False)
        header = rpc.call("getblockheader", genesis, True)
        if not re.fullmatch("[0-9a-f]{160}", raw):
            raise ValueError("Core returned a malformed serialized header")
        if header["hash"] != genesis or header["height"] != 0:
            raise ValueError("Core returned an inconsistent genesis identity")
        if not re.fullmatch("[0-9a-f]{8}", header["bits"]):
            raise ValueError("Core returned malformed compact bits")
        for key in ("target", "chainwork"):
            if not HEX256.fullmatch(header[key]):
                raise ValueError("Core returned malformed " + key)
        encoded = bytes.fromhex(raw)
        if hashlib.sha256(hashlib.sha256(encoded).digest()).digest()[::-1].hex() != genesis:
            raise ValueError("Core's serialized genesis disagrees with its reported block hash")
        if encoded[72:76][::-1].hex() != header["bits"]:
            raise ValueError("Core's serialized genesis disagrees with its reported bits")
        return {"hash": genesis, "header": raw, "bits": header["bits"],
                "target": header["target"], "work": header["chainwork"]}
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def hornet_values(binary, bits):
    reply = subprocess.run([str(binary), bits], check=True, capture_output=True, text=True, timeout=6)
    values = json.loads(reply.stdout)
    result = {"target": values["target"], "work": values["work"]["value"]}
    if any(not HEX256.fullmatch(v) for v in result.values()):
        raise ValueError("Hornet returned a malformed target or work value")
    return result


def judge(rows):
    if set(rows) != set(GENESIS) or len({bits for _, bits in GENESIS.values()}) != 3:
        raise ValueError("expected the three distinct genesis fixtures")
    differences = []
    for network, (genesis, bits) in GENESIS.items():
        row = rows[network]
        for role in ("baseline", "candidate"):
            core = row[role]
            required = {"hash": genesis, "bits": bits, "target": row["hornet"]["target"], "work": row["hornet"]["work"]}
            for key, expected in required.items():
                if core[key] != expected:
                    differences.append({"network": network, "implementation": role, "field": key,
                                        "expected": expected, "actual": core[key]})
        if row["baseline"]["header"] != row["candidate"]["header"]:
            differences.append({"network": network, "field": "serialized_header",
                                "baseline": row["baseline"]["header"], "candidate": row["candidate"]["header"]})
    if any(d.get("implementation") == "baseline" for d in differences):
        status = "inconclusive"
    elif differences:
        status = "differences"
    else:
        status = "agreement"
    return status, differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("hornet", "baseline", "candidate", "report"):
        parser.add_argument("--" + flag, required=True, type=Path)
    parser.add_argument("--cxx", default="clang++", help="C++23 compiler with a standard library supporting chrono format")
    args = parser.parse_args()
    if args.report.exists():
        parser.error("--report must be a new file")
    report = {"format": 1, "status": "inconclusive", "scope": "compact target and block work at genesis",
              "hornet_repo": "https://github.com/tobysharp/hornet", "hornet_branch": "tsharp/feature/script-validation",
              "hornet_commit": HORNET_COMMIT, "fixture_count": 3, "rows": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="hornet-genesis-") as temp:
            work = Path(temp).resolve()
            baseline, candidate = args.baseline.resolve(), args.candidate.resolve()
            for role, binary in (("baseline", baseline), ("candidate", candidate)):
                report[role] = identify(binary, work / role)
            if report["baseline"]["version"] not in ("v32.1", "v32.1.0"):
                raise ValueError("baseline must be Bitcoin Core v32.1")
            if report["baseline"]["sha256"] == report["candidate"]["sha256"]:
                raise ValueError("candidate and baseline binaries must be distinct")
            report["compiler"] = subprocess.check_output([args.cxx, "--version"], text=True, timeout=10).splitlines()[0]
            hornet = build(args.hornet, args.cxx, work)
            for network, (_, bits) in GENESIS.items():
                row = {"hornet": hornet_values(hornet, bits)}
                for role, binary in (("baseline", baseline), ("candidate", candidate)):
                    row[role] = core_header(binary, network, work / f"{role}-{network}")
                report["rows"][network] = row
            report["status"], report["differences"] = judge(report["rows"])
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError,
            http.client.HTTPException) as exc:
        report["error"] = str(exc)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(f"{report['status']}: {args.report}")
    return EXIT[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
