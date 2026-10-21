# Case

`rw_immich_004_tag_name_pattern_narrowed`

## Material Passport

- 阶段：E3-B pre-system human spot check
- 证据：固定 Base/Head OpenAPI、相邻 Spec commit、commit message
- 冻结记录：`reviews/pre_system/rw_immich_004_tag_name_pattern_narrowed.json`
- Ground Truth aggregate SHA256：`3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- 本 packet 没有使用任何 system evaluation 结果

## Repository

- Repository：`immich-app/immich`
- OpenAPI：`open-api/immich-openapi-specs.json`

## Base / Head Commit

- Base：`2feb889b9cdb189b08ef55fb94904315fdac04be`
- Head：`1b3aa9cd512a01a44ffe807ac509c456310c7750`

## Commit Message

`fix(server): correct tag create operations (#30877)`

## Relevant OpenAPI Diff

```diff
 components:
   schemas:
     TagCreateDto:
       required: [name]
       properties:
         name:
           type: string
+          pattern: "^[^/]*$"
```

`POST /tags` 的请求体引用该组件：

```yaml
paths:
  /tags:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/TagCreateDto"
```

## Plain-language Change

旧版本只要求标签名是字符串。新版本额外规定标签名不能包含 `/`。

因此，过去按 OpenAPI 合法的 `team/backend`，在 Head contract 中不再合法。

## Compatibility Reasoning

冻结标签是 **FAIL**。

这是 request contract 收窄：Head 接受的输入集合小于 Base。已有调用方如果发送包含 `/` 的标签名，会从“符合旧契约”变成“不符合新契约”。commit message 中的 `fix` 不会自动使该变化对旧调用方兼容。

## ContractAffectedScope Evidence

- `POST /tags`

该 operation 的 request body 直接引用 `TagCreateDto`。独立检查 fragment-local `$ref` 后，没有其他 operation 引用此请求组件。

## RuntimeExecutableScope Evidence

- `POST /tags`

该 operation 在 Head Spec 中仍存在，并继续引用 `TagCreateDto`，因此可以作为 Head contract 下的 runtime regression target。

## Frozen Label

| Field | Frozen value |
|---|---|
| status | `confirmed` |
| confidence | `high` |
| verdict | `fail` |
| contract ops | `1` |
| executable ops | `1` |

## Reviewer Questions

- **A. Agree**：同意冻结标签与两个 scope。
- **B. Disagree**：不同意；请指出是 verdict、affected scope 还是 executable scope 有误。
- **C. Unsure**：现有证据不足，需要查看额外历史或实现证据。

最值得人工检查的疑点：Base Spec 虽然允许 `/`，但旧实现是否实际上已经拒绝它？这不改变纯 OpenAPI 兼容性判断，却会影响对“真实行为回归”的解释。

