# 标注断点恢复设计

## 目标

浏览器刷新、暂时关闭页面或切换到其他窗口后，重新打开标注工具时自动回到上次工作的数据集、筛选器、播放速度和视频。恢复只影响浏览器界面状态，不改变 CSV 内容、审核状态或未保存表单值。

## 范围与约束

- 状态存储使用浏览器原生 `localStorage`，不增加后端接口和依赖。
- 状态键固定为 `video-labeler:resume:v1`，值为 JSON。
- 保存字段：`dataset`、`video_path`、`sample_id`、`filter`、`speed`。
- `dataset` 使用页面已有的“视频目录 / CSV 文件”显示值，用于避免在不同数据集间误恢复。
- 仅接受现有筛选值和 `0.5`、`1`、`2` 倍速；未知值丢弃并使用默认值。
- 只在当前数据集与保存状态的数据集一致时恢复；保存的视频不存在时回退到当前列表第一条。
- `localStorage` 不可用、读取失败、JSON 损坏或字段非法时静默回退到默认启动行为。
- 不保存 `dirty` 状态或事件编辑内容，防止把过期草稿伪装成已恢复数据。

## 组件与数据流

新增一个独立的浏览器脚本片段，挂在现有页面脚本之后：

1. 页面启动时读取并校验 resume JSON。
2. 包装最终生效的 `renderList()`。`load()` 拿到 `/api/status` 和 `/api/videos` 后第一次渲染列表时，脚本比较数据集；匹配时恢复筛选器和倍速，再调用原渲染函数。
3. 包装最终生效的 `openRow(index)`。首次打开行时按 `video_path` 优先、`sample_id` 兜底寻找保存的视频；找到则打开该行，否则保持 `load()` 的第一行回退逻辑。
4. 视频切换、筛选器变化、倍速变化和 `beforeunload` 触发轻量写入。没有当前行时不写入。
5. 写入失败只忽略本次保存，不阻断标注操作。

包装器必须保留原函数的返回 Promise 和参数，避免影响原有保存草稿、审核下一条和导航逻辑。

## 错误处理与边界

- `JSON.parse`、`localStorage.getItem`、`localStorage.setItem` 全部放在 `try/catch` 中。
- 恢复状态只消费一次；数据集不匹配或视频未找到时标记为已处理，避免后续导航反复跳回旧视频。
- 恢复筛选器后由原 `renderList()` 重新计算可见列表；如果筛选结果为空，仍遵循原页面空状态，不强行改筛选器。
- `sample_id` 仅作为兼容视频路径变化的次级匹配条件，路径优先保证同名样本不会误选。

## 测试策略

- HTML 合约测试确认：存在版本化 localStorage 键、字段名、JSON 读写保护、`renderList`/`openRow` 包装、路径优先和样本 ID 兜底、`beforeunload` 保存。
- 保留全部现有 API、CSV、Range、键盘快捷键和 HTML 规范化回归测试。
- 运行 `python -m unittest -q` 与 `python -m py_compile video_event_labeler.py test_video_event_labeler.py`。
