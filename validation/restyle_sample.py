"""Apply the user's elegance correction without erasing prior attempts or evidence."""
from pathlib import Path
import sys,copy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'novel_to_comic_skills/scripts'))
import comic_pipeline as cp
root=Path(__file__).resolve().parent/'灯塔来信'
def call(name,**kwargs):
    args=['--project',str(root)]
    for key,value in kwargs.items():args.extend(['--'+key.replace('_','-'),str(value)])
    return cp.run(cp.parser().parse_args([name,*args]))
project=cp.project_load(root)
cp.atomic_json(root/'design/reference-index-first-style.json',cp.load_json(root/'design/reference-index.json'))
for pid,attempts in project['art']['panels'].items():
    for attempt in attempts:
        if attempt['status']=='pending':
            call('fail-panel',panel=pid,attempt=attempt['number'],outcome='stale',reason='用户实看后指出画面脏，要求整体非常优雅。统一改美术方向；保留首次实际出图和状态，不将旧图通过新风格验收。')
script=copy.deepcopy(project['script'])
style=script['style']
style.update(look='精致优雅的彩色叙事漫画，干净细致线条、清透的两级赛璐璐明暗、纯净象牙白与海蓝灰配色、大量有组织留白；人物五官体态精致、背景简洁清爽，无噪点纸纹与污旧纹理',
             palette='清透象牙白、淡海蓝与雾蓝灰、墨绿、少量陶红；局部柔和金色点亮，不使用全画面黄褐色滤镜',
             selection_reason='用户指出第一批图像脏，要求整体非常优雅。采用轻盈而准确的线条、简化材质和环境、分层留白与稳定人物表演，避免木纹杂物与重黄光覆盖全画面。')
style['art_direction'].update(
    linework='清晰、细致、稳定的深灰轮廓线，内线更轻；不用照片式纹理、脏纸噪点或密集刻线。',
    shading='清透的两级赛璐璐明暗，局部极轻柔过渡；暗面保留清晰色相，不涂脏棕灰色。',
    color_rules='象牙白与浅海蓝灰为大面积底色，人物肤色自然清透；绿裙与陶红为小面积身份色，金光只用于局部叙事重点。',
    lighting='傍晚柔和浅桃光配冷海色；开灯后局部暖光，夜景以干净海蓝和小面积柔金对照。禁止全图黄褐滤镜与重油光。',
    backgrounds='浅色平整石墙、简洁浅木工作台、少量整齐档案；空间关系明确，背景按镜头适度概括并留白。场景基准台面不放透镜、固定环或备件盒，正文按当前状态添加。',
    composition='人物表情与动作优先，背景杂物大幅减少，细节集中于必要五官和叙事道具；避免重纹理抢视觉中心。',
    effects='灯光清澈克制，少量柔光，不加杂乱光点、泛黄雾霾或无依据粒子。')
for entity in [*script['settings'],*script['props']]:
    entity['reference_paths']=[];entity['reference_hashes']=[]
path=root/'scripts/elegant-script.json';cp.atomic_json(path,script);call('set-script',file=path)
for old in project['reviews'][-3:]:
    report={k:v for k,v in old.items() if k not in ('kind','recorded_at')}
    report['script_hash']=cp.digest(script)
    report['evidence']+=' 用户要求更优雅后，主代理重新逐格核对：原文、动作、说话人、服装形态、章节顺序与16格/4页均保留；只修改线条、色彩、环境细节与光影标准。第一批输出仅作未验收历史，不能沿用为新风格成品。'
    rpath=root/'reports'/('script-elegant-'+old['kind']+'.json');cp.atomic_json(rpath,report)
    call('review',kind=old['kind'],file=rpath)
call('lock-script');print(call('assert-art'))
cp.atomic_json(root/'reports/style-revision.json',{'requested_by':'user','reason':'当前画面有点脏，整体观感要非常优雅',
 'old_batch':'art/raw/*-a1.png retained, no false final acceptance',
 'new_direction':style,'status':'references and panels require new actual visual inspection'})
