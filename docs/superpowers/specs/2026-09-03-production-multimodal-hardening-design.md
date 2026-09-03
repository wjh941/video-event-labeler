# Production Multimodal Hardening Design

## Goal

将视频事件标注和人员属性标注升级为 SQLite 默认、可恢复、可审计、可验证的多模态数据工具，同时保留 CSV 作为兼容交换格式，并为模型预测和主动学习预留稳定接口。

## Scope

本次实现覆盖以下闭环：

1. 启动器默认使用 SQLite，首次启动自动导入或建立数据库，并自动增量索引视频媒体及 ffprobe 元数据。
2. 事件与人员保存使用乐观版本号；每次人工保存都生成 before/after 审计修订，可查询并恢复历史修订。
3. 证据拥有系统生成的稳定 ID；本地 URI 必须位于数据集媒体根目录内；事件和预测引用的证据必须可验证。
4. 模型预测支持列出、接受、拒绝；接受预测通过统一转换器写入正式标注并产生审计记录。
5. 质量检查支持 draft 和 strict 两种模式，检查媒体存在性、探测状态、事件范围、重叠、人员编号、必填字段和数据集统计。
6. 增加数据库备份、完整性检查、迁移前保护和 JSONL/CSV 导出恢复能力。
7. 大型 CSV 导入导出改为迭代处理，并提供进度回调及可取消检查点。
8. 增加 HTTP 端到端测试、路径安全测试、并发冲突测试，并同步 README、CLI 帮助和 CI 支持版本说明。

## Non-goals

- 本次不内置具体视觉、语音或大语言模型；模型通过 `AnnotationProvider` 插件接入。
- 本次不实现远程多人协作服务、账号系统或云存储。
- 本次不删除旧 CSV 兼容路径，但将其标记为兼容模式并避免与 SQLite 默认路径产生歧义。

## Architecture

`run_video_annotation.py` 是唯一推荐入口。它创建或打开 `SQLiteStore`，调用媒体索引器和 CSV 兼容导入器，然后启动事件和人员两个 HTTP 适配器。两个适配器只通过 `AnnotationService` 读写数据库；CSV 只在显式导入/导出命令中使用。

核心服务分为四层：

- domain：不可变数据对象和枚举约束。
- storage：SQLite 事务、迁移、备份、修订和并发控制。
- services：标注保存、预测决策、证据关联、媒体索引和质量策略。
- adapters：现有两个本地 HTTP UI 和 CLI，保持 API 兼容并补充 DB 模式接口。

所有写操作在一个 SQLite 事务内完成。保存前读取当前 revision；不匹配返回冲突，不覆盖他人修改。人工保存同时写入修订快照。媒体元数据刷新不增加人工标注 revision。

## Data flow

```text
video_root
  -> safe discovery
  -> sample upsert (relative path + source hash)
  -> media index (duration/fps/size/hash/probe status)
  -> event/person UI
  -> AnnotationService save
  -> sample revision + annotation_revisions + final labels
  -> quality validation
  -> CSV/JSONL export + manifest + backup
```

模型流程为：

```text
provider.predict -> model_predictions (draft)
  -> human accept/reject
  -> accepted prediction converted to Event/Person
  -> revision + audit record
```

## API changes

`AnnotationService` 增加：

- `accept_prediction(prediction_id: str, actor: str, expected_revision: int | None = None) -> SaveResult`
- `reject_prediction(prediction_id: str, actor: str) -> None`
- `list_predictions(sample_id: str) -> list[Prediction]`
- `restore_revision(sample_id: str, revision: int, actor: str, expected_revision: int | None = None) -> SaveResult`

`EvidenceService.attach` 在缺少 ID 时生成 UUID，并在本地 URI 场景调用安全路径解析。事件保存接口继续使用 `person_identity_attributes`，不恢复 `person_tag`。

CLI 增加：

- `index-media --db ... --video-root ...`
- `backup-db --db ... --output ...`
- `check-db --db ...`
- `validate --mode draft|strict ...`
- `export --format jsonl|csv --manifest ...`

## Error handling and recovery

- 所有外部路径先 `resolve_safe_media_path`，拒绝符号链接越界、目录伪装和非文件路径。
- SQLite 写入使用文件锁、WAL、busy timeout 和有限指数退避；冲突明确返回 HTTP 409。
- 导入逐行处理；单行错误记录行号并继续，最终返回错误、创建、更新、跳过统计。
- 导出先写时间戳备份，再原子替换；JSONL、CSV 和 manifest 使用同一事务边界的 revision 快照。
- 启动和迁移前可创建数据库备份；`check-db` 执行 SQLite integrity check 并报告 schema 版本。
- UI 保存失败时保留编辑内容，提示冲突/路径/校验错误，不静默清空表单。

## Quality and reproducibility

strict 模式至少拒绝：缺失视频、不可探测媒体、事件越界、结束时间早于开始时间、非法人员属性、重复人员编号、reviewed 样本中的 draft 子标注。报告包括按标签计数、空标签比例、事件重叠比例、媒体缺失数和样本哈希。

JSONL manifest 增加 schema 版本、生成时间、数据库最大 revision、源文件哈希、模型 provenance 和按 sample hash 生成的稳定 split（train/validation/test）。

## Testing strategy

- domain/storage：迁移、外键、证据 ID、路径安全、并发 revision、备份恢复和 integrity check。
- service：保存自动审计、接受/拒绝预测、恢复修订、媒体索引幂等性。
- adapter：SQLite 默认启动、API status/videos/save/update、Range 播放、409 冲突、404 和越界路径。
- CLI/export：流式 CSV、JSONL manifest、timestamped backup、strict/draft quality mode。
- CI：Python 3.11 为当前验证基线；若继续声明 Python 3.10 支持，必须增加独立 3.10 运行矩阵或调整项目版本声明。

## Acceptance criteria

1. `python run_video_annotation.py --video-root <root>` 默认创建并使用 `<root>/dataset.db`，无需手工传 `--db`。
2. 首次打开目录会建立媒体索引；质量报告不再因为正常视频目录为空索引而产生 `missing_media`。
3. 保存、预测接受和修订恢复均可在 `annotation_revisions` 中追踪并可回退。
4. 两个 UI 继续支持原视频按起止时间播放，事件时间自动按媒体时长校验。
5. 证据、预测、事件和人员均能在 JSONL 中保留来源和 lineage。
6. 所有新增行为有先失败后通过的自动化测试；全量 pytest、ruff、mypy 和编译检查通过。

