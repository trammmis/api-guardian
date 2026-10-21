# E2 元数据修正审计

正式运行 `guardian-e2-20260906T071336Z-2eacf61c` 完成后，审计发现命令行传入的 oasdiff release archive SHA256 是缩写 `cef8...`，不满足可复现性要求。

根据以下两个已有、相互一致的审计来源，完整值为：

`cef8ec7cdd32ab4ce9c188f9dbb50452a91fa36cc11c8e1dcf340b19282c3ce1`

- `docs/guardian-tool-contracts.md`
- Stage E1 `run_manifest.json`

修正范围仅为 `metadata.oasdiff_archive_sha256`：

- `raw_results.jsonl`：1320 处
- `contract_raw_results.jsonl`：60 处
- `summary.json`：1 处
- `run_manifest.json`：1 处

没有修改 duration、verdict、scope、request count、error code 或任何实验观测值。修正前后文件 SHA256：

| 文件 | 修正前 | 修正后 |
| --- | --- | --- |
| `raw_results.jsonl` | `3966c14cc5d16fb4d7d2e0a2a6294998322d88bf9188e141ca5178b4b7f45bff` | `f93f33a01a6d97f92a162eca5b41b2f11746c76f8ab7785e23d839e5f08ad196` |
| `contract_raw_results.jsonl` | `93148b5dbce809a020e0b24b47f9dc7e5942b18ab97c7a087026e552850028be` | `349023d9464935fcc6ec9006189bf59d124bf3df79f870bcbb4fddaf3aca957a` |
| `summary.json` | `094890aacaa77ecf1e8b4e3fd99e491e55f20c1bb53d745bbf333b0b20dbe03b` | `b243ce5d2c44327be65e73d30dc704d6eff658feffac227533e036e358103cde` |
| `run_manifest.json` | `3138cf412d16e3bc43ca5915945253069768e22d58c031ccab851f51f82b2d41` | `af1ae539ec8785c12b129d49a0de2000019a6feec256f2e7665f4ed88184372b` |

Harness 已增加 64 位十六进制 SHA256 校验，后续运行会拒绝缩写值。
