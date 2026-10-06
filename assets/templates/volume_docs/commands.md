# 《{{title}}》· {{volume}} 常用操作命令速查

请在终端中指定当前卷路径为 `--project` 执行流水线命令：

```powershell
# 1. 查看当前卷制作进度与体检
& $python -X utf8 $cli status --project '{{root}}'
& $python -X utf8 $cli doctor --project '{{root}}'

# 2. 标记阅读正文与确认来源
& $python -X utf8 $cli mark-read --project '{{root}}' --chapter ch000001 --note '已读完本章核心剧情'
& $python -X utf8 $cli confirm-source --project '{{root}}' --note '已确认全文无缺漏漏读'

# 3. 导入工作剧本与结构检查
& $python -X utf8 $cli set-script --project '{{root}}' --file 'scripts/work-script.json'
& $python -X utf8 $cli check-script --project '{{root}}'
& $python -X utf8 $cli preflight-typeset --project '{{root}}'

# 4. 提交三轮审查并锁定剧本
& $python -X utf8 $cli review --project '{{root}}' --kind coverage --file 'reports/coverage.json'
& $python -X utf8 $cli review --project '{{root}}' --kind continuity --file 'reports/continuity.json'
& $python -X utf8 $cli review --project '{{root}}' --kind comic --file 'reports/comic.json'
& $python -X utf8 $cli lock-script --project '{{root}}'

# 5. 登记角色基准参考图（出图前必须先过关卡）
& $python -X utf8 $cli assert-art --project '{{root}}'
& $python -X utf8 $cli register-reference --project '{{root}}' --file 'art/raw/ref.png' --qa 'reports/ref.json' --characters char-01

# 6. 打磨完整页提示词，一页一次生成与验收
& $python -X utf8 $cli build-prompt --project '{{root}}' --page page001 --notes 'prompts/page001-notes.md' --output 'prompts/page001.txt'
# 主代理通读并打磨提示词后登记；按返回提示词和参考路径调用一次绘图工具
& $python -X utf8 $cli begin-page --project '{{root}}' --page page001 --prompt 'prompts/page001.txt'
& $python -X utf8 $cli qa-inputs --project '{{root}}' --page page001 --attempt 1 --file 'art/page001.png'
& $python -X utf8 $cli finish-page --project '{{root}}' --page page001 --attempt 1 --file 'art/page001.png' --qa 'reports/page001.json'

# 7. 复制完整原生页、手机阅读质检与最终导出
& $python -X utf8 $cli prepare-pages --project '{{root}}'
& $python -X utf8 $cli review-layout --project '{{root}}' --file 'reports/layout.json'
& $python -X utf8 $cli export --project '{{root}}'
& $python -X utf8 $cli verify-export --project '{{root}}'
& $python -X utf8 $cli complete --project '{{root}}' --file 'reports/final.json'
```
