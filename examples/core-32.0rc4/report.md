# Bitcoin Core consensus comparison

**Result:** agreement

- Specification commit: `30c6d3fc807236a36b890a48c2d296e1149f971f`
- Core baseline commit: `f0de87f040621468b956f01f526a43a010310501`
- Run started: 2026-11-10T04:48:00.709859+00:00
- Platform: Darwin 27.0.0 arm64
- Baseline: v32.1.0, SHA256 `58f9e3b5b8cf0cecd268cc6bf02993f3244e1a93fe8d67949ced6b52c2fdd9fb`
- Candidate: v32.0.0rc4, SHA256 `906f5a6afc869e12b1d41d33715efc29d42cf84cd8a5d233505190608a44b458`

Selected 20 of 20 frozen scenarios.

Compared 11,801 block and header dispositions with 0 differences. The candidate completed 5,275 normative tip checks and 2,287 normative UTXO checks.

| Scenario | Baseline checks | Candidate checks | Decisions compared | Decision differences | Baseline checkpoint differences | Candidate checkpoint differences | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| feature_bip68_sequence.node0 | 60 | 60 | 446 | 0 | 0 | 0 | 0 |
| feature_bip68_sequence.node1 | 26 | 26 | 445 | 0 | 0 | 0 | 0 |
| feature_block.node0 | 168 | 168 | 346 | 0 | 0 | 0 | 0 |
| feature_block_reorg.node0 | 176 | 176 | 2529 | 0 | 0 | 0 | 0 |
| feature_cltv.node0 | 16 | 16 | 117 | 0 | 0 | 0 | 0 |
| feature_csv_activation.node0 | 172 | 172 | 679 | 0 | 0 | 0 | 0 |
| feature_dersig.node0 | 10 | 10 | 104 | 0 | 0 | 0 | 0 |
| feature_nulldummy.node0 | 14 | 14 | 107 | 0 | 0 | 0 | 0 |
| feature_segwit.node0 | 24 | 24 | 171 | 0 | 0 | 0 | 0 |
| feature_segwit.node1 | 24 | 24 | 171 | 0 | 0 | 0 | 0 |
| feature_segwit.node2 | 24 | 24 | 171 | 0 | 0 | 0 | 0 |
| feature_spec_pitfalls.node0 | 55 | 55 | 235 | 0 | 0 | 0 | 0 |
| feature_spec_pitfalls.node1 | 11 | 11 | 15 | 0 | 0 | 0 | 0 |
| feature_spec_pitfalls.node2 | 292 | 292 | 147 | 0 | 0 | 0 | 0 |
| feature_taproot.node0 | 5511 | 5511 | 4142 | 0 | 0 | 0 | 0 |
| mining_basic.node0 | 703 | 703 | 598 | 0 | 0 | 0 | 0 |
| mining_basic.node1 | 6 | 6 | 667 | 0 | 0 | 0 | 0 |
| p2p_invalid_block.node0 | 15 | 15 | 115 | 0 | 0 | 0 | 0 |
| p2p_segwit.node0 | 173 | 173 | 312 | 0 | 0 | 0 | 0 |
| p2p_segwit.node1 | 82 | 82 | 284 | 0 | 0 | 0 | 0 |

Checks count normative tip and UTXO comparisons. Decisions compare the two binaries' observed dispositions for original block and header events. The pinned RPC harness models some peer handling. Secondary chain tips and nonnormative observations are diagnostics. report.json contains the event indices, differences and errors.
