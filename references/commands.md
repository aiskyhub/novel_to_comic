# 辅助脚本与整页生产命令

使用本技能scripts/comic_pipeline.py与可用Python。输入/关卡使用标准库；DOCX、PDF提取按相应依赖；整页PNG校验和预览用Pillow，导出用reportlab、pypdf。不需要字体排版运行时或图像重组库。

```powershell
$comicPython = '实际Python绝对路径'
$comicCli = '本技能scripts/comic_pipeline.py绝对路径'
& $comicPython -X utf8 $comicCli status --project '作品绝对路径/书名/第1卷'
```

全部命令中的中文/含空格路径作为单独参数，JSON使用UTF-8。程序只执行机械步骤，不调用绘图模型、不代写剧情或通过报告。

## 项目目录

书名根目录维护README、docs/、source_texts/归档原稿与split_texts/切分正文；各卷目录独立维护project.json（Schema v6）、source/originals、scripts/chapters、docs/adaptation、design、art/references、art/pages、art/failed-pages、prompts、reports、pages/phone-previews和exports。正文资产只有完整页；分镜ID仍在剧本中追踪原著事件，不对应独立生成文件。

init-book默认复制原稿并核验SHA256；只有用户明确--action move时移动。init仅用于新建或空卷目录，自动归档原稿副本；续做使用status，不重新初始化。原文重提取见 [recovery.md](recovery.md)。

## 命令契约

| 命令 | 主要参数 | 行为 |
|---|---|---|
| init-book | --book-dir [--title] [--sources] [--action copy/move] | 书名项目与原稿归档 |
| split-source | --book-dir --file [--output-dir] [--pattern] | 按真实卷章标题切分文本 |
| init | --project --source [--title] [--volume] | 初始化独立卷 |
| confirm-source | --note [--scope] | 确认来源完整性与边界 |
| chapter | [--chapter] | 读取真实来源unit ID |
| mark-read | --chapter --note | 记录实际精读 |
| check-adaptation | --chapter [--file] | 校验本章细节契约；不代替语义审查 |
| set-script | --file | 导入完整稿，失效旧锁与导出 |
| script-chapter | --chapter | 读取本章实际分镜与交接 |
| set-script-chapter | --chapter --file | 在已有完整结构稿中替换本章 |
| impact | --file | 比较真实修订影响 |
| check-script | — | 原著引用、结构和手机单页布局检查 |
| review | --kind coverage/continuity/comic --file | 登记当前卷实际审查 |
| check-stage-review | [--file] | 机械校验阶段审查报告：核验应审批次全覆盖、章节指纹绑定与整改闭环 |
| lock-script | — | 三轮全卷审查与各阶段审查全覆盖通过后锁定 |
| assert-art | — | 出图前检查有效锁 |
| register-reference | --file --qa (--characters 或 --bindings) | 登记已看图的基准 |
| build-prompt | --page [--notes] [--output] | 编译一张完整页的全部内容，由分镜提示词工程师子代理打磨 |
| preview-page | --page [--file] | 验收前为待审验整页生成 360/390/430px 手机预览（不改变原图） |
| begin-page | --page --prompt | 登记一次整页调用；返回实际提示词、参考路径与尝试号 |
| qa-inputs | --page --attempt --file | 读取整页哈希/尝试绑定；不自动生成通过报告 |
| finish-page | --page --attempt --file --qa | 整页验收，归档原始PNG字节 |
| fail-page | --page --attempt --reason [--file] [--outcome failed/cancelled/stale] | 保存本次整页失败与原因 |
| prepare-pages | — | 按顺序复制完整页并生成手机缩小预览；不绘制内容 |
| review-layout | --file | 本卷全部原生完整页阅读审查 |
| export | — | 导出HTML、PDF、CBZ |
| verify-export | — | 核对完整页、预览、导出字节与顺序 |
| complete | --file | 记录真实最终验收 |
| status / preflight | — | 只读显示页数、pending、剩余整页调用与阻断 |
| doctor | — | 检查依赖与项目状态 |
| preflight-typeset | — | 预检对白气泡容量、字高排字可行性与估算调用成本 |
| estimate-cost | — | 预先计算全卷正文页数、API 生图调用预算与分批交付清单 |
| resolve-issue | --id --evidence | 记录原文提取问题的真实解决证据 |

基准QA还可用qa-inputs --file --characters或--bindings；已登记基准可用--reference检查。正文只使用--page。

正常生产：status→build-prompt→提示词子代理打磨→begin-page→渲染子代理一次绘图得到完整PNG→品控子代理看整页→qa-inputs→填写一份整页报告→主代理finish-page。失败时fail-page记录具体问题，再修订完整提示词。已经验收的页generation_required=false，不重绘；pending必须恢复，不重复调用。

整页报告字段见 [schemas.md](schemas.md)。prepare-pages输出layout.pages[].phone_previews中的360/390/430宽预览，真正阅读后填写phone_reading_notes。所有复制与预览步骤不消耗生图调用。预算按每页当前输入最多三次统计，微调提示词不重置；状态统计不是平台实际计费额度。
