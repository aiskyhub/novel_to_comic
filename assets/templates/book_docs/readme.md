# 《{{book_title}}》漫画项目

> 本项目为《{{book_title}}》小说改编完整漫画工程。项目顶层以书名为名称，分卷作为子文件夹进行独立制作与管理。

> ⚠️ **原著真实性绝对铁律**：所有漫画剧情、分镜镜头、台词对白，以及本 README 看板与 `docs/` 下的所有剧情描述，**必须 100% 严格忠于小说原文，绝对禁止胡编乱造、凭空加戏或脑补臆测！** 未提供或未读到的后续分卷，严禁猜测后文剧情，统一标明待后续输入。

{{kanban_table}}

## 🧭 怎么做：漫改全流程标准作业程序 (SOP)

本漫改项目以当前卷为独立制作与锁定单位，严格遵循以下不可跳过的制作顺序：

1. **原文归档与按卷切分**：
   - 原始小说文件统一存入 `source_texts/` 目录；
   - 若篇幅较长，使用 `split-source` 或手动按卷拆分小说，将切分文本存入 `split_texts/`（如 `vol_001_第1卷.txt`）。
2. **研读规范与世界观设定**：
   - 阅读 `docs/worldview.md`、`docs/characters.md` 和 `docs/art_direction.md`，确立全书统一的世界观规则、男女造型特征、核心角色层级与美术规范。
3. **分卷立项与工程初始化**：
   - 使用 `init` 命令初始化该卷制作子目录（如 `第1卷/`），自动生成该卷的 `project.json` 与本卷 `README.md`。
4. **通篇详细分镜剧本先行与三轮自校验（严禁胡编乱造）**：
   - 完整通读本卷原文，编制本卷详细可绘制剧本，所有分镜、对白与动作必须 100% 严格忠于原著，严禁魔改加戏，执行 `set-script` 导入；
   - 逐轮完成 coverage（事件覆盖与零虚构）、continuity（因果连贯与原著因果）、comic（镜头与排版）三轮审查并生成报告；
   - 通过 `lock-script` 锁定剧本并输出 `full-script.md`。**严禁在剧本锁定前出图！**
5. **角色与场景视觉基准登记**：
   - 通过 `assert-art` 关卡放行；建立男女主角多角度、全身与表情设定板；
   - 主代理看图验收合格后，通过 `register-reference` 登记基准图。续卷可直接复用前卷已通过的基准图。
6. **自适应多格生成与逐格验收**：
   - 按原生像素与展示尺寸紧凑合图，使用 `begin-batch` 登记批次 -> 绘图 -> `split-batch` 无损分格；
   - 主代理亲自看图，执行 `qa-inputs` 校验与 `finish-panel` 逐格验收。
7. **文字排版与页面合成**：
   - 使用 `compose` 进行本卷图文排版，生成整页 PNG；
   - 实际审阅所有页面后执行 `review-layout` 记录质检结论。
8. **多格式导出与最终验收交付**：
   - 执行 `export` 导出离线 HTML 阅读器、PDF 与 CBZ；
   - 渲染验证后执行 `complete` 记录交付，并更新本卷 `README.md` 与顶层总览看板。

## 📚 各种规范引导文件在哪（文档导航）

本项目将各维度的介绍与规范划分存放在 `docs/` 文件夹中，各模块职责与查阅时机如下：

| 规范引导文件 | 核心职责与内容 | 查阅与维护时机 |
|---|---|---|
| [docs/overview.md](docs/overview.md) | 作品基本信息、全书分卷规划、核心简介与题材定位 | 项目立项、分卷规划或向读者介绍全书时查阅 |
| [docs/structure.md](docs/structure.md) | 工程目录结构规范、各文件夹职责与文件流转规则 | 了解项目布局、维护工作流或排查文件路径时查阅 |
| [docs/worldview.md](docs/worldview.md) | 时代背景、地理势力、核心法则/能力体系与术语表 | 编剧分镜、场景空间设计与对白术语校验时查阅 |
| [docs/characters.md](docs/characters.md) | 跨卷核心角色档案、男女外观标准、设计层级与基准对照指南 | 角色人设、设定板制作、基准图登记与防撞脸核查时查阅 |
| [docs/art_direction.md](docs/art_direction.md) | 全书主美术方向（线条、配色、光影、背景留白、版式动线） | 风格确立、批次提示词撰写与整页视觉审美质检时查阅 |
| [docs/progress.md](docs/progress.md) | 全书制作进度看板、各卷实施里程碑与演进日志 | 制作推进、节点验收与换会话恢复进度时更新查阅 |

## 📁 项目目录结构

```text
{{book_name}}/
├── README.md                   # 本文件（项目总览、各卷看板、制作SOP与规范导航）
├── docs/                       # 漫画项目介绍与设计规范模块（随项目持续完善）
│   ├── overview.md             # 作品概述与全书分卷规划
│   ├── structure.md            # 项目工程与目录结构规范说明
│   ├── worldview.md            # 全书世界观、核心背景与跨卷通用设定
│   ├── characters.md           # 跨卷核心角色档案与视觉基准指南
│   ├── art_direction.md        # 全书统一美术规范（线条、上色、光影、版式）
│   └── progress.md             # 全书制作进度与分卷实施看板
├── source_texts/               # 原始小说文本归档目录（集中复制归档并校验副本，保留外部原稿）
├── split_texts/                # 文本切割统一存放目录（按卷/部拆分文本）
└── 第1卷/                      # 第 1 卷独立漫画制作工程（--project 目标）
    ├── README.md               # 本卷索引导航与核心状态速览
    ├── docs/                   # 本卷模块化说明文档目录（避免单README过长）
    │   ├── info.md             # 本卷基本信息与叙事焦点
    │   ├── status.md           # 制作状态看板
    │   ├── commands.md         # 常用操作命令速查
    │   ├── structure.md        # 本卷工程目录结构说明
    │   ├── notes.md            # 关键注记与跨卷人设继承
    │   └── deliverables.md     # 导出品交付路径与说明
    ├── project.json            # 本卷制作状态、哈希与索引
    ├── full-script.md          # 本卷锁定的分镜剧本
    ├── source/                 # 本卷提取的章节与原稿副本
    ├── design/                 # 本卷角色与场景档案
    ├── art/                    # 本卷参考图、画格、批次图与裁切
    ├── reports/                # 本卷质检审查报告
    ├── pages/                  # 本卷排版页面 PNG
    └── exports/                # 本卷阅读器、PDF、CBZ 成品
```

## 🚀 常用操作命令速查

```powershell
# 1. 拆分原稿到 split_texts/
& $python -X utf8 $cli split-source --book-dir '{{book_dir}}' --file 'source_texts/原稿.txt'

# 2. 初始化第 1 卷工程（自动生成第1卷/README.md）
& $python -X utf8 $cli init --project '{{book_dir}}/第1卷' --source '{{book_dir}}/split_texts/vol_001_第1卷.txt' --title '{{book_title}}' --volume '第1卷'

# 3. 查看第 1 卷制作状态
& $python -X utf8 $cli status --project '{{book_dir}}/第1卷'
```
