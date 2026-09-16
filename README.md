# 视频事件标注工具（Video Event Labeler）

面向本地视频数据集的两阶段网页标注工具：先把一个视频目录递归整理成清单 CSV 并标注行为事件与毫秒级时间段，再用同一份 CSV 标注人物身份属性。标注人员与质检人员在 Windows 上本地使用，无需部署、无第三方 Python 依赖；SQLite 是内部数据源，CSV/JSONL 是对外交换格式。

## 功能特性

- 递归扫描视频目录（`.mp4`、`.avi`、`.mov`、`.mkv`、`.webm`、`.m4v`），生成并增量维护 `video_labeler_manifest.csv`，已有人工标注保留、新视频追加。
- 按目录名和文件名预填草稿标签：`pos`/`neg` 分层；`neg` 预填 `normal_scene`；目录路径推断光照（白天、黑夜、红外）；中文/英文关键词映射到 12 个固定行为标签，也支持 1–64 字符的自定义标签。预填只是草稿，必须人工确认。
- 行为标注网页：截取开始/结束时刻（毫秒级，`H:MM:SS.mmm` 显示）、循环片段复查、保存草稿、审核并下一条；`normal_scene` 不得与正例行为混用，正例审核必须有完整且合法的时间段。
- 人物标注网页：填写人员数量与每人唯一编号、年龄段、人脸熟悉度、体态熟悉度（枚举校验）；按 CSV 行自动切换原视频；事件卡片只读回看，可播放事件片段；只写人物字段，不改写事件。
- 组合启动器 `run_video_annotation.py`：行为阶段结束后按 `Ctrl+C` 自动衔接人物阶段。
- SQLite 存储（schema 版本 3，含 datasets、samples、media_assets、events、persons、evidence、model_predictions、annotation_revisions 八张表），事务写入、WAL、乐观 revision 冲突检测。
- CSV 兼容导入/导出：确定性样本/事件/人员 ID、未知列保存在 `samples.extra_json`、迁移时丢弃旧 `person_tag_list`、源视频变更或缺失报 `stale` 且不动已有标注、原子写入加时间戳备份和 `.meta.json` 元数据。
- 质量命令行：跨表一致性校验、数据集统计、逐样本 JSONL 导出（含 provenance 与修订记录）。
- 媒体路径安全：拒绝逃逸视频根目录及符号链接切换；可选 ffprobe 读取时长/分辨率/帧率，缺失时安全降级。

## 组成

```text
video files ──> CSV import ──> SQLiteStore ──> AnnotationService ──> 浏览器标注页（行为 / 人物）
                                     └────────> quality/stats ──> CSV / JSONL 导出
```

| 模块 | 职责 |
| --- | --- |
| `video_event_labeler.py` | 行为与事件时间标注页（标准库 `http.server`，本地 127.0.0.1） |
| `person_identity_labeler.py` | 人物身份属性标注页 |
| `run_video_annotation.py` | 两阶段组合启动器 |
| `video_labeler/domain.py` | 经校验的不可变领域记录（Sample/Event/Person 等） |
| `video_labeler/storage/` | SQLite 事务仓库（`sqlite_store`）、schema 迁移、CSV 兼容适配、文件锁 |
| `video_labeler/services.py` | 面向两个标注页的行投影与事件/人员保存 |
| `video_labeler/quality.py` | 校验、统计、JSONL 导出 |
| `video_labeler/media.py` | 视频发现、SHA-256、路径安全校验、可选 ffprobe 探测 |
| `old/` | 归档的旧版单脚本，不参与运行 |

## 快速开始

环境要求：Python 3.10 或更高（CI 验证基线为 3.11）、Windows；ffprobe 可选。

运行标注页无需安装任何第三方包；开发工具链可选安装：

```powershell
pip install -e ".[dev]"
```

### 组合启动（推荐）

```powershell
python .\run_video_annotation.py --video-root 'D:\videos'
```

启动器会：

1. 扫描视频目录，创建或增量更新 `video_labeler_manifest.csv`。
2. 打开行为事件标注页面。
3. 行为阶段完成后，在终端按 `Ctrl+C` 停止第一阶段。
4. 自动启动人物身份标注页面。
5. 人物阶段完成后，在终端按 `Ctrl+C` 结束。

行为已标完、只需补人物时：

```powershell
python .\run_video_annotation.py --video-root 'D:\videos' --person-only
```

### 分开启动

行为标注（终端会打印本地地址，如 `http://127.0.0.1:8765/`，手动复制到浏览器打开）：

```powershell
python .\video_event_labeler.py --video-root 'D:\videos'
```

不带参数启动时，页面内可选择视频文件夹或输入目录路径。人物标注：

```powershell
python .\person_identity_labeler.py --video-root 'D:\videos' --csv 'D:\videos\video_labeler_manifest.csv'
```

### 数据集命令行

```powershell
python -m video_labeler import-csv --csv video_labeler_manifest.csv --video-root D:\videos --db dataset.db
python -m video_labeler export-csv --db dataset.db --csv video_labeler_manifest.csv --video-root D:\videos
python -m video_labeler validate --db dataset.db
python -m video_labeler stats --db dataset.db
python -m video_labeler export --db dataset.db --format jsonl --output train.jsonl
```

`validate` 输出 JSON 质量报告，存在错误时退出码为 1；`stats` 输出各状态样本数、事件/人员计数与完成率等聚合值；`export` 每个样本写一条含媒体、事件、人员、provenance 与修订记录的 JSONL。`docs/demo_dataset/README.md` 提供一份不提交媒体的合成数据演示。

## 配置与参数

组合启动器 `run_video_annotation.py`：`--video-root`（必填）、`--csv`（必须位于视频根目录下，默认 `<video-root>\video_labeler_manifest.csv`）、`--person-only`、`--no-browser`、`--event-port`（默认 8765）、`--person-port`（默认 8865）、`--db`（指定 SQLite 数据库）。

`video_event_labeler.py`：`--video-root`、`--csv`、`--port`（默认 8765，范围 1–65535）、`--db`。未提供 `--video-root` 且无 `--csv` 时进入空页面，可在页面上导入目录。

`person_identity_labeler.py`：`--video-root`、`--csv`、`--video`（兼容单视频 CSV）、`--host`（默认 127.0.0.1）、`--port`（起始端口，被占用时自动向后尝试最多 20 个）、`--no-browser`、`--db`。

## 推荐目录结构

```text
D:\videos\
├─ 跌倒\
│  └─ pos\
│     └─ fall-pos-001.mp4
├─ 入侵\
│  └─ neg\
│     └─ normal-neg-001.mp4
└─ video_labeler_manifest.csv
```

目录名和文件名中的行为关键词会用于预填行为标签；`neg` 优先于文件名中的事件词，负例预填 `normal_scene`。预填结果只是草稿，必须人工确认后才能审核。

## CSV 字段

新清单使用 UTF-8 with BOM 编码，字段顺序为：

```text
sample_id,video_path,lighting,lighting_evidence,behavior_class,behavior_id,security_zone_points,person_count,person_identity_attributes,events
```

| 字段 | 用途 |
| --- | --- |
| `sample_id` | 视频文件名，作为稳定记录 ID |
| `video_path` | 原视频绝对路径；人物页面按此路径切换视频 |
| `lighting` | 从目录名推断的白天、黑夜或红外 |
| `lighting_evidence` | 默认 `人工确认` |
| `behavior_class` | 目录推断的行为类别（中文） |
| `behavior_id` | 一个或多个行为标签，逗号分隔 |
| `security_zone_points` | 兼容旧格式，默认 `null` |
| `person_count` | 非负整数，允许为 `0` |
| `person_identity_attributes` | JSON 人员数组 |
| `events` | 行为事件及毫秒级起止时间 |

`events` 既接受标准 JSON，也兼容旧的 `ms` 后缀格式；行为脚本读取时支持 UTF-8（含 BOM）与 GB18030 编码。

## 人员属性格式

`person_identity_attributes` 只保存结构化 JSON，不再生成或维护旧的 `person_tag_list` 字段。

```json
[
  {
    "person_id": "p1",
    "age_group": "adult",
    "face_familiarity": "stranger",
    "body_reid_familiarity": "unknown"
  }
]
```

可选值：

- `age_group`: `child`、`adult`、`elderly`、`unknown`
- `face_familiarity`: `familiar`、`stranger`、`unknown`、`not_visible`
- `body_reid_familiarity`: `familiar`、`stranger`、`unknown`、`not_visible`

每行人员编号必须唯一。人员数为 0 时保存为空数组 `[]`。人脸或体态无法判断时使用 `unknown` 或 `not_visible`，不要编造身份。

## 标注流程

行为阶段：

1. 选择左侧记录。
2. 在视频播放器中定位事件开始位置，点击事件卡片的“截取”。
3. 定位结束位置，再点击“截取”。
4. 点击“循环片段”反复检查区间。
5. 点击“保存草稿”或“审核并下一条”。

正例事件审核时必须填写合法的开始和结束时间，且结束时间晚于开始时间。`normal_scene` 可以没有时间段，不能和正例行为混用。

人物阶段：

1. 选择当前 CSV 记录，确认页面已经切换到对应原视频。
2. 填写人员数量；`0` 表示画面中没有需要标注的人员。
3. 为每个人填写唯一编号、年龄段、人脸熟悉度和体态熟悉度。
4. 使用事件卡片的“播放片段”检查行为区间与人物属性是否匹配。
5. 点击“保存到 CSV”，或用“保存并上一段 / 保存并下一段”。

人物页面不会改写事件字段；如果需要调整行为或时间，请回到 `video_event_labeler.py`。

## 数据安全与恢复

- 行为脚本首次修改已有 CSV 前，会在同目录 `event_labeler_backups\` 下创建时间戳备份；检测到 CSV 被外部修改时拒绝保存并提示刷新，不会覆盖外部改动。
- CSV 导出前写同目录 `.before_export_<UTC时间戳>` 备份，并生成 `<csv>.meta.json`（schema 版本、UTC 导出时间、数据库 revision、样本数）；写入使用临时文件加原子替换，配合 `.lock` 文件锁。
- 数据库写入为事务式，样本带乐观 revision；过期修订写入会得到冲突错误，需重新加载后重试。
- 从旧 CSV 迁移时补齐 `person_count`、`person_identity_attributes`，并移除旧的 `person_tag_list`。

恢复方式：关闭标注服务，把备份文件复制回原 CSV 文件名，然后重新启动脚本。

## 常见问题

### 浏览器打不开

查看终端打印的本地地址（如 `http://127.0.0.1:8765/`），手动复制到浏览器。`run_video_annotation.py` 和 `person_identity_labeler.py` 提供 `--no-browser`；`video_event_labeler.py` 本身不自动开浏览器。

### 视频不存在

确认 CSV 的 `video_path` 指向真实文件；相对路径需要配合 `--video-root` 使用。导入时发现源视频被改动或删除的记录会标记为 `stale`，已有标注不受影响。

### 保存提示 CSV 被外部修改

关闭其他正在编辑该 CSV 的程序，刷新页面，重新确认当前记录后再保存。

### 需要重新扫描目录

再次运行 `video_event_labeler.py --video-root ...` 或在页面内重新导入。已存在记录的人工事件和人物属性会保留，新视频会增量加入。

## 已知边界

- 工具只按原文件提供视频流，不做转码；能否播放取决于浏览器对相应编码的支持。
- 未配置 ffprobe 时时长/分辨率等元数据为未知，`validate` 会给出 warning 但不视为错误。
- 行为脚本的文件夹选择弹窗依赖桌面环境（tkinter）；无桌面时自动退化为纯网页模式。
- 面向单机单人使用，本地服务默认只绑定 127.0.0.1，无鉴权，请勿直接暴露到公网。

## 开发与测试

```powershell
python -m pytest -q          # 运行 tests/ 下的测试套件（pyproject 的 testpaths）
python -m pytest test_video_event_labeler.py   # 根目录的 unittest 风格脚本测试需显式指定
python -m ruff check video_labeler
python -m mypy video_labeler --exclude 'video_labeler/(storage|services|quality)'
```

GitHub Actions 在 Ubuntu 与 Windows（Python 3.11）上运行编译检查、ruff、mypy 和 pytest；根目录标注脚本与 `old/` 归档不在 ruff/mypy 范围内（见 `pyproject.toml`）。变更记录见 `CHANGELOG.md`（当前版本 0.2.0）。

## 文档

- `docs/architecture.md`：数据流、安全边界与 CLI 命令一览。
- `docs/data-model.md`：schema 版本 3 的表结构与数据血缘。
- `docs/demo_dataset/README.md`：可复现的合成演示数据集。
- `docs/superpowers/`：各功能的设计文档与实施计划。
