# Task 1 实施报告：领域模型与 SQLite Schema

## 变更文件

- `video_labeler/__init__.py`：导出共享领域类型。
- `video_labeler/domain.py`：新增不可变 `Sample`、`MediaAsset`、`Event`、`Person`、`Evidence`、`Prediction` 数据类，以及年龄段、熟悉度、媒介类型、来源和审核状态枚举常量。模型会校验必填字段、枚举值、置信度、JSON 和时间区间；人员数量由人员记录派生，不在 `Person` 中重复存储。
- `video_labeler/schema.py`：新增 schema v1 迁移器。创建 datasets、samples、media_assets、events、persons、evidence、model_predictions、annotation_revisions 和 schema_migrations 表，启用外键、约束及查询索引，重复执行安全。
- `tests/conftest.py`：添加后续 SQLiteStore 使用的惰性 fixture 和测试工厂。
- `tests/test_domain.py`、`tests/test_schema.py`：覆盖模型校验、枚举、冻结语义、外键、索引、迁移幂等性、未来版本拒绝和 UTC 时间默认值。
- `pyproject.toml`：添加 Python 3.10+ 打包元数据和 pytest/ruff/mypy 开发依赖声明；运行时无第三方依赖。

## 验证命令

```text
python -m pytest -q tests/test_domain.py tests/test_schema.py
10 passed in 0.04s

python -m py_compile video_labeler/domain.py video_labeler/schema.py
exit code 0
```

## Review 修复 Round 3

- 对已存在但没有 UTC 定义的旧版 `schema_migrations` 表执行显式兼容迁移：在当前事务中重建新定义、保留版本记录，并为缺少 `Z` 后缀的历史时间补齐 UTC 标记。
- 增加旧表升级回归测试，验证记录保留以及升级后非法时间写入被拒绝。

复核后验证：

```text
python -m pytest -q tests/test_domain.py tests/test_schema.py
16 passed in 0.10s

python -m py_compile video_labeler/domain.py video_labeler/schema.py
exit code 0
```

## Commits

- `8e765ce feat: add typed multimodal domain and schema migrations`
- `b403301 fix: harden schema migration atomicity and constraints`
- `976fa10 fix: harden migration statement execution and timestamps`

## 设计检查

- 年龄段严格为 `child`、`adult`、`elderly`、`unknown`。
- 人脸和体态熟悉度严格为 `familiar`、`stranger`、`unknown`、`not_visible`。
- 事件草稿允许空时间；负数和结束早于开始会被拒绝。
- SQLite 启用 `PRAGMA foreign_keys=ON`，并为样本状态、样本关联、媒介 `(modality, uri)` 等建立索引。
- schema 不创建 `person_tag_list` 列。

## 关注事项

- `video_labeler.storage.sqlite_store.SQLiteStore` 尚由 Task 2 实现；fixture 已按 brief 要求惰性导入，因此本任务测试可独立运行。
- 当前 schema 版本为 1；后续结构变更应新增迁移函数并递增 `CURRENT_SCHEMA_VERSION`，不要修改已应用迁移的语义。

## Review 修复

根据 Task 1 review 追加以下修复：

- events 表增加约束：非 draft 记录必须同时具有起止时间。
- 将 schema DDL 从 `executescript` 改为逐条 `execute`，并把 marker 创建、DDL 和版本记录放在同一事务中；失败会完整回滚，随后可以重试。
- 已应用版本会重新执行 `IF NOT EXISTS` 声明，能够修复 marker 存在但表或索引被删除的数据库。
- datasets、samples、model_predictions、annotation_revisions 的时间列统一增加 UTC `Z` 后缀约束；datasets 默认值由 SQLite UTC `strftime` 生成。

修复后验证：

```text
python -m pytest -q tests/test_domain.py tests/test_schema.py
14 passed in 0.08s

python -m py_compile video_labeler/domain.py video_labeler/schema.py
exit code 0
```

## Review 修复 Round 2

- `schema_migrations.applied_at` 增加 UTC `strftime` 默认值与 `Z` 后缀约束。
- v1 DDL 执行改用 `sqlite3.complete_statement` 增量识别完整 SQL 语句，避免按分号切分导致未来字符串或 trigger 语句被错误拆分。

复核后验证：

```text
python -m pytest -q tests/test_domain.py tests/test_schema.py
15 passed in 0.10s

python -m py_compile video_labeler/domain.py video_labeler/schema.py
exit code 0
```
