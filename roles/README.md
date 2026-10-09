# 角色体系与独立提示词目录指引 (Role System Directory)

本目录为 `novel-to-comic` 工业化制作团队各岗位角色的**专属系统提示词库（Standalone Role Prompts）**。

## 核心设计铁律：角色物理隔离与单一提示词注入

为避免角色认知混淆与指令污染，**不同的角色不需要也不应该读取其他角色的扮演提示词**：

1. **主代理（总导演）**：扮演总导演与统筹中枢，严格遵循 [`lead_director.md`](lead_director.md)。【零下场干活】。
2. **派发子代理时精准注入**：主代理派发任务时，**仅向该子代理注入其专属的角色提示词文件内容**，禁止将全套角色提示词打包丢入上下文。
3. **数据协议权威来源（Single Source of Truth）**：
   所有角色统一引用 [`references/schemas.md`](../references/schemas.md) 与 `assets/` 下的可执行 Schema 和报告模板作为数据契约。**角色提示词仅描述岗位职责、必读输入、产出成果与验收标准，严禁私自重新定义或修改字段规范**。
4. **子代理零跨岗**：每个子代理在独立沙箱中只履行本角色的专属契约，不越俎代庖。
5. **子代理模型选用建议**：派发子代理时，建议使用与主代理一致或同等水平的模型（若宿主环境支持则优先对齐），确保各专业子代理具备充分的理解与审查水平。

## 岗位角色索引表

| 角色代码 | 提示词文件 | 现实岗位映射 | 核心职责 |
|---|---|---|---|
| **lead_director** | [`lead_director.md`](lead_director.md) | 总导演 / 总制片人 (Showrunner) | 全局统筹调度、工单派发、争议仲裁、运行 CLI 门禁与终审放行（零下场干活） |
| **screenwriter** | [`screenwriter.md`](screenwriter.md) | 改编编剧师 / 分镜台本师 | 逐章精读小说原文、提炼三级证据细节清单、编写 3~4 格整宽纵排逐格分镜草稿（常规3格、简单4格，遵循 canonical panel schema） |
| **script_reviser** | [`script_reviser.md`](script_reviser.md) | 剧本改稿整改师 / 脚本精修师 | **专职承接驳回剧本整改**，执笔画格级物理修改（场景Establishing/高潮动作格/生动对白），输出结构化整改记录与误报裁定 |
| **script_auditor_a** | [`script_auditor_a.md`](script_auditor_a.md) | 红队审评官 A (场景与高潮) | 独立红队啄木鸟，客观审查关键场景空间（Establishing Shot）与关键剧情高潮推进，输出标准阶段审查数据 |
| **script_auditor_b** | [`script_auditor_b.md`](script_auditor_b.md) | 红队审评官 B (幽默与台词) | 独立红队啄木鸟，客观审查原著幽默还原（纯严肃章节不适用）与角色台词生动度，输出标准阶段审查数据 |
| **chief_script_editor** | [`chief_script_editor.md`](chief_script_editor.md) | 全卷文学终审编辑 | 全卷分镜汇总后执行 coverage / continuity / comic 三轮拉网自校验，产出绑定当前剧本指纹的 JSON 审查报告 |
| **art_director** | [`art_director.md`](art_director.md) | 概念美术与人设设计师 | 制定风格指南与角色档案（男女造型清晰、多角度/表情设定），编写基准图提示词与参考报告 |
| **prompt_engineer** | [`prompt_engineer.md`](prompt_engineer.md) | 分镜提示词工程师 | 将锁定分镜编译打磨为 3~4 格整宽纵排（常规3格、简单4格）、字高可读、气泡排版的 1080×2400 原生整页提示词 |
| **render_operator** | [`render_operator.md`](render_operator.md) | 画面渲染执行员 | begin-page 登记，调用生图工具一次性生成包含所有画格与气泡的原生整页 PNG 并归档 |
| **qa_inspector** | [`qa_inspector.md`](qa_inspector.md) | 画面与排版品控员 | 手机 360/390/430px 视口核验整页（支持 preview-page 预生成预览），实测字高与文字错漏，提取 qa-inputs 填写真实整页 QA 报告 |
| **packager** | [`packager.md`](packager.md) | 后制包装与交付员 | 页面归档 (prepare-pages)、排版审阅汇总、多格式导出 (HTML/PDF/CBZ) 与交付完整性校验 |
