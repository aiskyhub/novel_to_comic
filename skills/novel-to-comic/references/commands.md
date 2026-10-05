# 辅助脚本调用

定位本技能 scripts/comic_pipeline.py，使用已发现的 Python。基本输入提取和关卡使用标准库；DOCX 需要 python-docx，PDF 提取需要 pdfplumber，排版/导出及字体覆盖检查需要 Pillow、reportlab、pypdf。先检查已提供的运行时依赖，不为了使用技能升级或改写全局环境。

Windows 命令形式：
```powershell
$comicPython = '实际发现的 Python 绝对路径'
$comicCli = '本技能 scripts/comic_pipeline.py 的绝对路径'
& $comicPython -X utf8 $comicCli status --project '作品绝对路径/书名/第1卷'
```

所有命令均带 `--project`。含空格、中文的路径始终作为单个参数传递，JSON 文件使用 UTF-8。程序输出 JSON；失败返回退出码 2。`check-script` 的 errors 非空同样返回 2。

## 统一项目目录与架构

项目顶层目录必须以《书名》（作品名）命名，包含项目整体说明、模块化介绍文档、原始文本集中归档、文本切分目录，以及各分卷独立制作子目录（`--project` 目标）：

```text
<书名>/
  README.md                   # 顶层说明文件，解释当前漫画项目整体结构，引导阅读各个模块文档
  docs/                       # 统一存放项目各维度介绍与规范文档，随制作推进不断完善
    overview.md               # 作品概述、分卷规划与核心故事梗概
    structure.md              # 项目工程与目录结构规范说明
    worldview.md              # 全书世界观、核心背景与跨卷通用设定
    characters.md             # 跨卷核心角色档案与视觉基准指南
    art_direction.md          # 全书统一美术规范（线条、上色、光影、版式）
    progress.md               # 全书制作进度与分卷实施看板
  source_texts/               # 原始小说文本文档归档目录（创建书名项目时将小说移动至此）
    原稿.txt
  split_texts/                # 文本切割统一存放目录（按卷/部/篇拆分）
    第1卷.txt
    第2卷.txt
  第1卷/                      # 第 1 卷独立漫画制作工程（--project 目标）
    README.md                 # 本卷索引导航与核心状态速览（避免单文件过长）
    docs/                     # 本卷模块化说明文档目录
      info.md                 # 本卷基本信息、原文来源与叙事焦点
      status.md               # 全阶段详细关卡状态看板
      commands.md             # 本卷流水线常用操作命令速查 (PowerShell)
      structure.md            # 本卷工程目录结构与子目录职责说明
      notes.md                # 关键注记、跨卷人设继承与特别制作要求
      deliverables.md         # 导出品交付路径与成品清单 (HTML/PDF/CBZ)
    project.json              当前卷制作状态与索引（包含 title 与 volume）
    full-script.md            当前卷锁定的通篇剧本
    source/                   提取的章节；originals/ 保存原稿副本
    scripts/                  工作剧本、修订稿与项目专用辅助代码
    design/                   人物、场景、道具及美术档案
    art/references/            已登记参考图（helper 保存）
    art/panels/                已验收画格（helper 保存）
    art/raw/                   批次生成原图、未采用图和编辑尝试
    art/crops/<批次ID>/         无损裁切画格（尚需逐格验收）
    prompts/                  实际提示词（helper 保存）
    reports/                  剧本、参考图、画格、页面与最终质检报告
    pages/                    排版页面 PNG（helper 保存）
    exports/<input_hash>/      阅读器、PDF、CBZ（helper 保存）
    tmp/                      本项目临时文件与预览
    versions/                 原文重提取等需要独立状态的内部版本
  第2卷/
    project.json              第2卷制作状态与索引
    ...
```

- **原文移动与归档**：在创建书名项目时，原始小说文本应移动到书名项目的 `source_texts/` 目录中集中保管，不散落在项目外部或临时目录。
- **切割文本统一存放**：长篇小说按卷或篇幅切割时，所有切割出的子文本统一存放在 `split_texts/` 目录中。
- **书名顶层 README.md（全书总览与规范导览）**：书名项目根目录下的 `README.md` 承担全书层面的三大职责：
  1. **分卷制作总览看板**：总览每一卷之类的信息（对应原文范围、工程子目录、核心剧情看点、制作阶段、导出品交付成果）。**铁律要求**：看板中的“核心剧情看点”必须严格基于已读已确认的原文提炼，严禁凭空编造或夸大剧情；未读或尚未立项的后续分卷，严禁臆测后文，统一标明“待后续原文输入后提取真实剧情”。
  2. **漫改全流程标准作业程序 (SOP)**：清晰阐明“应该怎么做”（原文归档切分、研读规范、分卷立项、通篇剧本三轮校验锁、基准图登记、多格生成与验收、页面合成、多格式导出交付等不可跳过的八步流程，全流程严格忠于小说文本，绝对禁止胡编乱造）；
  3. **各种规范引导文件导航**：明确指出“规范引导文件在哪”，索引并引导阅读 `docs/` 文件夹中的模块文档（`overview.md` 作品概述、`structure.md` 目录规范、`worldview.md` 世界观体系、`characters.md` 角色视觉基准、`art_direction.md` 美术规范、`progress.md` 制作看板），且这些模块随制作推进持续丰富。
- **分卷独立 docs 体系与精简 README.md**：每一卷制作子目录（如 `第1卷/`）内必须设立 `docs/` 文件夹并维护独立的精简 `README.md`。单一 README 文件如果过长，会导致 AI 在每次处理或读取时上下文膨胀、理解变慢甚至遗漏关键指令；因此将本卷信息按功能拆分至 `docs/` 模块文件中，卷根目录 `README.md` 仅作为核心状态速览和模块索引入口：
  1. `docs/info.md`：本卷基本信息、原文来源文件、章节数、有效段落数、本卷叙事焦点（必须严格基于本卷已提取正文真实提炼，严禁脑补）；
  2. `docs/status.md`：本卷详细制作状态看板（阅读、剧本锁与哈希、基准参考、画格通过/计划数、排版、导出）；
  3. `docs/commands.md`：针对本卷 `--project` 的状态查看、审查、锁定、基准登记、生成验收、排版导出常用命令；
  4. `docs/structure.md`：本卷工程目录结构及各子目录资产流转路径说明；
  5. `docs/notes.md`：关键注记、跨卷人设继承与特别制作要求、叙事备忘；
  6. `docs/deliverables.md`：导出品交付路径与成品清单（阅读器、PDF、CBZ）。
  > 💡 运行 `init`、`lock-script`、`export`、`complete` 等命令时，脚本会自动创建和维护更新本卷的 `docs/` 文件与精简 `README.md`。
- **跨卷资产复用**：后续卷可复用前卷已通过的人物与场景参考图基准（`art/references/` 与 `design/`），保持全书画风和形象高度统一。

`set-script --file`、`review --file`、`begin-batch --plan --prompt` 及各类 QA 输入先写入当前卷的项目子目录。实际批次提示词按批次 ID 区分文件名；各格尝试指向同一次实际提示词，报告按审查类型与版本区分，避免覆盖仍被记录引用的文件。项目内自编资料和阅读器引用优先使用相对路径；工具调用使用实际绝对路径。工具输出目录无法指定时，保留返回的真实路径并复制原始输出到 `art/raw/`，再执行登记命令。技能程序、运行时、系统字体和用户外部原稿可保留原位置，作品相关副本与成果归档在项目内。

`init` 要求目标新建或为空：先初始化，再添加项目资料。续做先读取当前卷目录的 `versions/current.json`（若存在），定位当前状态目录，再对其已有的 `project.json` 执行 `status`；没有版本指向记录时使用卷目录。原文变更需要重新提取时，按 [recovery.md](recovery.md) 在 `versions/` 内建立状态，不改用同级目录。仅剧本、画风、排版或返修变化继续使用当前状态目录。交付时给出书名顶层目录与其中的各卷成品路径。

| 命令 | 其他参数 | 行为 |
|---|---|---|
| init-book | --book-dir 目录 [--title 书名] [--source 原文...] [--action move/copy] | 一键建立书名项目、移动原文至 source_texts、建立 split_texts、生成 docs/ 模块模板与顶层 README.md |
| split-source | --book-dir 目录 --file 原文 [--output-dir 目标目录] [--pattern 正则] | 将长篇原文按卷切分并统一输出到 split_texts/ 目录 |
| init | --source 一个或多个文件 [--title 书名] [--volume 卷名] | 新卷目录提取输入，创建空剧本与索引，生成本卷 README.md |
| chapter | [--chapter ID] | 读取真实原文单元；长篇一次只读所需章节 |
| resolve-issue | --id 问题ID --evidence 证据 | 记录已经查明的提取问题 |
| confirm-source | --note 证据 [--scope 范围说明] | 确认获取范围；所有提取问题须解决 |
| doctor | [--project 卷目录] | 只读检测 Python 环境、系统依赖 (Pillow, reportlab, pypdf)、字体及工程状态 |
| preflight-typeset | [--font 字体路径] | 在出图前预检台词长度、字符密度与排版可行性，避免绘图后无法排版 |
| mark-read | --chapter ID --note 实际阅读笔记 | 记录实际读完的章节 |
| set-script | --file 完整或工作中剧本JSON | 导入稿件，变化使锁失效 |
| check-script | 无 | 结构、引用、覆盖与状态检查，输出 script_hash |
| review | --kind coverage/continuity/comic --file 报告JSON | 记录实际通过且版本一致的本卷审查 |
| lock-script | 无 | 三轮齐备且通过，冻结并输出 full-script.md，更新本卷 README.md |
| assert-art | 无 | 任何生成/编辑图像前的本卷关卡 |
| qa-inputs | 新参考：--file 图像，加 --bindings 绑定文件或 --characters ID…；已登记参考：--reference ID；pending 画格：--panel ID --attempt N --file 图像 | 只读输出 QA 所需图像与输入指纹，不生成检查结论 |
| register-reference | --file 图像 --qa 报告JSON；--bindings 绑定JSON 或 --characters 角色ID… | 登记基准并返回 reference_id；--characters 便捷入口绑定 base |
| bind-panel | --panel ID --bindings 绑定JSON | 明确绑定形态和参考 ID，不修改冻结剧情 |
| begin-batch | --plan 含canvas_pixels的批次计划JSON --prompt 实际提示词TXT | 容量与全组条件通过后一次登记非空画格组；部分复用须先重排，无固定格数上限 |
| split-batch | --batch 真实批次ID --file 原图 --regions 像素格区JSON | 归档原图并无损提取，像素不足逐格拒绝；不自动验收 |
| finish-panel | --panel ID --attempt 尝试号 --file 图像 --qa 报告JSON | 校验并复制通过的画格 |
| fail-panel | --panel ID --attempt 尝试号 --reason 原因 [--category 类别] [--outcome failed/cancelled/stale] | 结算 pending 尝试；支持 9 大失败分类，不重置尝试上限 |
| compose | [--font 字体路径] | 通过画格 → 图文排版 PNG |
| review-layout | --file 报告JSON | 全部页面实际看图后登记 |
| export | 无 | 导出离线阅读器、PDF、CBZ，更新本卷 README.md |
| verify-export | 无 | 验证实际交付内容与顺序 |
| complete | --file 最终报告JSON | 记录真实最终验收，准确报告本卷交付完成，更新本卷 README.md |
| status | [--plan 单批计划或分组队列JSON] | 核验进度、逐格阻塞、未结算尝试与原稿警告；提供计划时返回容量和复用清单 |
| script-chapter | --chapter ID | 只读取所需章节及相关人物、场景和相邻状态 |
| set-script-chapter | --chapter ID --file 章节JSON | 合并单章修改并使本卷锁失效 |
| impact | --file 候选完整剧本JSON | 只读报告修改影响 |
| preflight | [--plan 单批计划或分组队列JSON] | 统计画格尝试、生成批次、pending 与剩余任务；返回 planned_batches 容量与复用清单，估算调用 |

`init` 不调用模型；`set-script` 不替你编剧；`review` 不代替阅读；`begin-batch` 不自动出图；`split-batch` 不自动通过 QA；`compose` 不重绘画面。模型负责这些创作与判断，脚本负责可靠的机械步骤。

生产队列：先跑 status，恢复 pending 批次，再按画布容量和画质规划未完成格。preflight --plan 的 planned_batches 返回最低所需画布、复用/待生成/pending 清单；混有复用格时移除它们，重新排满画布并重写提示词，再启动。begin-batch 对部分复用计划拒绝且不登记尝试；返回 generation_required=false 时全部复用，不出图。否则只绘制 panels 中的实际画格，并使用返回的 prompt 文件。拿到图后 split-batch，再逐格 qa-inputs 与 finish/fail。适合的失败格可合并返修，pending 必须结算后才能重试；分组变化不刷新三次上限。

页面与交付文件有 input_hash。用 project.json 中最新 layout.input_hash 和 layout.pages[].id 填入对应报告；不要沿用旧报告或猜页数。

图像实际生成并亲自查看后，先用 `qa-inputs` 读取 `image_sha256` 与 `reference_visual_key` 或 `attempt_bindings`，将这些返回值原样加入实际视觉报告。`begin-batch` 在每个实际画格中返回 render_hash；`qa-inputs` 校验它仍对应本次输入。不得自行改写历史哈希或把旧报告套到新图。

`preflight_counts.initial_panel_attempt_budget` 是首次全稿按每格三次计算的预算，不包含未规划的后续视觉修订。`attempts_recorded`/`pending_attempts` 保留全部历史与未结算次数；`current_input_attempt_slots` 只合计参考有效的当前输入尚余次数，参考缺失的画格计入 `panels_with_unknown_budget`，不能当作零预算或宣称可直接开工。`attempts[].current_input` 为 true/false/null；null 表示因参考无效无法核对输入，旧 pending 仍应结算，不抹除历史。

新版初始化自动归档输入到 source/originals，数据统一使用 schema_version=5。其他版本明确拒绝，不提供迁移，不修改旧项目。详细字段及示例见 schemas.md。

`preflight_counts.generation_batches_recorded` 是登记批次数，batches_with_raw 是已归档输出的批次数；它们不是平台计费计数或剩余额度。pending_batches 按仍含 pending 格的批次统计。planned_generation_calls 仅在传入 --plan 时按该分组中尚未通过且不在 pending 的画格估算，未传时为 null；ungrouped_panels_remaining 显示尚未纳入估算的格数。计划包含被阻塞格时仍须查看 panel_blockers，不能把估算当作已具备生成条件。
