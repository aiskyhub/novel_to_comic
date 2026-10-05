"""Root-authored source/script reviews; never generates visual QA automatically."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel_to_comic_skills/scripts'))
import comic_pipeline as cp

repo = Path(__file__).resolve().parents[1]
root = repo / 'validation/灯塔来信'
fixture = repo / 'novel_to_comic_skills/tests/fixtures'
def call(name, **kwargs):
    args = ['--project', str(root)]
    for key, value in kwargs.items():
        args.append('--' + key.replace('_', '-'))
        args.extend(value if isinstance(value, list) else [str(value)])
    return cp.run(cp.parser().parse_args([name, *args]))

if (root / 'project.json').exists():
    raise SystemExit('Preserving existing sample; resume it rather than initialize again.')
call('init', source=[str(fixture / 'validation-story.txt')], title='灯塔来信')
call('confirm-source', note='主代理完整读取自创短篇：四章十六段正文，最后一句平静灯光为全文结束，无缺章。')
notes = [
    '读到沈知微抱木匣归还透镜、陆砚检查裂纹、唐宁找到给周婶的未寄旧信、周婶认出姐姐字迹。',
    '读到祖母的误解及歉意、周婶澄清姐姐照看伤者；许川判断缺固定环，老赵提供旧铁盒备件。',
    '读到沈知微和唐宁穿相同围裙，许川与陆砚在同色衬衫下修环与检查水平；三人先装透镜，再开电闸。',
    '读到周婶写完整回信、男女主恢复基础衣装、六人门口收拾并把回信入匣，最后走下石阶的双人对白。',
]
project = cp.project_load(root)
for chapter, note in zip(project['source']['chapters'], notes):
    call('mark-read', chapter=chapter['id'], note=note)
call('set-script', file=fixture / 'validation-script.json')
project = cp.project_load(root)
reviews = {
    'coverage': '逐段对照四章十六段正文与p01–p16：每段独立事件均对应一格。p05旁白保留风暴夜拆透镜、误解姐姐与想道歉；p06保留照看伤者真相；p13逐字保留整封回信；p16保留双方结尾对白。没有删去独立支线或追加事件。原文两姐妹关系由旧信与周婶解释呈现。',
    'continuity': '检查十六格时序与全部人物前后状态：旧信先由唐宁找到、周婶认字后沈知微读取；固定环问题先诊断再取备件；p11明确尚未点灯，p12才开闸。p09女性换围裙、p10男主换工作衬衫、p14男女主恢复基础衣服，各有来源支持的transition；唐宁在p13/p15保持工作围裙。最终木匣收纳回信，夜景结束。',
    'comic': '逐格检查动作时刻、景别与说话人：p01进入、p02观察、p03取信、p04认字、p05侧脸读信、p06释然、p07诊断、p08交备件、p09换装完毕、p10拧环、p11放透镜、p12开灯、p13写回信、p14窗前、p15收拾、p16下阶。四页各两行双格，LTR顺序与每页叙事任务相合；长旁白p05与p13留独立文字带，不删字。多角度、同服装、六人同框及男女主与配角对照均有明确镜头。',
}
for kind, evidence in reviews.items():
    report = {'script_hash': cp.digest(project['script']),
              'reviewed_chapter_ids': [c['id'] for c in project['source']['chapters'] if c['has_body']],
              'checks': {k: True for k in cp.REVIEW_CHECKS[kind]}, 'evidence': evidence,
              'issues': [{'severity': 'minor', 'description': '初稿p13/p15唐宁误写基础服装；已改为工作围裙并复查正文来源。', 'resolved': True}]}
    path = root / 'reviews' / (kind + '.json')
    cp.atomic_json(path, report)
    call('review', kind=kind, file=path)
call('lock-script')
print(call('assert-art'))
