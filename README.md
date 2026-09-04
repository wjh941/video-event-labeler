# Video Event Labeler

SQLite platform commands, schema details, and a reproducible synthetic demo are documented in `docs/architecture.md`, `docs/data-model.md`, and `docs/demo_dataset/README.md`. The current database schema is version 3.

本仓库提供一套本地视频标注工具：先从视频目录生成行为事件 CSV，再使用同一份 CSV 标注人物身份属性。工具只使用 Python 标准库，不需要安装第三方 Python 包。SQLite 是正式数据源，CSV 用于兼容导入和导出。

## 环境

- Windows 或 Linux
- Python 3.11 或更高版本
- 一个按目录组织的视频数据集

进入仓库目录：

```powershell
cd 'D:\default file\视频标注工具'
```

## 推荐：组合启动

一次完成两个阶段：

```powershell
python .\run_video_annotation.py --video-root 'D:\videos'
```

启动器会：

1. 扫描视频目录，创建或增量更新 `video_labeler_manifest.csv`。
2. 在视频目录中默认创建或打开 `dataset.db`，并建立媒体索引。
3. 打开行为事件标注页面。
4. 行为阶段完成后，在终端按 `Ctrl+C` 停止第一阶段。
5. 自动启动人物身份标注页面。
6. 人物阶段完成后，在终端按 `Ctrl+C` 结束。

如果行为事件已经标完，只进入人物阶段：

```powershell
python .\run_video_annotation.py --video-root 'D:\videos' --person-only
```

组合启动器默认使用 8765 端口标注行为、8865 端口标注人物；端口被占用时可调整：

```powershell
python .\run_video_annotation.py --video-root 'D:\videos' --event-port 9000 --person-port 9001
```

## 分开启动

### 1. 生成清单并标注行为

```powershell
python .\video_event_labeler.py --video-root 'D:\videos'
```

行为标注脚本启动后会自动打开本地浏览器。如果浏览器没有自动打开，请复制终端打印的 `http://127.0.0.1:<port>/` 地址访问。需要手动控制浏览器时可使用：

```powershell
python .\video_event_labeler.py --video-root 'D:\videos' --no-browser
```

页面中的“导入视频文件夹”会调用系统原生文件夹选择器。若系统没有桌面会话、Tk 初始化失败或对话框被系统策略阻止，接口会返回明确提示，此时在旁边的路径框输入绝对路径并点击“按路径导入”即可继续，不影响 CSV/SQLite 导入流程。

也可以指定已有 CSV 或数据库：

```powershell
python .\video_event_labeler.py `
  --video-root 'D:\videos' `
  --csv 'D:\videos\my_manifest.csv' `
  --db 'D:\videos\dataset.db'
```

不带命令行参数时，页面支持选择视频文件夹或输入目录路径。工具会递归扫描常见格式：`.mp4`、`.avi`、`.mov`、`.mkv`、`.webm`、`.m4v`。

### 2. 标注人物身份

```powershell
python .\person_identity_labeler.py `
  --video-root 'D:\videos' `
  --db 'D:\videos\dataset.db'
```

也可使用已有 CSV（兼容模式）：

```powershell
python .\person_identity_labeler.py `
  --video-root 'D:\videos' `
  --csv 'D:\videos\video_labeler_manifest.csv'
```

人物脚本会根据每一行的 `video_path` 自动切换原视频。点击事件卡片的“播放片段”时，会从该事件的开始时间播放到结束时间并自动暂停，不会生成新视频。人物脚本只保存人物字段，行为类型、事件时间、灯光和其他字段由行为标注脚本维护。

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

目录名和文件名中的行为关键词会用于预填行为标签。`pos` 表示正例，`neg` 表示负例；负例会预填 `normal_scene`。预填结果只是草稿，必须人工确认后才能审核。

## CSV 字段

新清单使用 UTF-8 with BOM 编码，字段顺序为：

```text
sample_id,video_path,lighting,lighting_evidence,behavior_class,behavior_id,security_zone_points,person_count,person_identity_attributes,events
```

| 字段 | 用途 |
| --- | --- |
| `sample_id` | 视频文件名，作为稳定记录 ID |
| `video_path` | 原视频相对路径；人物页面按此路径切换视频 |
| `lighting` | 从目录名推断的白天、黑夜或红外 |
| `lighting_evidence` | 默认 `人工确认` |
| `behavior_class` | 目录推断的行为类别 |
| `behavior_id` | 一个或多个行为标签，逗号分隔 |
| `security_zone_points` | 兼容旧格式，默认 `null` |
| `person_count` | 非负整数，允许为 `0` |
| `person_identity_attributes` | JSON 人员数组 |
| `events` | 行为事件及毫秒级起止时间 |

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

## 行为标注流程

1. 选择左侧记录。
2. 在视频播放器中定位事件开始位置，点击事件卡片的“截取”。
3. 定位结束位置，再点击“截取”。
4. 点击“循环片段”反复检查区间。
5. 点击“保存草稿”或“审核并下一条”。

正例事件审核时必须填写合法的开始和结束时间，且结束时间晚于开始时间。`normal_scene` 可以没有时间段，不能和正例行为混用。

## 人物标注流程

1. 选择当前记录，确认页面已经切换到对应原视频。
2. 填写人员数量；`0` 表示画面中没有需要标注的人员。
3. 为每个人填写唯一编号、年龄段、人脸熟悉度和体态熟悉度。
4. 使用事件卡片的“播放片段”检查行为区间与人物属性是否匹配。
5. 保存当前记录，或保存后切换上一条/下一条。

人物页面不会改写事件字段；如果需要调整行为或时间，请回到 `video_event_labeler.py`。

## SQLite 维护与导出

```powershell
python -m video_labeler index-media --db 'D:\videos\dataset.db' --video-root 'D:\videos'
python -m video_labeler validate --db 'D:\videos\dataset.db' --mode strict
python -m video_labeler backup-db --db 'D:\videos\dataset.db' --output 'D:\backups\dataset.db'
python -m video_labeler check-db --db 'D:\videos\dataset.db'
python -m video_labeler export --db 'D:\videos\dataset.db' --format jsonl --output 'D:\exports\train.jsonl'
```

`backup-db` 使用 SQLite 在线备份接口，兼容 WAL；`check-db` 执行完整性检查。严格质量模式会拒绝未审核、缺失时间或无效事件，草稿模式适合持续标注。

## 安全写入与恢复

- SQLite 写入使用事务、乐观修订号和文件锁；过期页面保存会返回冲突，不覆盖新数据。
- 写入前会创建时间戳备份，并使用临时文件和原子替换，避免半写入 CSV/JSONL。
- 行为和人物页面都会自动保存未提交草稿到浏览器本地存储，刷新后可恢复。
- 从旧 CSV 迁移时会补齐 `person_count`、`person_identity_attributes`，并移除旧的 `person_tag_list`。
- 本地视频路径必须位于 `--video-root` 下，路径穿越请求会被拒绝。

恢复方式：关闭标注服务，使用 `backup-db` 生成的数据库备份或 CSV 同目录时间戳备份恢复，然后重新启动脚本。

## 常见问题

### 浏览器打不开

查看终端打印的本地地址，例如 `http://127.0.0.1:8765/`，手动复制到浏览器。也可以使用 `--no-browser` 后手动访问。

### 视频不存在

确认数据库或 CSV 的 `video_path` 是相对于 `--video-root` 的真实文件；重新运行 `index-media` 可刷新媒体状态。

### 保存提示版本冲突

关闭其他编辑页面，刷新后重新确认当前记录再保存。服务不会覆盖较新的修订。

### 需要重新扫描目录

再次运行 `video_event_labeler.py --video-root ...` 或 `index-media`。已存在记录的人工事件和人物属性会保留，新视频会增量加入。

## 测试

```powershell
python -m pytest -q
ruff check video_labeler
mypy video_labeler --exclude 'video_labeler/(storage|services|quality)'
```

## SQLite compatibility adapter

SQLite is the internal source of truth. Import existing manifests and export compatibility CSV files with these commands:

```powershell
python -m video_labeler import-csv --csv 'D:\videos\video_labeler_manifest.csv' --video-root 'D:\videos' --db 'D:\videos\dataset.db'
python -m video_labeler export-csv --db 'D:\videos\dataset.db' --csv 'D:\videos\video_labeler_manifest.csv' --video-root 'D:\videos'
```

The adapter accepts UTF-8/BOM CSV and common delimiters, stores unknown columns in `samples.extra_json`, derives `person_count` from `person_identity_attributes`, and computes deterministic sample/event/person identifiers. Legacy `person_tag_list` is discarded during import and never emitted on export. A changed or deleted source video is reported as `stale` while annotations remain untouched. Export writes a UTF-8 BOM CSV, a timestamped backup when needed, and `<csv>.meta.json` with schema version, UTC export time, database revision, and sample count. Malformed JSON in draft rows is retained as a draft with an import error.

测试覆盖行为预标注、CSV 迁移、原子备份、并发修改检测、人物属性校验、多视频切换、事件字段保护、HTTP 接口以及数据库备份完整性。
