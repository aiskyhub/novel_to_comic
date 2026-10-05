# novel_to_comic

将小说或长篇故事忠实改编为完整漫画的 Codex 插件与通用技能体系。

严格忠于小说原著文本（绝对禁止胡编乱造漫画剧情与文档描述），通篇剧本先行与多轮自校验，按原生像素紧凑合图、逐格验收、排版和导出完整漫画。

## 核心特性

- **最高铁律：严格忠于原著**：所有漫画剧情、分镜镜头、台词对白、角色动作及工程文档描述，必须 100% 忠于小说原文，零胡编乱造、零脑补臆测。
- **独立卷制作流程**：以当前卷为独立制作与锁定单位。全卷完整剧本 → 多轮自校验与审查（覆盖度、因果连续性、漫画表达）→ 本卷锁定 → 基准人设制作与继承 → 正文自适应合图生成 → 逐格验收 → 排版导出。
- **画质自适应合图（Capacity-driven Batches）**：不设固定格数上限，根据画布原生像素容量与各镜头最小细节预算自适应规划合图批次，合并生成后无损分格，每格独立三次尝试与质检。
- **作品项目模块化文档**：顶层书名项目目录集中归档原稿（`source_texts/`）与切分文本（`split_texts/`），通过 `docs/` 模块化维护作品全局设定（`overview.md`、`worldview.md`、`characters.md`、`art_direction.md` 等）；各分卷独立维护卷级工程文档。

## 目录结构

```text
novel_to_comic/
├── .codex-plugin/
│   └── plugin.json                  # Codex 插件清单配置
├── skills/
│   └── novel-to-comic/              # 通用小说转漫画技能核心
│       ├── SKILL.md                 # 技能主入口及指令说明
│       ├── VERSION.json             # 技能版本信息
│       ├── agents/                  # 代理定义
│       ├── assets/                  # 模板与静态资源
│       ├── README/                  # 书名与分卷模块化文档规范模板
│       ├── references/              # 美术、分镜、指令、质量标准与架构规范
│       ├── scripts/                 # 批次规划、排版、流水线与切分脚本
│       └── tests/                   # 自动化单元测试与回归套件
├── validation/                      # 测试样本与技能包校验工具
│   ├── check_skill_package.py       # 技能包完整性校验
│   ├── sync_installed_skill.py      # 本地环境技能同步工具
│   └── 灯塔来信/                    # 验收样本项目
└── README.md
```

## 安装与使用

### 通过 Codex Marketplace 安装

在 Codex 中添加 aiskyhub 市场源并安装：

```bash
# 添加市场
codex plugin marketplace add aiskyhub/aiskyhub

# 安装插件
codex plugin install novel-to-comic@aiskyhub
```

### 技能调用

```text
$novel-to-comic 将我提供的小说忠实改编为完整漫画。
```

## 测试与校验

运行单元测试套件：

```bash
python -B -m unittest discover -s skills/novel-to-comic/tests -v
```

运行技能包格式与引用校验：

```bash
python validation/check_skill_package.py
```
