# 统一视频事件标注工具设计

## 目标

交付一个本地运行的 Python 视频标注工具，替代现有的多行为和简单标注脚本。它能够：

- 读取并继续编辑已有的多行为和简单 CSV；
- 从任意视频目录生成或增量更新可用的多行为 CSV；
- 根据数据集目录与文件名自动预填行为标签，随后由人工审核时间段和人员标签；
- 对新建或自动生成的行为只接受约定标签，保持训练数据一致，并保留旧 CSV 的历史标签。

交付目录为 `D:\default file\视频标注工具\`。旧脚本和其旧测试将移至该目录下一层的 `old\`，不做不可恢复删除。

## 数据与标签

新建 manifest 使用 UTF-8 with BOM，并包含以下字段：

`sample_id`, `video_path`, `data_stratum`, `behavior_id`, `events`, `person_tag_list`, `review_status`。

- `sample_id` 是去除扩展名后的相对视频路径，保证目录内唯一。
- `video_path` 是相对导入目录的视频路径，服务器只允许解析到该目录内的文件。
- `data_stratum` 为从路径读取的 `pos` 或 `neg`；无法识别时为空。
- `behavior_id` 使用逗号连接的事件类型，与 `events` 保持同一顺序。
- `events` 延续现有的毫秒格式，包含每个事件的 `event_type`、`start_time_ms` 和 `end_time_ms`。
- `person_tag_list` 保留 `stranger`、`acquaintance`、`null` 三种值。
- `review_status` 为 `pending` 或 `reviewed`。自动预填和新视频均为 `pending`。

固定行为标签为：

`person_fall`, `climb_fence`, `peep_car_window`, `pickup_package`, `linger_wander`, `stay_long`, `cat_enter_frame`, `dog_enter_frame`, `stranger_enter_frame`, `approach_risk_zone`, `normal_scene`。

`normal_scene` 是负例，不能和其他行为标签同时存在。已有 CSV 中的历史行为标签可以读取和保留，但新建行为只能从固定标签中选取。

## 导入和自动预标

界面提供“导入视频文件夹”操作；脚本也支持 `--video-root` 参数。两者均递归扫描 `.mp4`、`.avi`、`.mov`、`.mkv`、`.webm`、`.m4v`。

目录内已有 `video_labeler_manifest.csv` 时，按规范化相对路径检查，保留已有记录及标注，只写入新发现的视频；不存在时创建文件。首次修改任意 CSV 前创建带时间戳的备份，并通过同目录临时文件替换完成写入。

预标逻辑以 `pos` / `neg` 路径层级优先于文件名中的 `Positive` / `Negative` 文字：

- `neg`：预填 `normal_scene`。
- `pos`：合并目录和文件名可识别的正例标签，允许多个标签。
- 无 `pos` / `neg`：仅按文件名识别；无匹配时留空。

当前 `D:\dapeng-test` 的目录及文件名映射为：

| 来源 | 预填标签 |
| --- | --- |
| `徘徊`、`linger` | `linger_wander` |
| `拾取包裹`、`pick_up_package`、`pick_up_packages` | `pickup_package` |
| `窥视`、`peep_car` | `peep_car_window` |
| `跌倒`、`fall` | `person_fall` |
| `逗留`、`stay` | `stay_long` |
| `靠近` | `approach_risk_zone` |
| `climb`、`fence` | `climb_fence` |
| `cat_come`、`cat_in` | `cat_enter_frame` |
| `dog_come`、`dog_in`、`dog_out` | `dog_enter_frame` |
| `strange_car_invasion`、`stranger_in` | `stranger_enter_frame` |

每个自动预填事件的时间为空。导入不会将任何预填结果标为已审核。

## 界面与保存

界面保持本地网页形式，使用 Python 标准库 HTTP 服务和浏览器原生视频控件：

- 顶栏保留播放速度和当前条目计数；键盘左右键切换视频，空格播放或暂停。
- 右侧提供人员标签按钮、固定标签下拉菜单和“添加行为”按钮。每个事件卡显示开始、结束输入框、视频当前帧截取和删除操作。
- 添加 `normal_scene` 时清除其他事件；添加正例时清除 `normal_scene`，均要求确认以避免误删未保存编辑。
- 保存草稿、审核并下一条、清空当前事件时间、待审核/已审核/需补时间筛选均可用。
- 草稿保存可记录部分时间，但状态保持 `pending`。审核正例前，所有事件必须有合法开始和结束时间；审核 `normal_scene` 不要求时间。只有“审核通过”操作会将 `review_status` 设为 `reviewed`。
- 每个 CSV 在首次写入时备份。API 验证样本 ID、人员标签、固定标签、去重事件类型、互斥规则和时间格式；无效请求不会改写 CSV。

简单 CSV（只有 `start_time` / `end_time`）继续使用单一时间段界面和原字段保存。多行为 CSV 使用事件卡界面。两种模式共享视频服务、人员标签、审核状态、备份、导航和筛选能力。

## 代码结构

主文件 `video_event_labeler.py` 保持单文件交付，使用标准库：`argparse`、`csv`、`json`、`pathlib`、`tempfile`、`shutil`、`http.server` 和可选的 `tkinter.filedialog`。不引入第三方依赖。

后端职责分为以下小函数：CSV 模式检测与读写、原子备份写入、事件序列化/解析、目录扫描、标签推断、视频路径校验、请求载荷校验。HTML、CSS 和 JavaScript 内嵌于脚本，避免额外部署步骤。

## 测试和验收

新增 `test_video_event_labeler.py`，以临时目录和本地 HTTP 服务验证：

1. 自动扫描、稳定生成 manifest，并且重复导入不重复写行。
2. `D:\dapeng-test` 形式的正负路径、`dog_out` 和组合文件名得到预期预填标签。
3. `neg` 优先生成 `normal_scene`，正例标签与 `normal_scene` 互斥。
4. 多行为保存会验证标签、时间顺序、唯一事件类型，并在首次保存前创建备份。
5. 简单 CSV 继续保存 `start_time` / `end_time`，不会被多行为处理破坏。
6. 目录穿越请求、无效人员标签和不存在的样本不会改写文件。

验收时运行单元测试、Python 编译检查和脚本 `--help`。同时用临时 CSV 启动服务并检查导入、保存、筛选接口响应。
