# Bitcoin consensus checks

Compare a Bitcoin Core release candidate with the previous release on frozen regtest blocks and headers. Both binaries process the same event sequence. The report shows which block and header decisions differed, whether each binary reached the recorded chain tip and UTXO set, and which checks completed. A separate adapter runs the spec's script fixtures against rbitcoin.

The baseline is Bitcoin Core v32.1 (`f0de87f040621468b956f01f526a43a010310501`). The corpus and RPC harness come from [bitcoin-consensus-spec](https://github.com/coreyphillips/bitcoin-consensus-spec/tree/30c6d3fc807236a36b890a48c2d296e1149f971f) at `30c6d3fc807236a36b890a48c2d296e1149f971f`. The Core comparison reads those files from their Git objects. Run and review a candidate before updating either pin.

## Compare Core binaries

Use Python 3.9 or newer and Git on Linux or macOS. Supply verified `bitcoind` executables and a new output directory. The checkout below also works for the rbitcoin check.

```sh
git clone --depth 1 --branch v32.1-2 https://github.com/coreyphillips/bitcoin-consensus-spec.git ../bitcoin-consensus-spec

python3 compare.py --spec ../bitcoin-consensus-spec \
  --baseline /path/to/bitcoin-32.1/bin/bitcoind \
  --candidate /path/to/candidate/bin/bitcoind \
  --output reports/candidate
```

By default the command runs all 20 scenarios. To try a single case, add `--scenario feature_cltv.node0`. Repeat the option to select additional cases. Each scenario starts an isolated regtest node with the recorded activation parameters and mock time. The node needs loopback RPC access. Use `--jobs 4` to run four scenarios concurrently. For a baseline-only control run, pass the same binary in both roles and add `--self-check`.

Open `report.md` for counts by scenario and `report.json` for observations, binary hashes, event indices, and errors. An event index counts from zero after the scenario metadata. The Core command uses these exit codes.

| Exit | Meaning |
| --- | --- |
| 0 | The baseline reproduced the required checkpoints, the candidate matched, and both reported the same dispositions on the selected cases. An explicit self-check is labeled separately. |
| 1 | The baseline reproduced the checkpoints and the candidate produced a different checkpoint or event disposition. Inspect the first difference in its scenario. |
| 2 | The comparison is inconclusive. See the baseline differences, missing results, or execution errors in the report. Argument errors also exit 2. |

The checkpoints cover tip hash and height, plus UTXO hash and output count at normative checkpoints that carry them. Side-branch status and observations at nonnormative checkpoints are diagnostic. For peer-delivered events the pinned harness models some peer handling before calling Core over RPC. The recorded dispositions describe that path. The suite covers frozen cases and does not exercise live peer behavior, mempool policy, or standalone Core script vectors. Check the activation parameters and any intended deployment changes when investigating a difference.

## Run on a release tag

The [release workflow](.github/workflows/core-release.yml) runs in a GitHub repository with Actions enabled. From the Actions tab, select **Bitcoin Core release comparison**, choose **Run workflow**, and enter a Core tag such as `v32.0rc4`. The scheduled run checks daily at 05:17 UTC for RCs newer than v32.1 whose final version has not yet been tagged. Manual dispatch can also replay historical tags supported by the CMake build.

The workflow downloads the v32.1 Linux release at the digest recorded in the frozen spec, verifies the candidate tag through GitHub and builds its target commit. It runs the block corpus and Hornet genesis checks on those binaries. Its artifact includes the reports, commits, and archive digest. A completed report for the same tag object and companion commit suppresses duplicate scheduled runs while the artifact is retained.

## Cross-check target and work with Hornet

The two Core binaries report the target and chainwork for the mainnet, signet, and regtest genesis headers. Each has a different compact target (`nBits`). At height zero, chainwork is exactly the work of that block. The adapter compiles Hornet's compact-target arithmetic from its pinned source and compares both values for each header.

```sh
git clone https://github.com/tobysharp/hornet.git ../hornet
python3 hornet_check.py --hornet ../hornet \
  --baseline /path/to/bitcoin-32.1/bin/bitcoind \
  --candidate /path/to/candidate/bin/bitcoind \
  --report reports/hornet.json
```

The helper was built with Apple Clang 21 on macOS and Clang 19 with GNU 14 headers on Ubuntu 24.04. `--cxx` selects a compiler. The repository must contain commit `28dd9a6d66c2221a6a5c11cef937c47346435cf0`. These three cases exercise target expansion and work calculation. They do not exercise the difficulty adjustment algorithm or the rest of Hornet's validator.

## Check rbitcoin

Install a Rust toolchain and use an rbitcoin checkout at the revision to test. The bundled adapter calls its native script verifier. The revision used for the example was `1ec1f626da3dd0f9d17f38881023d4bbdb2bf9da`.

```sh
git clone https://github.com/rbitcoin-org/rbitcoin.git ../rbitcoin
git -C ../rbitcoin checkout 1ec1f626da3dd0f9d17f38881023d4bbdb2bf9da
python3 rbitcoin_check.py --spec ../bitcoin-consensus-spec \
  --rbitcoin ../rbitcoin --report reports/rbitcoin.json
```

The command builds the pinned spec runner and adapter in a temporary directory with locked dependencies. Pass `--offline` when Cargo's dependency cache is populated. The bundled adapter covers two script suites. Supply `--harness /path/to/rbitcoin-harness` to run the broader vector suites with an existing conformance adapter that reports its rbitcoin revision. The JSON records the suites exercised, skipped suites, verdicts, diagnostic differences and adapter errors. A runner failure exits 1 and a setup or reporting failure exits 2. The [checks workflow](.github/workflows/checks.yml) runs the bundled adapter at the pinned rbitcoin revision on pull requests and pushes to main.

## Recorded runs

- [Core v32.1 against v32.0rc4](examples/core-32.0rc4/report.md) is a historical comparison. The RC predates the frozen baseline. Both macOS arm64 release executables were checked against signed release manifests.
- [Hornet genesis arithmetic](examples/hornet-genesis.json) compares those Core binaries with Hornet on the three genesis headers.
- [rbitcoin native script](examples/rbitcoin-native.json) records all 318,336 script verdicts for the two supported suites.
- [rbitcoin with an external adapter](examples/rbitcoin-external.json) covers eight suites. Four BIP340 primitive calls returned adapter errors for message lengths outside its API, so the runner recorded a failed suite.

## Extending coverage

Record the originating spec and revision, the rule and inputs, the activation context, the expected observation, and which code computes it. A [Hornet rule](https://hornetnode.org/spec.html), a property of [btc-lean](https://github.com/Rob1Ham/btc-lean), or a [Bitcoin Commons](https://github.com/BTCDecoded/blvm-consensus) check can supply an additional comparison when both sides implement the same operation. Count shared fixtures once. Keep the implementation and its validation engine in each report when adding nodes such as libbitcoin or bitcoin-rs.
