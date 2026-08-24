# 毫秒时间与 Gist 发布设计

## 目标

将标注界面的时间显示和手工输入统一为固定三位毫秒精度，同时保持多行为 CSV 与旧 `多行为同时发生-multi_behavior_event_labeler.py` 完全相同的存储单位：整数毫秒加 `ms` 后缀。

完成验证后，新建一个公开 GitHub Gist，仅发布可运行源码、测试和说明文件。

## 时间规则

- 界面展示和截取结果一律为 `H:MM:SS.mmm`，例如 `0:00:01.234`、`0:00:01.000`。
- 浏览器截取使用 `Math.floor(video.currentTime * 1000)` 取得整数毫秒，避免超过当前帧的时间。
- 输入仍接受旧有的 `H:MM:SS` 和较短小数形式，但重新渲染时补齐为三位毫秒。
- Python 服务端保持以整数毫秒校验、保存和传输，不引入浮点时间字段。
- `events` CSV 的事件格式继续为：

```text
{"event_type":"person_fall",
"start_time_ms":1234ms,
"end_time_ms":2000ms}
```

这与旧脚本的 `events_to_csv_value` 结构一致。

## 测试

- 将 `format_time_text(62000)` 固定断言为 `0:01:02.000`。
- 将 `format_time_text(62250)` 固定断言为 `0:01:02.250`。
- HTML 级测试验证前端 `timeText(62000)` 和截取逻辑输出三位毫秒。
- 运行完整单元测试、Python 编译检查和本地状态接口检查。

## Gist 发布

公开 Gist 只包含：

- `video_event_labeler.py`
- `test_video_event_labeler.py`
- `README.md`

不包含视频、CSV、自动备份、截图、设计文档、产品文档或 `old` 归档目录。使用 GitHub CLI 的公开创建命令，在发布后返回 Gist 链接。

GitHub CLI 必须先能在当前会话中找到并已登录；若安装程序尚未完成或 `gh.exe` 未在 PATH 中，发布步骤会暂停，不会以其他方式上传你的内容。
