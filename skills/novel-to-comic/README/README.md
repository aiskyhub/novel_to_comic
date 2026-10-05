# novel-to-comic 独立 Markdown 模版库与规范导航

本目录（`README/`）收录小说转漫画（novel-to-comic）流水线在初始化与各制作阶段生成的所有**独立纯粹 Markdown 模版文件**。

每个模版均为独立的 `.md` 文件，禁止合并硬编码，支持动态参数替换（如 `{{book_title}}`、`{{volume}}`）。

## 模版分类与文件列表

### 1. 书级项目模版（顶层脚手架）
- [book_readme.md](book_readme.md)：全书工程顶层 `README.md` 模版（含看板与全流程 SOP）
- [book_kanban.md](book_kanban.md)：全书分卷制作总览看板动态区块模版
- [book_overview.md](book_overview.md)：作品基本信息、全书分卷规划与题材定位模版（`docs/overview.md`）
- [book_structure.md](book_structure.md)：项目工程与目录结构规范说明模版（`docs/structure.md`）
- [book_worldview.md](book_worldview.md)：全书世界观、核心背景与跨卷通用设定模版（`docs/worldview.md`）
- [book_characters.md](book_characters.md)：跨卷核心角色档案与视觉基准指南模版（`docs/characters.md`）
- [book_art_direction.md](book_art_direction.md)：全书统一美术规范模版（`docs/art_direction.md`）
- [book_progress.md](book_progress.md)：全书制作进度看板与实施里程碑模版（`docs/progress.md`）

### 2. 卷级制作工程模版（分卷独立工作区）
- [volume_readme.md](volume_readme.md)：本卷工程 `README.md` 导航索引模版
- [volume_status_block.md](volume_status_block.md)：本卷关键状态速览动态区块模版
- [volume_info.md](volume_info.md)：本卷基本信息、原文来源与叙事焦点模版（`docs/info.md`）
- [volume_status.md](volume_status.md)：全阶段详细关卡状态看板模版（`docs/status.md`）
- [volume_commands.md](volume_commands.md)：本卷流水线常用操作命令速查模版（`docs/commands.md`）
- [volume_structure.md](volume_structure.md)：本卷工程目录结构说明模版（`docs/structure.md`）
- [volume_notes.md](volume_notes.md)：关键注记、跨卷人设继承与特别制作要求模版（`docs/notes.md`，支持人工增补）
- [volume_deliverables.md](volume_deliverables.md)：导出品交付路径与成品清单模版（`docs/deliverables.md`）
