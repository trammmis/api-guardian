# Case

`rw_appwrite_002_migration_cutover_replaced`

## Material Passport

- 阶段：E3-B pre-system human spot check
- 证据：固定 Base/Head OpenAPI、相邻 Spec commit、commit message、fragment-local `$ref` 闭包
- 冻结记录：`reviews/pre_system/rw_appwrite_002_migration_cutover_replaced.json`
- Ground Truth aggregate SHA256：`3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- 本 packet 没有使用任何 system evaluation 结果

## Repository

- Repository：`appwrite/specs`
- OpenAPI：`specs/1.9.x/open-api3-1.9.x-server.json`
- Base bytes：`5,051,479`
- Head bytes：`5,127,762`
- 上限：`5,242,880` bytes

## Base / Head Commit

- Base：`0aff819e4744849f2cb12a4fdf58cde50d3ad8ac`
- Head：`7e76b048625236d2cfd129b4d66f899c7010f551`

## Commit Message

`chore: update API specs and SDK examples`

## Relevant OpenAPI Diff

```diff
 paths:
-  /tablesdb/{databaseId}/migrations/{migrationId}/cutover:
-    post:
-      operationId: tablesDBCutoverMigration
+  /tablesdb/{databaseId}/migrations/{migrationId}/cutovers:
+    post:
+      operationId: tablesDBCreateCutover

 components:
   schemas:
     frameworkAdapter:
       required:
-        [key, installCommand, buildCommand, outputDirectory, fallbackFile]
+        [key, installCommand, buildCommand, outputDirectory]
       properties:
         fallbackFile:
           type: string
+          nullable: true
```

`GET /sites/frameworks` 的 response 先引用 `frameworkList`，再传递引用 `frameworkAdapter`。

## Plain-language Change

旧版本使用单数路径 `/cutover`；新版本删除它并新增复数路径 `/cutovers`。同时，站点 framework response 中的 `fallbackFile` 从“必有字符串”变成“可以缺失或为 null”。

## Compatibility Reasoning

冻结标签是 **FAIL**。

新增相似路径不能保留旧 URI 的兼容性；旧客户端仍需修改请求地址。另一方面，response 从必填非空字段变为可选、可空，扩大了服务器允许返回的 shape，旧客户端可能没有处理缺失值或 null。

该记录保持冻结的 `probable / medium`，原因是 commit 同时包含多项 generated-spec 更新；本 packet 没有把它升级为 confirmed。

## ContractAffectedScope Evidence

1. `POST /tablesdb/{databaseId}/migrations/{migrationId}/cutover`：Base operation 被删除。
2. `GET /sites/frameworks`：其 response 通过 `frameworkList` 引用发生兼容性变化的 `frameworkAdapter`。

新增加的复数 `/cutovers` 是 replacement candidate，但冻结 ContractAffectedScope 聚焦 breaking facts，没有把纯新增 operation 作为受破坏的旧 operation。

## RuntimeExecutableScope Evidence

- `GET /sites/frameworks`

旧的单数 `/cutover` 已从 Head Spec 删除，不能由 Head Spec 驱动执行。`GET /sites/frameworks` 仍存在，因此保留在 executable scope。

## Frozen Label

| Field | Frozen value |
|---|---|
| status | `probable` |
| confidence | `medium` |
| verdict | `fail` |
| contract ops | `2` |
| executable ops | `1` |

## Reviewer Questions

- **A. Agree**：同意冻结标签与两个 scope。
- **B. Disagree**：不同意；请指出 path replacement 或 response nullability 的判断问题。
- **C. Unsure**：需要检查 release note 或 implementation diff。

最值得人工检查的疑点：`frameworkAdapter.fallbackFile` 从 required/non-null 变为 optional/nullable，是否应按本项目的 response-consumer compatibility convention 判为 breaking。

