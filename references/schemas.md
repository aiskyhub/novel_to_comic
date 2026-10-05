# 项目与数据契约（版本 5）

`project.json` 由 helper 创建和更新，位于各分卷制作子目录中，包含 schema_version（5）、title（书名）、volume（卷名，如“第1卷”）、source、script、reviews、script_lock、art、layout、exports、final_review。书名顶层目录维护全书 `README.md`、`docs/` 模块化介绍文档、集中归档的 `source_texts/` 与切分后的 `split_texts/`。以当前卷为独立制作与锁定单位；其他卷尚未编剧、审查或完成，不阻塞当前卷制作与交付。不要直接更改 source、锁、尝试号或完成记录来放行。编剧时在当前卷状态目录的 `scripts/` 中写完整 script JSON，通过 set-script 导入；报告保存到 `reports/`。统一项目分卷布局见 [commands.md](commands.md)，原文新版本的内部状态目录见 [recovery.md](recovery.md)。仅支持 schema_version=5；其他版本明确拒绝，不提供迁移，也不修改已有旧项目或补历史通过结论。

## Source

files 记录原始输入绝对路径、项目内 archive_path、格式、编码和 SHA256；制作使用归档副本，外部原稿变化单独警告；chapters 使用顺序 ID（不以原章号为唯一键），保留标题、正文存在性、阅读记录；units 记录 id、chapter_id、kind、text、locator。TXT 用行号，DOCX 用段落/注释位置，PDF 用页与行，EPUB 用 spine/member/段落。

集中归档在 `source_texts/` 下生成 `archive_manifest.json`，切分原稿在 `split_texts/` 下生成 `split_manifest.json`。同名原稿自动附加序号与来源映射，切分记录各段落指纹与范围，防止覆盖与漏文。

读取 `chapter` 的输出获取真实 unit ID，不猜编号。所有正文 unit 均需映射，章标题本身不用绘制。source_index_hash 覆盖不可变来源索引，阅读笔记不改变它。issues 需要逐项查明；空章可通过注明确实无正文解决，不代表补画该章。

## Script

使用 assets/script-template.json 的结构。以下为必填字段：

| 对象 | 字段与要求 |
|---|---|
| 本卷 | outline：本卷结构与核心故事梗概（必须 100% 严格提炼自已确认正文，绝对禁止脑补编造）；ending：本卷实际原文真实收尾 |
| style | genre、look、palette、selection_reason；format=pages/strip；reading_direction=ltr/rtl；可配 width、height、font_size、max_segment_height、font_path；新项目使用 art_direction 记录具体美术规范，references 保存实际参考记录 |
| character | id、name、aliases 数组、importance=major/supporting/minor；narrative 含 goal/motivation/voice/arc（必须忠实于原著人设事实）；visual 含 face_shape/eyes/brows/nose_mouth/body/posture/hair/age；source_facts、design_notes 数组 |
| source_fact | text 与非空 source_unit_ids；必须为原文真实事实，设计注记不能写成原作事实 |
| setting | id、description；可补空间布局、道具、参考路径/指纹与设计注记 |
| event | id、description（严格对应原著真实事件，绝不捏造新情节）、非空 source_unit_ids；每个事件必须在画格中出现 |
| scene | id、chapter_id、setting_id；可补时刻、读者/人物知情状态、叙事层级 |
| panel | id、chapter_id、scene_id、source_unit_ids、event_ids、cast、action、shot、space、expression、state_before、state_after、dialogue（台词对白/旁白必须严格忠实于原著语义，严禁私自编造加戏）；可补 visual_plan 对象记录视觉中心、层次与局部色彩等 |
| page | id、chapter_id、按阅读顺序排列的 panel_ids；columns=1/2；可用 rows 指定每行一格或两格，混合整宽和双格行 |
| continuity_handover | opening_state 与 closing_state，记录本卷开场继承与结末状态（人物状态、世界规则、未回收伏笔），供跨卷交接审查核验 |
| source_disposition | unit_id、kind=context/repetition/paratext、reason；context 还需真实 panel_ids |

panels 数组就是本卷镜头顺序；pages 按此顺序覆盖每格恰好一次，不能遗漏、重复或改变顺序。相同章号的不同卷使用不同内部 chapter_id。所有分镜内容严格忠于原著，严禁胡编乱造。

### 来源引用与跨卷命名空间

`source_unit_ids` 支持本卷短字符串 ID（如 `"u0000001"`）与跨卷命名空间对象：
```json
{
  "volume_id": "vol-001",
  "source_index_hash": "64位SHA256指纹",
  "unit_id": "u0000002"
}
```
跨卷继承事实与设定时必须使用带命名空间的对象，严禁直接使用前卷短 ID 混入本卷。流水线会校验跨卷项目的 `source_index_hash` 与单元存在性，避免编号碰撞或静默失效。

art_direction 的字段及决策方法见 [art-direction.md](art-direction.md)；模板的默认规范需按本作调整。references 的建议记录为 `{work,url,scope,access,observations,adaptation}`，access=viewed/metadata_only/unavailable；仅在实际看过画页时填写具体观察，不要求为了锁定剧本额外上网。已有项目可以沿用原来的 style，补美术规范时通过 set-script 正常重审。

rows 为画格 ID 数组的数组，例如 `[["p1"],["p2","p3"],["p4"]]`；每行一格占满可用宽度，两格默认平分，可用 row_weights 调整；展平后必须与 panel_ids 完全一致。rtl 仅改变一行的物理摆放，JSON 保持阅读顺序。省略 rows 时按 columns 自动分行，末行只有一格则使用整宽。

状态按角色 ID 保存对象，例如 `{ "form":"base", "costume":"coat-a", "injuries":[], "items":[], "location":"room-a", "knowledge":[] }`。各 cast 都有 state_before/after。相邻出场的已记录状态发生变化时，在当前格 state_transitions 填 `{character_id,fields,reason,source_unit_ids}`，解释状态间变化；直接呈现的变化仍要在 action/events 中有依据。

dialogue 为 `{kind,speaker,text,anchor?}`。kind 是 speech/thought/caption/sfx；speech/thought 的 speaker 必须在本格 cast 中。anchor 是 0..1 画面坐标，对应图中该说话人的位置；默认排版器使用其水平分量。文字原样保留，脚本自动添加说话人标识不改变对白正文。

ID 使用稳定、短且适合文件名的字母数字/连字符，避免路径符号。角色形式扩展、关系、道具与阶段设定可作为额外字段保存在相应对象；明确它们的来源与生效范围。

## Reports

审查/视觉报告不自动生成 pass。编剧或制作模型实际完成检查后保存 JSON；程序核验完整性与版本。

剧本报告：
```json
{
  "script_hash": "使用 check-script 输出的实际指纹",
  "reviewed_chapter_ids": ["全部有正文的实际章节 ID"],
  "checks": {"该审查类型的每个检查项": true},
  "evidence": "实际对照位置、发现、修订和复查结果",
  "issues": [{"severity":"major", "description":"具体问题", "resolved":true}]
}
```

coverage 检查项：all_source_read/events_preserved/arcs_preserved/ending_preserved（必须核对原著事件 100% 覆盖且零虚构事件）。
continuity：causality/timeline/identity/states/knowledge_and_reveals（核对因果与状态逻辑 100% 来源于原著，严禁自设因果）。
comic：drawable_panels/dialogue_and_speakers/reading_order/pacing/text_density（核对分镜可绘制且台词/旁白忠于原著，严禁捏造台词）。

参考图报告检查项：identity/distinctiveness/angles_and_expressions/source_faithfulness/gender_readability/body_design/design_tier_fit/visual_elegance。
画格报告：identity/continuity/composition/drawing_quality/no_unwanted_text/gender_readability/distinctiveness/body_design/design_tier_fit/visual_elegance/native_detail。
画格报告的 reviewed_ids 和 attempt_bindings 只能包含当前一个画格，不共用整图结论；另需非空 detail_notes，记录裁切图在原生尺寸和成品阅读尺寸的面部、线条、动作、道具细节及实际比较对象。
以上视觉报告共同需要非空 evidence、实际 reviewed_ids、image_sha256 与 findings；参考还需 comparisons 和 reference_visual_key，画格还需 attempt_bindings（具体格式见下文）。

页面报告另外需要 `input_hash` 和全部实际 `reviewed_page_ids`；检查项 text_accuracy/reading_order/speaker_assignment/face_visibility/visual_elegance。
最终报告需要 `input_hash`；检查项 source_scope/story_complete/visual_consistency/exports_opened，并在 evidence 中说明已核查顶层及分卷 `README.md`、`docs/` 模块文档，确认所有剧情描述 100% 真实忠实于小说原文，无任何脑补臆测。

## Artifacts 与版本

参考图绑定角色设计与画风指纹，保留真实文件 SHA256 和 QA。画格尝试绑定视觉输入（含 visual_plan）、实际提示词、人物参考、尝试号和生成时全书指纹。只更改对白可以复用视觉输入相同的图；参考/状态/动作等改变则不能复用。

layout 保存全部实际页面/分段、画格顺序、字体和 PNG 指纹；exports 保存各格式文件的指纹。final_review 与实际输入一致且文件仍完整时 status 才会给 complete=true。

页面指纹包含排版器版本；更新排版实现后需要重新 compose、实际看页、review-layout 和 export。画格输入不变时复用有效绘图，无需重新生成。

额外场景/道具图片参考写入对应对象的 reference_paths/reference_hashes，均使用项目内相对路径与实际 SHA256，数组长度一致。画格通过 prop_ids 指定使用的道具；发出提示词前核验真实文件内容，改变参考会使关联绘图失效。


## 人物与制作绑定

character 保留 importance，新增 design_tier=lead/core/background；appearance={gender_presentation,requirements,source_unit_ids}，gender_presentation=feminine/masculine/source_defined，表示美术表达而非程序推断身份。source_defined 必须说明原作特殊设定及有效来源。identity_card 包含 face/eyes_brows/nose_mouth/body/posture/temperament 非空描述及非空 invariants 数组。

appearance_versions 是包含 base 的数组，每项含 id、description、非空 visual 对象；版本 visual 描述形态差异，保留身份锚点。comparison_with 指定需比较的角色；distinctions 含 other_character_id、至少两项 face_differences、body_difference、performance_difference。原作要求相似性时用 source_exception={reason,source_unit_ids} 替代强行差异。

参考登记绑定文件：
```json
{"purpose":"combined","subjects":[{"character_id":"角色ID","version_id":"base","region":null}]}
```
purpose=portrait/full_body/turnaround/expressions/pose/combined；region=null 或归一化 [x,y,w,h]，多人板应明确区域。登记返回 reference_id。

画格在文本剧本中必须指定每位 cast 的 appearance_versions；绘制前通过 bind-panel 提交生产绑定：
```json
{"appearance_versions":{"角色ID":"base"},"reference_ids":["登记返回的真实ID"]}
```
绑定存入 art.bindings，不修改冻结剧情，必须覆盖实际 cast 的正确形态。一个画格可组合多张角度/表情参考；新参考不替换旧绑定。参考错人、错形态或内容变化会阻塞。空 cast 的环境格可使用空人物绑定。

报告检查项见上文 Reports。参考 reviewed_ids 为真实人物 ID，正文为画格 ID；comparisons=[{character_ids:[A,B],evidence}] 记录真实比较。`image_sha256` 必须匹配实际验收文件。参考、正文与页面共同含 `elegance_notes={linework,color_and_light,visual_hierarchy}`，各项为实际观察的非空描述，分别说明线条、颜色光线和视觉层次。填写布尔值不替代看图，重大未解决缺陷不能通过。

参考报告的 `reference_visual_key` 绑定图像内容、角色设计、用途与角色/版本/区域；画格的 `attempt_bindings={"实际画格ID":{"attempt":实际尝试号,"render_hash":"实际视觉输入指纹"}}` 绑定本次尝试。使用 `qa-inputs` 读取这些机械字段，再由主代理实际看图填写证据与检查结论；命令不生成通过报告。`begin-batch` 为每个实际画格返回 render_hash。登记同图、同设计与同参考语义的新 ID 不会刷新视觉输入的尝试预算，参考区域或用途发生实际变化则需要复核。

## 页面、气泡与影响范围

page.narrative={purpose,new_information,emotion,focus_panel_id,page_turn} 用于记录页面任务、信息、情绪、重点与翻页关系；新增创作应填写。page.row_weights 可与实际 rows 平行，例如 [[1],[0.6,0.4],[1]]，每行数量对应画格且所有权重大于零。rtl 按叙事顺序反转物理摆放，保留不等宽画格自己的权重。

panel.aspect_ratio 为目标宽高比。完整图像等比例容纳，不能裁掉叙事内容。改画幅先检查构图；同样的图像可完整容纳时重新排版，明确需要构图变化时修改 visual_plan 并返修。

lettering_mode=band/bubbles（画格优先于 style，默认 band）。bubbles 模式每句对白恰好对应一项 bubbles={dialogue_index,rect:[x,y,w,h],tail:[x,y]或null,order}；坐标为画格图像区的归一化坐标，order 从 0 连续且不重复。文字及说话人只取 dialogue。可用 protected_regions=[归一化矩形] 指定必须避让区域；程序检查几何碰撞，主代理仍亲自检查面部与动作。

compose 保存可编辑排版 manifest、字体内容及各页面指纹。页漫画布严格等于 width×height，内容溢出时调整相应页面的行、气泡或分格并重审；条漫按完整行拆为不超过 max_segment_height 的片段。字号、字体、页序、对白和气泡变化只更新排版；视觉风格、角色造型、动作、构图和实际参考变化才影响绘图。

script-chapter 输出章节集合及相关人物、场景与相邻状态；set-script-chapter 仅合并该章的 events/scenes/panels/pages，完整冻结关卡仍检查全书。impact 对候选完整剧本给出修改影响且不写项目。


## 批次计划、裁切与尝试

单格和多格都使用 begin-batch；计划不修改冻结剧情，只描述本次生成的制作格区和最低像素。panels 为非空数组，无固定格数上限；ID 不重复、按剧本顺序排列，可跨成品页面。target_region 是归一化 [x,y,w,h]，区域不能重叠，单格必须为 [0,0,1,1]。min_pixels 是最低原生 [width,height]，不能低于成品展示尺寸。没有 aspect_ratio 时用计划像素比例确定预期高度，实际验收再按裁切图与排版画幅核查无需放大。

canvas_pixels 为必填的正整数 [width,height] 规划目标，各格区域份额必须能容纳其 min_pixels。所需最低画布由每方向最大的 ceil(min_pixels/区域份额) 决定，仅消除一个浮点 ULP 的整数边界误差；规划画布与所需画布每边不超过 12,000、总像素不超过 24,000,000。预算不足启动前拒绝，且不登记尝试；不能用扩大规划值冒充工具已经具备大图能力。

```json
{
  "canvas_pixels": [3072,1024],
  "panels": [
    {"panel_id":"p1","target_region":[0,0,0.5,1],"min_pixels":[1536,1024]},
    {"panel_id":"p2","target_region":[0.5,0,0.5,1],"min_pixels":[1536,1024]}
  ]
}
```

以上尺寸只是计划示例，不能当作工具保证；按作品真实画幅、复杂度与输出能力调整。assets/batch-plan-template.json 提供紧凑多格规划示例，同样不代表该尺寸和复杂度已通过实际出图验证。begin-batch 返回 batch_id、panels（含 attempt/render_hash/reference_ids）、canvas_pixels、required_canvas_pixels、reused、prompt 和去重 referenced_image_paths；generation_required=false 表示全部复用，无需生成。混有已通过与未完成格时拒绝，须先排除复用格、重排剩余格区并重写提示词，不自动沿用或修改旧格区。

实际格区 JSON 以本次生成的每个画格 ID 为键，值是整数像素 [x,y,w,h]，必须恰好覆盖实际生成格，不包含 reused。示例：

```json
{"p1":[0,0,1536,1024],"p2":[1536,0,1536,1024]}
```

split-batch 先保留原图，再检查格区结构；结构错误不提取，像素不足的格单独加入 rejected_panels，其他格提取保留。返回 panels 中包含实际可用裁切的 file、sha256、region、width、height 及原尝试绑定；尚未通过 QA。登记过的裁切格区不可更改，未提取格可修正格界后用同一原图恢复。

art.batches 按 batch_id 索引，记录实际 panels、canvas_pixels、requested_plan（含 canvas_pixels 和原计划 panels）、plan_hash、实际提示词路径/哈希、参考用途和对象、原图路径/哈希/尺寸、crops、extraction_issues 与时间。plan_hash 绑定 {canvas_pixels,panels}；规划目标和实际 raw.width/raw.height 分开记录。每个 art.panels 尝试必须有 batch_id；每格的 number、render_hash、状态、参考和 QA 仍独立。一个批次实际生成一次，每格各增加一次尝试；分组与提示词不参与刷新视觉输入预算。

QA、通过记录及后续复用都要求登记原图、提示词和裁切文件仍与哈希一致，且本格输入和尝试号一致。只有 finish-panel 才登记通过。纯裁切不计新绘图尝试；新生成或图像编辑必须建立新批次。preflight 的队列可写成 {"batches":[批次计划,批次计划]}，各组不能共享画格 ID，接口只读且不预留尝试。

### 画格验收会计与看板统计（accepted_panel）

看板与 `status` 严格共用统一的 `accepted_panel` 判定：
1. 必须存在明确的 accepted 记录；
2. 记录所绑定的 attempt 必须存在且非 pending、非 failed、非 stale；
3. 本格关联的实际裁切图片文件必须存在，且 SHA256 指纹与记录一致；
4. 任何 pending、failed、stale、过期尝试或物理文件缺失，均绝不计入通过。

### 失败分类与重试预算（fail-panel）

`fail-panel` 记录 9 大标准化失败原因类别：
- `identity`：核心人设、五官结构、发型体型漂移；
- `state`：服装、伤势、持有物、位置等连贯性状态错误；
- `action_space`：动作物理逻辑违背常理、空间站位透视崩塌；
- `prop`：关键剧情道具缺失、形制错误或凭空多出道具；
- `art_style`：画风突变、杂乱笔触、质感脏污或配色违背全书规范；
- `panel_border`：构图中心偏离、画框裁切缺失关键内容；
- `native_detail`：原生分辨率或清晰度不足、面部模糊；
- `unintended_text`：画面产生生造文字、乱码、多余气泡或水印伪印；
- `tool_exception`：绘图工具报错、接口网络超时、显存溢出等外部工程故障。

`tool_exception` 属于工具异常，享有独立的网络/调用重试预算，不消耗模型层面的 3 次创作尝试上限。

### 幂等性与人工注记保护

流水线对顶层及卷级工程的初始化与更新具备严格的幂等性：
- 重新运行 `init-book` 或分卷 `init` 不会擦除既有制作说明；
- `docs/notes.md` 为人工注记专属文件，流水线永不覆写；
- 各级 `README.md` 中仅动态更新标记在 `<!-- AUTO_STATUS_START -->` 与 `<!-- AUTO_STATUS_END -->` 之间的自动状态区块，注记区与外部文本在锁定、导出和交付后始终完整保留。

### 完成范围与独立交付

工程严格区分三个层次的完成状态：
1. **本卷完成（Volume Complete）**：本卷全部画格通过、排版通过、完成导出并签署 final_review。第一卷锁定并制作交付时，第二卷即使尚未初始化，第一卷亦能完整交付并准确报告“第一卷交付完成”；
2. **作品目标完成（Work Complete）**：用户指定的所有分卷均达到完成交付状态；
3. **原作完结（Source Finished）**：根据小说实际连载与实体出版状态客观记录。

