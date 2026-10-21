# Case

`rw_immich_006_asset_face_response_removed`

## Material Passport

- 阶段：E3-B pre-system human spot check
- 证据：固定 Base/Head OpenAPI、相邻 Spec commit、commit message、fragment-local `$ref` 闭包
- 冻结记录：`reviews/pre_system/rw_immich_006_asset_face_response_removed.json`
- Ground Truth aggregate SHA256：`3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- 本 packet 没有使用任何 system evaluation 结果

## Repository

- Repository：`immich-app/immich`
- OpenAPI：`open-api/immich-openapi-specs.json`

## Base / Head Commit

- Base：`a278c10c7594746bbc99cccacdf9eb51ac228436`
- Head：`38438c8d9a4b19e8563327b26d098bec6d862711`

## Commit Message

`refactor!: remove asset faces from AssetResponseDto (#27779)`

## Relevant OpenAPI Diff

```diff
 components:
   schemas:
     AssetResponseDto:
       properties:
-        unassignedFaces:
-          type: array
-          items:
-            $ref: "#/components/schemas/AssetFaceWithoutPersonResponseDto"
         people:
           type: array
           items:
-            $ref: "#/components/schemas/PersonWithFacesResponseDto"
+            $ref: "#/components/schemas/PersonResponseDto"

-    AssetFaceWithoutPersonResponseDto: ...
-    PersonWithFacesResponseDto: ...
```

## Plain-language Change

旧版本的 asset response 可以带 `unassignedFaces`，并且 `people` 数组中的人员对象包含 face 相关结构。新版本删除 `unassignedFaces`，同时把 `people` 的元素替换为不含旧 face 结构的 `PersonResponseDto`。

## Compatibility Reasoning

冻结标签是 **FAIL**。

这是 response contract 删除字段并替换嵌套对象类型。依赖旧 face 信息的客户端会失去原来由 Base OpenAPI 声明的数据，不能把它视为单纯增加字段。

## ContractAffectedScope Evidence

下列 24 个 operation 的 OpenAPI schema 通过 fragment-local `$ref` 传递引用 `AssetResponseDto`：

1. `GET /assets/{id}`
2. `PUT /assets/{id}`
3. `GET /duplicates`
4. `GET /memories`
5. `POST /memories`
6. `GET /memories/{id}`
7. `PUT /memories/{id}`
8. `GET /search/cities`
9. `GET /search/explore`
10. `POST /search/large-assets`
11. `POST /search/metadata`
12. `POST /search/random`
13. `POST /search/smart`
14. `GET /shared-links`
15. `POST /shared-links`
16. `POST /shared-links/login`
17. `GET /shared-links/me`
18. `GET /shared-links/{id}`
19. `PATCH /shared-links/{id}`
20. `GET /stacks`
21. `POST /stacks`
22. `GET /stacks/{id}`
23. `PUT /stacks/{id}`
24. `GET /view/folder`

它们受影响不是因为 path 本身被编辑，而是因为共同使用的 response component 发生了 breaking change。

## RuntimeExecutableScope Evidence

上述 24 个 operation 在 Head Spec 中都仍存在，并仍通过各自 response schema 引用 Head 版本的 `AssetResponseDto`，所以 RuntimeExecutableScope 与 ContractAffectedScope 相同。

没有 operation 因删除而从 executable scope 排除。

## Frozen Label

| Field | Frozen value |
|---|---|
| status | `confirmed` |
| confidence | `high` |
| verdict | `fail` |
| contract ops | `24` |
| executable ops | `24` |

## Reviewer Questions

- **A. Agree**：同意冻结标签与 24 个 operation 的 scope。
- **B. Disagree**：不同意；请指出错误或不应纳入的具体 operation。
- **C. Unsure**：需要逐 operation 检查完整 `$ref` 路径。

最值得人工检查的疑点：24 条传递引用是否全部位于 response 方向，且是否存在仅在不可能分支中引用 `AssetResponseDto` 的 operation。

