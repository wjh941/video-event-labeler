# SQLite 多模态标注平台设计

## 目标

将当前的行为标注器和人物属性标注器升级为本地优先的多模态数据标注平台：

- SQLite 作为程序内部唯一数据源，保证事务、查询和迁移能力。
- CSV 继续作为兼容导入/导出格式，不破坏已有数据集和下游脚本。
- 统一管理视频、音频、文本、行为事件、人物属性和模型预测。
- 记录数据血缘，使每个标签都能回答“来自哪个媒体、哪个模型、哪个版本、谁确认”。
- 保持当前单机、标准库优先和浏览器本地服务的使用方式。

## 非目标

- 本阶段不实现云端账号、权限系统或多人实时协作。
- 本阶段不内置具体计算机视觉、ASR 或多模态大模型。
- 不强制转码或复制大型视频文件；平台只索引原始媒体并保存引用。

## 推荐架构

```text
                     +----------------------+
                     |   Browser UI          |
                     | event/person views    |
                     +----------+-----------+
                                |
                     +----------v-----------+
                     | HTTP API             |
                     | validation/auth      |
                     +----------+-----------+
                                |
              +-----------------v-----------------+
              | Application / Domain              |
              | samples, events, people, quality |
              +---------+---------------+---------+
                        |               |
                +-------v------+ +------v-------+
                | SQLite store | | Media adapters|
                | tx/migration | | probe/audio   |
                +-------+------+ +------+--------+
                        |               |
                 +------v------+   +----v---------+
                 | CSV adapter |   | AI providers  |
                 | import/export|  | optional      |
                 +-------------+   +--------------+
```

代码边界建议如下：

```text
video_labeler/
├─ domain/       # 类型、枚举、跨字段校验
├─ storage/      # SQLite repository、CSV adapter、migration
├─ media/        # 路径、哈希、时长和媒体元数据适配器
├─ pipeline/     # 导入、预标注、质量检查、导出
├─ api/          # HTTP 路由和请求响应模型
└─ web/          # 两个标注页面及共享组件
```

现有三个脚本先作为兼容入口，内部逐步调用这些模块；不要求一次性重写前端。

## 数据模型

### samples

代表一个待标注样本，使用稳定 ID 而不是单纯文件名。

| 字段 | 说明 |
| --- | --- |
| `sample_id` | 相对路径规范化后生成的稳定标识 |
| `dataset_id` | 所属数据集 |
| `relative_path` | 相对于视频根目录的路径 |
| `source_sha256` | 原始文件校验和，用于检测替换和去重 |
| `status` | `draft`、`reviewed`、`rejected` |
| `schema_version` | 当前数据模型版本 |
| `created_at` / `updated_at` | UTC 时间戳 |

### media_assets

一个样本可拥有多个模态资产：

| 字段 | 说明 |
| --- | --- |
| `sample_id` | 样本 ID |
| `modality` | `video`、`audio`、`transcript`、`image` |
| `uri` | 本地路径或外部 URI |
| `duration_ms` | 时长，未知时为空 |
| `fps`、`width`、`height` | 视频元数据，未知时为空 |
| `metadata_json` | 模态特有元数据 |

不要求所有样本都有音频或文本；缺失模态是正常状态，不应被误当成错误。

### events

行为事件独立成表，支持一个样本多个重叠事件：

```text
event_id, sample_id, event_type, start_time_ms, end_time_ms,
source, confidence, review_status, annotator, revision
```

`source` 区分 `human`、`model`、`imported`。模型候选不能直接覆盖人工确认结果。

### persons

人员属性保存为结构化字段：

```text
person_record_id, sample_id, person_id, track_id,
age_group, face_familiarity, body_reid_familiarity,
source, confidence, review_status, annotator, revision
```

当前字段保持不变：`person_count` 由有效人员记录数量派生，人员数组导出到 CSV 的 `person_identity_attributes`。允许人员数量为 0。

### evidence

用于表达多模态证据，而不是把所有信息塞进一个字符串：

```text
evidence_id, sample_id, modality, start_time_ms, end_time_ms,
uri, text, source, confidence
```

例如，一个行为事件可以关联视频片段、音频时间段和 ASR 文本。第一阶段可以只保存视频证据，后续再接入音频和文本。

### model_predictions 和 revisions

模型输出单独保存，不覆盖最终标注：

```text
prediction_id, sample_id, task, label_json,
model_name, model_version, confidence, created_at
```

每次人工保存生成一条 revision，至少记录操作者、时间、修改前后摘要和应用版本。这样可以实现审计、回滚和标注一致性分析。

## CSV 兼容策略

CSV 仍使用当前字段：

```text
sample_id,video_path,lighting,lighting_evidence,behavior_class,
behavior_id,security_zone_points,person_count,
person_identity_attributes,events
```

兼容规则：

1. 导入时按 `relative_path + source_sha256` 幂等匹配，避免同名文件覆盖。
2. `events` 和 `person_identity_attributes` 继续使用 JSON 单元格，便于现有工具读取。
3. 导出时保留未知列，但未知列不参与核心校验。
4. 旧的 `person_tag_list` 只在迁移适配器中识别，导出的新 CSV 不再生成该字段。
5. CSV 导入和导出都创建备份，并使用临时文件、`fsync` 和原子替换。
6. CSV 不强制增加新列；导出同时生成同名的 `.meta.json` 旁车文件，其中记录 `schema_version`、导出时间和数据库 revision，避免破坏严格依赖现有列顺序的工具。

## 数据处理流水线

```text
导入目录
  -> 路径规范化与 SHA-256
  -> 媒体元数据探测
  -> SQLite upsert（可重复执行）
  -> 可选模型预标注
  -> 人工确认行为和人物
  -> 跨字段质量检查
  -> 导出 CSV / JSONL / 训练集索引
```

模型接入使用窄接口，不把模型依赖写死在标注器中：

```python
class AnnotationProvider(Protocol):
    def predict(self, sample: Sample) -> list[Prediction]: ...
```

每个 provider 必须返回模型名称、版本、置信度和证据时间段；人工确认后才写入最终标签。

## 可靠性与安全性

- 所有写操作放在 SQLite 事务中，启用 WAL；导出 CSV 使用独占锁。
- 保存前比较数据库 revision，冲突时返回明确的 `409`，不覆盖他人修改。
- 启动时执行 schema migration；迁移失败则只读启动并保留原数据库。
- 对每个媒体保存大小、修改时间和 SHA-256，文件被替换时标记为 `stale`。
- HTTP 默认只绑定 `127.0.0.1`；若允许局域网访问，必须增加一次性访问令牌。
- 视频路径只允许解析到配置的视频根目录内，拒绝路径穿越。
- 请求体大小、行号、时间段、枚举值和 JSON 结构全部在 API 层校验。
- 使用结构化日志记录导入数量、保存耗时、失败原因和恢复动作。

## 质量系统

提供独立命令：

```powershell
python -m video_labeler validate --db dataset.db
python -m video_labeler stats --db dataset.db
python -m video_labeler export --db dataset.db --format jsonl
```

质量检查至少包括：

- 视频存在、可读、时长和帧率可获取
- 事件起止时间在媒体范围内且结束时间大于开始时间
- 人员编号唯一，人员数量和数组长度一致
- 枚举值、JSON 和 schema 版本合法
- 模型预测已被人工确认或明确拒绝
- 类别分布、缺失字段、重复文件和审核进度统计

后续可以增加双人标注的一致性指标，例如 Cohen's kappa 或时间段 IoU。

## 测试策略

1. **单元测试**：schema、迁移、时间校验、路径安全、CSV round-trip。
2. **集成测试**：SQLite transaction、CSV 导入导出、旧数据迁移、HTTP API。
3. **故障测试**：写入中断、磁盘空间不足、媒体被替换、并发保存和损坏数据库。
4. **属性测试**：随机人员数组、事件区间和未知 CSV 列不会破坏数据约束。
5. **性能测试**：至少 10 万条样本的导入、筛选、分页和导出耗时。
6. **CI**：Python 编译、pytest、Ruff、Mypy、覆盖率和 Windows 运行矩阵。

## 分阶段实施

### Phase 1：可靠核心

- 拆出 domain、storage 和 validation 模块。
- 建立 SQLite schema、migration runner 和事务 repository。
- 实现 CSV 双向适配、旧字段迁移、文件锁和稳定 sample ID。
- 保持现有两个页面和组合启动命令可用。

### Phase 2：多模态数据链路

- 增加媒体元数据表和可选音频/转录资产。
- 增加 evidence、model_predictions 和 annotation revisions。
- 提供 AnnotationProvider 接口和一个本地 mock provider，便于演示人机协作。

### Phase 3：质量与面试展示

- 增加 validate/stats/export 命令。
- 增加 JSONL 训练集导出、类别平衡报告和标注一致性分析。
- 增加 CI、覆盖率、性能基准、架构图和示例数据集。

## 验收标准

- 现有 CSV 可以无损导入 SQLite，再导出后事件和人物字段语义一致。
- 同一目录重复导入不会产生重复样本。
- 两个标注页面都通过统一 repository 保存，不直接修改 CSV。
- 程序异常退出或并发保存时不会产生半写入或静默覆盖。
- 一个样本可以同时关联视频事件、人物属性、音频/文本证据和模型候选。
- 所有最终标签都能追溯到人工或模型来源。
- 新增功能有自动化测试和可复现的命令行演示。

## 方案取舍

- 选择 SQLite 而不是直接改造 CSV，是为了事务、查询、迁移和嵌套数据能力。
- 保留 CSV 是为了兼容现有仓库和外部标注工具，降低迁移风险。
- 暂不引入云端数据库，是因为当前目标是单机稳定性和面试可演示性；未来可在 repository 层替换存储实现。
