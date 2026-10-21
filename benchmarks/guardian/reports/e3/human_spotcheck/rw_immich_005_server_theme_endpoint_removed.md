# Case

`rw_immich_005_server_theme_endpoint_removed`

## Material Passport

- 阶段：E3-B pre-system human spot check
- 证据：固定 Base/Head OpenAPI、相邻 Spec commit、commit message
- 冻结记录：`reviews/pre_system/rw_immich_005_server_theme_endpoint_removed.json`
- Ground Truth aggregate SHA256：`3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- 本 packet 没有使用任何 system evaluation 结果

## Repository

- Repository：`immich-app/immich`
- OpenAPI：`open-api/immich-openapi-specs.json`

## Base / Head Commit

- Base：`7d181f0686aa70f910bfa2f985a8d5471995b95b`
- Head：`2f8be45fe0fc2c343f6255cd3fed5c4cb3a4d4f2`

## Commit Message

`chore!: remove /api/server/theme endpoint (#27880)`

## Relevant OpenAPI Diff

```diff
 paths:
-  /server/theme:
-    get:
-      operationId: getTheme
-      responses:
-        "200":
-          content:
-            application/json:
-              schema:
-                $ref: "#/components/schemas/ServerThemeDto"

 components:
   schemas:
-    ServerThemeDto: ...
```

Head 中 `/server/theme` path 和 `ServerThemeDto` 均不存在。

## Plain-language Change

旧版本公开了读取服务器主题配置的 `GET /server/theme`。新版本把整个 endpoint 和它的 response schema 删除了。

## Compatibility Reasoning

冻结标签是 **FAIL**。

调用旧 URI 的客户端在 Head contract 中找不到该 operation。即使产品在其他位置提供了替代能力，原 method + path 没有保留，已有调用方仍需修改。

## ContractAffectedScope Evidence

- `GET /server/theme`

这是被删除的 Base operation，所以它仍属于契约意义上的受影响范围。

## RuntimeExecutableScope Evidence

空集合。

`GET /server/theme` 已不在 Head Spec 中，当前基于 Head Spec 的测试工具不能再生成并执行它。因此它进入 ContractAffectedScope，但不进入 RuntimeExecutableScope。

## Frozen Label

| Field | Frozen value |
|---|---|
| status | `confirmed` |
| confidence | `high` |
| verdict | `fail` |
| contract ops | `1` |
| executable ops | `0` |

## Reviewer Questions

- **A. Agree**：同意冻结标签与两个 scope。
- **B. Disagree**：不同意；请指出 endpoint 是否仍以相同 method + path 存在。
- **C. Unsure**：需要额外 release note 或 implementation evidence。

最值得人工检查的疑点：是否存在未体现在这份 OpenAPI 中的兼容别名或重定向。即便存在，它也不会让 Head OpenAPI 本身继续声明旧 operation。

