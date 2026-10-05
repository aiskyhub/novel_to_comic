# 《{{book_title}}》项目工程与目录结构规范

## 1. 项目整体架构

本项目顶层以书名命名，采用分卷独立工程与集中资料管理的架构：

```text
{{book_name}}/
├── README.md                   # 顶层说明与各模块导航入口
├── docs/                       # 项目介绍与设计规范模块文档
│   ├── overview.md             # 作品概述与分卷规划
│   ├── structure.md            # 项目结构说明（本文档）
│   ├── worldview.md            # 全书世界观与核心设定
│   ├── characters.md           # 跨卷核心角色档案与视觉基准
│   ├── art_direction.md        # 全书统一美术规范
│   └── progress.md             # 制作进度与各卷状态跟踪
├── source_texts/               # 原始小说文本文档归档目录（含 archive_manifest.json）
├── split_texts/                # 文本切割统一存放目录（含 split_manifest.json）
└── 第1卷/                      # 第1卷漫画制作独立工程（--project 目标）
    ├── README.md               # 本卷索引导航与核心状态速览
    ├── docs/                   # 本卷模块化说明文档目录
    │   ├── info.md             # 本卷基本信息与叙事焦点
    │   ├── status.md           # 制作状态看板
    │   ├── commands.md         # 常用操作命令速查
    │   ├── structure.md        # 本卷工程目录结构说明
    │   ├── notes.md            # 关键注记与跨卷人设继承
    │   └── deliverables.md     # 导出品交付路径与说明
    ├── project.json            # 制作状态与索引 (Schema v5)
    ├── full-script.md          # 本卷锁定的分镜剧本
    ├── source/                 # 提取的章节与原稿副本
    ├── scripts/                # 工作剧本与修订稿
    ├── design/                 # 卷内角色、场景及美术档案
    ├── art/                    # 参考图、画格、批次原图与裁切
    ├── prompts/                # 实际生成提示词
    ├── reports/                # 各阶段审查质检报告
    ├── pages/                  # 排版完成页面 PNG
    └── exports/                # 离线阅读器、PDF、CBZ 成品
```

## 2. 制作工作流

1. 原始小说放入 `source_texts/` 归档；
2. 如需按卷拆分，拆分文本存放于 `split_texts/`；
3. 各卷以子文件夹（如 `第1卷/`）作为 `--project` 执行 pipeline 流程；
4. 跨卷资产复用：后续卷可复用前卷已通过的人物设定与参考图基准。
