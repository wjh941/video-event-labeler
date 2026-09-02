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

## 设计检查

- 年龄段严格为 `child`、`adult`、`elderly`、`unknown`。
- 人脸和体态熟悉度严格为 `familiar`、`stranger`、`unknown`、`not_visible`。
- 事件草稿允许空时间；负数和结束早于开始会被拒绝。
- SQLite 启用 `PRAGMA foreign_keys=ON`，并为样本状态、样本关联、媒介 `(modality, uri)` 等建立索引。
- schema 不创建 `person_tag_list` 列。

## 关注事项

- `video_labeler.storage.sqlite_store.SQLiteStore` 尚由 Task 2 实现；fixture 已按 brief 要求惰性导入，因此本任务测试可独立运行。
- 当前 schema 版本为 1；后续结构变更应新增迁移函数并递增 `CURRENT_SCHEMA_VERSION`，不要修改已应用迁移的语义。
