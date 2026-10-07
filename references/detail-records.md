# 可核查的章节细节记录

按章编剧与返修时读取。记录用于让省略和映射可追查，程序不自动判断剧情完整性，也不生成通过报告。

## 改编推断边界与三级证据模型

小说文学与漫画视听存在媒介差异。为防止“字字句句有出处”的要求将合理分镜误判为虚构，或迫使执行者伪造出处，系统严格确立**三级证据体系**：

1. **第一类：原文明示事实（Explicit Canonical Facts）——【严格忠实，必须有精准出处】**
   - 包含：剧情事件走向、对白核心意义与发言态度、既定身份与人际关系、因果动机、情节揭示、原文明确描写的场景与关键道具。
   - 契约要求：必须 100% 忠实原著，在 `details` 中具备真实 `source_unit_ids` 和原文短引句（quote），在分镜中以实际动作或台词呈现。
2. **第二类：视觉呈现选择（Visual Staging Choice）——【允许专业转译，标明视觉呈现】**
   - 包含：为在画格中具象化第一类事实所需的分镜机位（镜头景别/俯仰角度）、构图透视、分格节奏、肢体动作中间过渡、微表情神态、符合世界观的光影与环境细节。
   - 契约要求：属于漫画转译的合理艺术推断。在分镜设计中明确归为“视觉呈现（visual staging）”，无需牵强附会假造原文逐字引句，但必须服从角色性格基调、情节氛围与美术规范，严禁反客为主。
3. **第三类：新增实体/情节/因果（Fabricated Canonical Elements）——【绝对禁止，一票否决】**
   - 包含：原文未发生的新事件、私加感情线或平行冲突、改变故事走向的自创动机、与原作设定冲突的魔改、无中生有的虚假对白。
   - 契约要求：绝对严厉禁止。一旦发现，判定为 Major/Critical 缺陷，一票否决。

## 保存与检查

1. 从当前章完整原文提取全部独立叙事细节，保留真实 unit ID、短引句、事实和作用。先完成提取，再写详细场戏与分镜；不能从摘要或程序按段自动填一项。
2. 在 `docs/adaptation/<chapter_id>.md` 写详细场戏、细节映射、格数依据、取舍和章末交接。在 `scripts/chapters/<chapter_id>.json` 的 `chapter_adaptations` 数组中保存当前章的一条记录；汇总本卷时保留所有正文章记录。
3. 逐段重读原文，查清单是否漏提，再核对每项的实际呈现。填写 `unit_audits` 的真实发现，不写泛泛的“覆盖 100%”。有遗漏就补细节与分镜；结构通过不等于语义通过。
4. 保存 Markdown 后用 SHA256 计算文件真实字节指纹，写入 `document_sha256`。执行 `check-adaptation --project <当前状态目录> --chapter <真实章ID> --file <章节草稿JSON>`。不传 `--file` 时检查已导入本卷剧本中的该章。
5. 修复错误，保存输出 `draft_hash` 与实际语义核查结果后进入下一章。哈希只保存到工作记录/独立报告，不回填所哈希的草稿或所绑定 Markdown，以免自引用造成指纹永远变化。

`check-adaptation` 为只读命令，可用于未完成全卷的章节草稿，不要求其他章已编剧或已读。它返回 errors、error_count、draft_hash 和 `semantic_review_required: true`；有错误退出码为 2。它只检查细节记录和来源归档，不代替 `check-script` 的全卷结构、角色、连续性与页面检查，也不允许提前出图。

## `chapter_adaptations` 契约

本卷 script 必须有该数组，每个有正文的章节恰好一条记录；无正文章无需记录。记录属于剧本指纹的一部分，修改任何记录后旧审查不能沿用。

| 字段 | 内容 |
|---|---|
| chapter_id | 当前章真实 ID |
| document_path | 固定为 `docs/adaptation/<chapter_id>.md`，相对当前状态目录 |
| document_sha256 | 已保存章节文档的真实 SHA256；文件缺失、空白或变更时阻断 |
| details | 非空细节数组，按原文叙述与揭示顺序排列；每项独立可核对 |
| unit_audits | 每个正文 unit 恰好一条重新阅读核查记录 |

每个 detail 包含：

| 字段 | 内容 |
|---|---|
| id | 章内唯一细节 ID；不同章可以各用 d1，不当作原文 ID |
| kind | action/dialogue/thought/reveal/state/environment/paratext |
| sources | 非空 `[{"unit_id": "真实本章正文ID", "quote": "该 unit 中实际存在的短引句"}]`；关联多段时每段分别引句 |
| fact | 一个独立事实、行动、发言意义或心理变化，写明必要先后与触发 |
| narrative_function | 该细节承担的信息、因果、性格、关系、情绪、空间或伏笔作用 |
| treatment | shown/merged/repetition/paratext；未解决或遗漏不能标为完成 |
| presentations | shown/merged 时非空，指向实际画面或文字内容；其他处理为空数组 |
| reason | merged/repetition/paratext 必填具体理由；不能以篇幅或生成次数为省略理由 |
| retained_detail_id | repetition 必填本章已 shown/merged 的另一细节 ID，不允许自引用或重复链 |

每个 presentation 包含 `panel_id`、`field`、`excerpt`：

- `panel_id` 必须是本章实际画格，且其 `source_unit_ids` 包含该细节引用的原文单元。
- `field` 支持 `action`、`space`、`expression`、`dialogue.<从0开始的序号>.text`。状态变化须在这些实际画面或文字内容中呈现；仅修改 `state_before/state_after` 中的内部状态不算呈现。
- `excerpt` 必须为这个字段真实存在的非空摘句。来源 ID、事件描述或“该格已体现”的解释不能充当呈现证据。
- 多格呈现一个细节时列出多条证据；一格承担多个细节时分别列证据，人工检查同一时刻、空间、顺序及阅读容量是否兼容。

每个 unit_audit 包含 `unit_id`、`detail_ids`、`evidence`。`detail_ids` 非空且恰好列出从该 unit 提取的全部细节；`evidence` 说明实际重新阅读发现、补项或具体合并/保留依据。原文段落里未提取的独立信息必须补入 details，不能靠写一条审查记录掩盖。

`context` 信息用 shown/merged 细节指向真实画格，不能免除段内提取。只有无新增叙事作用的重复内容用 repetition；出版/格式文字用 paratext，且 kind 必须也是 paratext。有叙事作用的序言、书信、回忆、关键环境与场景氛围说明绝对不能归为 paratext。**特别注意：原著中的关键场景描写（environment）、核心剧情冲突与高潮推进（action/reveal）以及所有喜剧幽默笑点/吐槽梗（dialogue/action/thought）均具备重大叙事价值，绝不得作为无用信息省略或标为 repetition**。每 10 章阶段剧本审批时，双子代理将重点排查这些关键要素是否被完整提取并如实呈现。

## 双轨验收：机械门禁与人工语义审查

必须在流程与记录中严格区分**机械门禁通过**与**人工语义审查结论**：

1. **机械门禁（Automated Gate）**：
   - `check-adaptation` 与 `check-script` 校验 JSON 结构、字段合法性、原文单元引用、文件真实存在与 SHA256 指纹匹配。
   - **机械通过仅证明数据结构闭环与可追溯，绝不等于剧情未被遗漏，绝不等于模型真正精读了原文，绝不等于视觉转译合理**。
2. **人工/语义审查（Semantic Review）**：
   - 审阅人（或主代理）必须亲自通读原文与分镜，核查：
     - 清单是否遗漏了具有叙事作用的明示事实（第一类证据）；
     - 分镜的视觉选择是否准确服务于情节而非擅自加戏（第二类证据）；
     - 是否存在隐蔽的新增情节或虚构因果（第三类证据）。
   - 报告与 `unit_audits` 中的 evidence 必须明确指出具体原文段落、具体画格 ID、核查比对事实与问题修订记录，严禁填写泛泛的形式化词句（如“覆盖率 100%”或“机械检查通过”）。

## 版本与返修边界

`check-script`、`review`、`lock-script` 与有效出图锁均校验全卷记录和绑定文档。改写 Markdown 后先修订对应细节与分镜并更新文档指纹，通过 `set-script` 或 `set-script-chapter` 正常导入，重做受影响语义审查并形成同一最新剧本指纹的三类报告。不要只更新指纹而跳过核查。

`script-chapter` 返回选定章的 `chapter_adaptations`。`set-script-chapter` 要求恰好一条对应章记录，只替换该章，保留其他章记录；它仍只适用于已经有完整结构稿的单章返修。

版本 5 规范要求记录完备，若缺少记录，保留原稿和已生成资产，重新读取原文补齐记录并正常重审后才能重新锁定或出图；不自动生成细节、不修改历史报告。机械检查通过只证明记录可追溯、摘句真实存在、文档指纹一致；语义完整性与零虚构结论必须基于真实比对证据。
