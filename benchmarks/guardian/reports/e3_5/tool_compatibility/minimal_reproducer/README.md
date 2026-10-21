# Supabase `UnauthorizedResponse` minimal reproducer

该复现器只保留一个 operation、一条 `401` response、一个 response component 和一个 schema component。

触发结构是：

```text
DELETE /identities/{identityId}
→ responses.401.content.application/json.schema
→ $ref: #/components/responses/UnauthorizedResponse
```

这里的 `$ref` 位于 **Schema Object** 槽位，却指向 **Response Object**。`UnauthorizedResponse` 自身位于
`components.responses`，其内部 schema 再正确引用 `components.schemas.ErrorSchema`。因此问题不是
`UnauthorizedResponse` 的内部 schema，而是 operation 中把整个 Response Object 当成 Schema Object 使用。

`minimal_base.yaml` 与 `minimal_head.yaml` 只改变 `info.version`，两者都保留该非法引用，以验证 oasdiff
在加载 Base 时的行为。诊断脚本还会临时生成一个修正 control：把 operation 内的引用改为
`#/components/schemas/ErrorSchema`；control 不替换任何 E3-B 输入。
