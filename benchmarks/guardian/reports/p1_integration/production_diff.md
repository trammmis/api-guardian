# P1-I Production Diff

## Modified production files

| File | Before SHA256 | After SHA256 |
|---|---|---|
| `app/services/guardian/scope.py` | `9c93058ec5b3d358f5dfe2ddc81415cd276fe7cf448b527124cd629e856f2787` | `0c0627bb1bb838b124fb0bc0d5fe80fd8aa2183a8dae60f73b86e5e84341b669` |
| `app/services/guardian/state.py` | `37cdd05c87f13503fc439194b4f8905687b19fa7dcc907dccd4aa93d89bb555c` | `9c8325a56554e4588c009ab26952a039f55363e62d00cfefa5424bea1ece42c8` |
| `app/services/guardian/nodes.py` | `5bb6868a9d32c1c80fda5adc5b8d5f1def9f6fac8d2408bda96fba7b22142001` | `d08232832d556134dca05df94e2c466bd1bb4b753d3d143ddf184a975bbe61c7` |
| `app/services/guardian/graph.py` | `9151a5cf7616902bb6201041b3d7c547ca737b5763601d1c7b38abce69c307fd` | `9151a5cf7616902bb6201041b3d7c547ca737b5763601d1c7b38abce69c307fd` |
| `app/services/guardian/oasdiff.py` | `f2628a647de1fe7e9bc20796f49fb2df0c673b258c6fd0d9ee58d55b00a77043` | `f2628a647de1fe7e9bc20796f49fb2df0c673b258c6fd0d9ee58d55b00a77043` |
| `app/services/guardian/schemathesis.py` | `6ab64de45e57ddb2e02dca7ad250059df1fef64b7c4add3fdbbcb5cde1af39e5` | `6ab64de45e57ddb2e02dca7ad250059df1fef64b7c4add3fdbbcb5cde1af39e5` |
| `app/services/guardian/models.py` | `b75110edb4ca89bf4a012a7831078b209983145e96915ade767042e8b92d925c` | `b75110edb4ca89bf4a012a7831078b209983145e96915ade767042e8b92d925c` |
| `app/services/guardian/security.py` | `0c7e2ce96e0b8e7e8573085f72d13ae2161c6874c1db653a323087a860d01527` | `0c7e2ce96e0b8e7e8573085f72d13ae2161c6874c1db653a323087a860d01527` |
| `app/services/guardian/errors.py` | `137cfd87b19484cb5fed3d4f3763e35e92f07be143f7b036ccfe5b65f2fcbce4` | `137cfd87b19484cb5fed3d4f3763e35e92f07be143f7b036ccfe5b65f2fcbce4` |

## Behavioral change

1. `prepare_scan` 额外保留白名单 `base_operations`，不保存完整 OpenAPI。
2. `GuardianState` 与 checkpoint rehydration 增加 `base_operations: tuple[OperationRef, ...]`。
3. `select_test_scope` 按 change 解析：精确 Head 映射优先；只有两个 oasdiff 1.28.0 冻结 removal rule ID 且 Base/Head 结构证明删除时，贡献空执行范围；其他 unresolved change 仍 fallback。

## Explicit non-changes

- Contract normalization、Contract/Regression/Final Verdict 未改。
- oasdiff、Schemathesis、Graph topology、LLM、数据库 schema 未改。
- 没有 component dependency resolver 或模糊文本匹配。
