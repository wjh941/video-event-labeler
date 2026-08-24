# 参考事件清单格式设计

## 目标

视频文件夹导入后生成的事件清单，必须与
`D:\0818 03\0818_cam03_clips_sample_sorted_by_start_event_labeler_manifest.csv`
使用相同的 CSV 表头、字段顺序、字段表达和事件时间格式，并仍可在本工具中人工补充或审核事件。

## 固定 CSV 契约

新建清单固定使用 UTF-8 with BOM 编码和下列九列，顺序不得变化：

```text
sample_id,video_path,lighting,lighting_evidence,behavior_class,behavior_id,security_zone_points,person_tag_list,events
```

- `sample_id`：视频文件名，包含扩展名，例如 `sample.mp4`。
- `video_path`：视频文件的绝对 Windows 路径。
- `lighting`：按文件名推断，`daytime` 为 `白天`，`night_black_white` 为 `红外`，其它含 `night` 的名称为 `黑夜`；无法识别时为空。
- `lighting_evidence`：新建行固定为 `人工确认`。
- `behavior_class`：与 `behavior_id` 中的标签一一对应，并用英文逗号按相同顺序连接。
- `behavior_id`：预选或人工确认的英文行为标签，用英文逗号按事件顺序连接。
- `security_zone_points`：新建行固定写字面量 `null`。
- `person_tag_list`：新建行初始为空；由人工选择后写入 `stranger`、`acquaintance` 或 `null`。
- `events`：使用旧多行为工具的多行 JSON 样式；每个事件时间以整数毫秒加 `ms` 保存，例如 `"start_time_ms":1710ms`。

工具不得再向新清单写入 `data_stratum`、`review_status`、`start_time`、`end_time` 或其他私有列。

## 行为映射

下列英文标签写入 `behavior_id`；对应中文名称写入同位置的 `behavior_class`：

| 行为标签 | 中文分类 |
| --- | --- |
| `person_fall` | 人员跌倒 |
| `climb_fence` | 翻越围栏 |
| `peep_car_window` | 窥视车窗 |
| `pickup_package` | 拾取包裹 |
| `linger_wander` | 徘徊 |
| `stay_long` | 长时间逗留 |
| `cat_enter_frame` | 猫进入画面 |
| `dog_enter_frame` | 狗进入画面 |
| `stranger_enter_frame` | 入侵 |
| `approach_risk_zone` | 靠近风险区域 |
| `normal_scene` | 正常场景 |

导入时，自动预选直接识别英文标签本身和现有别名（包括 `dog_out`），并按标签在文件名或相对路径中首次出现的位置排列。对于多行为视频，标签及分类遵循该预选顺序及后续事件添加顺序。例如 `stranger_enter_frame,linger_wander` 对应 `入侵,徘徊`。

## 导入、保存和审核

导入文件夹时，工具继续从文件名预选单个或多个行为，但事件初始时间为空，等待人工标注。对于新建清单和已符合本规格的参考清单，保存或审核时以固定九列顺序写回，并更新 `behavior_id`、`behavior_class`、`person_tag_list` 和 `events`。

审核状态不再写入新建清单或参考清单。界面只根据事件是否已填写有效起止时间显示“需补时间”或“可审核”；`normal_scene` 不要求时间。工具仍保留对旧版通用事件清单和简单起止时间清单的读取及原样保存支持，以便继续处理现有数据，但新生成的清单一律使用固定九列契约。

## 数据保护与验证

每次保存前创建原 CSV 备份，并以临时文件原子替换。测试必须覆盖：

1. 新文件夹导入生成精确的九列表头及顺序。
2. 生成行的文件名、绝对路径、照明、证据、区域点和行为分类符合本规格。
3. 单行为与多行为的 `behavior_id`、`behavior_class` 和 `events` 顺序一致。
4. 保存后 CSV 不出现工具私有列，事件仍使用 `ms` 且保留毫秒精度。
5. 参考 CSV 可以读取、显示并保存，保存后仍保留相同的九列顺序。
