# 《{{title}}》· {{volume}} 工程目录结构

```text
{{root_name}}/
├── README.md                 # 本卷索引导航与核心状态速览（模块化入口）
├── docs/                     # 本卷模块化说明文档目录（避免单README过长）
│   ├── info.md               # 本卷基本信息与叙事焦点
│   ├── status.md             # 制作状态看板
│   ├── commands.md           # 常用操作命令速查
│   ├── structure.md          # 本卷工程目录结构说明（本文档）
│   ├── notes.md              # 关键注记与跨卷人设继承
│   └── deliverables.md       # 导出品交付路径与说明
├── project.json              # 当前卷制作状态、版本哈希、尝试账本与索引 (Schema v5)
├── full-script.md            # 通篇锁定的详细分镜剧本（锁定后自动生成）
├── source/                   # 提取的章节正文；originals/ 保存原稿副本
├── scripts/                  # 工作剧本、修订稿与项目专用辅助代码
├── design/                   # 本卷角色、场景、道具及美术档案
├── art/                      # 画面资产目录
│   ├── references/           # 已登记参考基准图（register-reference 保存）
│   ├── panels/               # 已验收画格 PNG（finish-panel 保存）
│   ├── raw/                  # 批次生成原图与编辑尝试
│   └── crops/                # 无损裁切画格（待逐格验收）
├── prompts/                  # 实际批次提示词文件
├── reports/                  # 剧本审查报告、参考图 QA 与画格视觉质检报告
├── pages/                    # 排版合成完成的页面 PNG
├── exports/                  # 离线阅读器、PDF、CBZ 导出品
└── versions/                 # 原文重提取等需要独立状态的内部版本
```
