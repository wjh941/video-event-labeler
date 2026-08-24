# 负例标签优先设计

## 目标

视频路径或文件名包含独立负例标记 `neg` 时，自动预选必须只返回 `normal_scene`，即使同一路径还包含任意正例行为标签。

## 识别规则

在 `infer_prelabels` 中，对标准化相对路径使用分隔符边界识别 `neg`。分隔符为路径分隔符、连字符、下划线、空白字符、加号或文件扩展名前的句点。

以下名称均为负例：

```text
neg/cam01/fall.mp4
dog_out+fall-neg-001.mp4
stranger_enter_frame_neg_001.mp4
```

`negative_scene.mp4`、`negation.mp4` 等包含但未分隔 `neg` 的单词不是负例。

## 优先级与输出

负例判断先于 `pos` 分层和所有行为标签扫描。匹配后，函数返回 `("neg", ["normal_scene"])`；导入清单相应写入 `behavior_id=normal_scene`、`behavior_class=正常场景`，事件时间保持空值。

## 验证

测试必须覆盖文件名负例覆盖正例标签、目录负例覆盖正例标签、独立边界匹配，以及 `negative` 不被误判。完成后运行完整测试、语法编译并更新公开 Gist。
