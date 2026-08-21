# 视频事件标注工具

`behavior_class` 取导入根目录下的第一级文件夹名称。例如导入 `D:\dapeng-test` 时，`D:\dapeng-test\跌倒\pos\clip.mp4` 的类别为 `跌倒`。再次导入会刷新已发现视频的类别，但保留人工事件、时间和人员标签。

选择固定行为标签后，点击“新建事件片段”可新增独立的行为片段；同一行为可添加多段。每段的时间可精确录入或通过“截取”写入毫秒时间，填写有效起止时间后可点击“循环片段”反复检查该区间。右侧同时提供上一条、下一条和当前筛选进度，所有操作控件均位于侧栏，不遮挡视频播放区域。

无需安装第三方库。使用 Python 3 在本机启动后，浏览器打开 `http://127.0.0.1:8765`。

```powershell
cd 'D:\default file\视频标注工具'
python .\video_event_labeler.py
python .\video_event_labeler.py --video-root 'D:\dapeng-test'
python .\video_event_labeler.py --video-root 'D:\videos' --csv 'D:\videos\existing.csv'
```

不带参数启动后，点击“导入视频文件夹”选择目录。工具会递归扫描常见视频格式，并在该目录创建或增量更新 `video_labeler_manifest.csv`。已有标注不会因再次导入而覆盖。

新生成的清单使用 UTF-8 with BOM 编码，并严格采用以下九列及顺序，兼容 `0818_cam03_clips_sample_sorted_by_start_event_labeler_manifest.csv`：

```text
sample_id,video_path,lighting,lighting_evidence,behavior_class,behavior_id,security_zone_points,person_tag_list,events
```

其中 `sample_id` 是包含扩展名的视频文件名，`video_path` 为绝对 Windows 路径，`lighting_evidence` 为 `人工确认`，`security_zone_points` 为字面量 `null`。`events` 保持旧多行为工具的多行 `ms` 事件格式，时间精确到毫秒。新清单不会写入 `data_stratum` 或 `review_status`。

支持的行为标签：

`person_fall`、`climb_fence`、`peep_car_window`、`pickup_package`、`linger_wander`、`stay_long`、`cat_enter_frame`、`dog_enter_frame`、`stranger_enter_frame`、`approach_risk_zone`、`normal_scene`。

`pos` 路径中的视频会按目录和文件名预填正例标签；`neg` 路径会预填 `normal_scene`。标签本身及别名均可识别，`dog_out` 会预填为 `dog_enter_frame`。多行为按照标签在路径或文件名中的出现顺序预选，人工仍可增删和调整时间。

“保存草稿”可保存未完整的时间段；“审核并下一条”要求正例的每个行为都有合法的开始、结束时间，且结束必须晚于开始；`normal_scene` 不需要时间段。界面根据事件时间显示“需补时间”或“可审核”，审核状态不写入新清单。

首次改写已有 CSV 前，工具会在 CSV 同级的 `event_labeler_backups` 文件夹创建带时间戳的备份，并通过临时文件原子替换写入。工具兼容已有的多行为 `events` CSV，以及单个 `start_time` / `end_time` CSV；旧版文件会保留自身列结构。
