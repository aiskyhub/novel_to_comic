"""Records the root agent's completed, actual reference-image observations."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel_to_comic_skills/scripts'))
import comic_pipeline as cp
repo=Path(__file__).resolve().parents[1];root=repo/'validation/灯塔来信'
def call(name,**kwargs):
    args=['--project',str(root)]
    for key,value in kwargs.items():
        args.append('--'+key.replace('_','-'));args.extend(value if isinstance(value,list) else [str(value)])
    return cp.run(cp.parser().parse_args([name,*args]))
project=cp.project_load(root)
if project['art']['references']:raise SystemExit('References already registered; resume without duplication.')
script=project['script']
relative='art/raw/workshop.png';sha=cp.sha_file(root/relative)
script['settings'][0].update(reference_paths=[relative],reference_hashes=[sha])
script['props'][0].update(reference_paths=[relative],reference_hashes=[sha])
script_path=root/'scripts/production-script.json';cp.atomic_json(script_path,script)
call('set-script',file=script_path)
for old in project['reviews'][-3:]:
    report={k:v for k,v in old.items() if k not in ('recorded_at','kind')}
    report['script_hash']=cp.digest(script)
    report['evidence']+=' 主代理复查新增场景/道具基准：左窗、中台、后架、右门，木匣/玻璃透镜/固定环/铁盒用途明确；仅加入实际文件指纹，十六格动作、文字、状态和页序均未改变。参考中的完整固定环是装配完成的部件示意，p07须表现缺环，p10才修好。'
    path=root/'reports'/('script-production-'+old['kind']+'.json');cp.atomic_json(path,report)
    call('review',kind=old['kind'],file=path)
call('lock-script');print(call('assert-art'))
pair_w={'character_ids':['zhiwei','tangning'],'evidence':'实际比较单人板、两女工作装板与六人无标签对照：知微中面部较长、眼形收窄、下颌缓收；唐宁脸长较短、脸颊饱满、眼形较圆。知微修长站姿，唐宁较矮轻盈且前倾。相同围裙仍可辨；两者女性面孔、肩颈与体态清楚。'}
pair_m={'character_ids':['luyan','xuchuan'],'evidence':'实际比较男主单人板/工作装、双男性板与六人对照：陆砚长窄骨相、眼眉修长、颌线收敛；许川宽方脸、短粗低眉、宽鼻口。陆砚高修长挺直，许川矮壮肩背厚实。同灰衬衫仍区别明确，二者男性造型清楚。'}
assets=[
 ('zhiwei-base',[('zhiwei','base',None)],'turnaround','已看完整图：正面、四分之三、侧面与全身，黑发低束、长椭圆柔颌、细鼻唇与修长女性肩颈稳定，象牙衬衫/绿裙/浅针织披肩完成度精致；专注与释然表情协调。',[pair_w]),
 ('luyan-base',[('luyan','base',None)],'turnaround','已看完整图：清晰男性长脸、较强直眉、利落颌线、修长挺直男性肩背；正面/侧面/全身与表情均一致。蓝灰衬衫和深裤符合设定；与知微女性造型有结构区别，并非换发型共用脸。',[pair_m]),
 ('women-base',[('tangning','base',[0,0,.5,1]),('zhou','base',[.5,0,.5,1])],'combined','已看左右两区：唐宁年轻圆短脸、较圆杏眼、短棕发、陶红裙、前倾轻快；周婶灰黑卷发、笑纹、年龄感与圆润矮体态，灰蓝外套朴素。各有正面、三分之四、侧面、全身与表情；两位女性清楚，周婶未套用年轻脸。',[pair_w,{'character_ids':['tangning','zhou'],'evidence':'两者短脸但唐宁大眼饱满年轻面部与轻盈体态；周婶眼角下落、笑纹/年龄线、矮圆体态与沉稳姿态明确，正侧面均可分辨。'}]),
 ('men-base',[('xuchuan','base',[0,0,.5,1]),('zhao','base',[.5,0,.5,1])],'combined','已看重制的正确图，未用误选重复图：许川宽方颌、厚低眉、短发、结实肩背；老赵宽额后退发际线、宽鼻、皱纹与腹部敦实、稍驼。灰衬衫/棕工装、全部角度与表情对应本人，男性造型明确。',[pair_m,{'character_ids':['xuchuan','zhao'],'evidence':'许川方下颌、低浓眉与结实四肢；老赵高额/退发、皱纹与腹部轮廓、稍驼重心明显，不靠衣色判断年龄或身份。'}]),
 ('women-work',[('zhiwei','work',[0,0,.5,1]),('tangning','work',[.5,0,.5,1])],'combined','已看同款灰围裙板，与基础板逐人对照：知微去掉针织披肩，绿裙保留；唐宁米色衬衫/陶红裙保留。长椭圆与圆短脸、窄长眼与较圆眼、修长与轻盈身形区别清楚。侧脸、专注与笑容均保留身份，女性肩颈与比例未中性化。',[pair_w]),
 ('luyan-work',[('luyan','work',None)],'combined','已看灰工作衬衫/卷袖的全身、正面、侧面和专注近景，与基础板眉眼、鼻颌、男性肩颈比例对应，改变衣色和袖口未换脸。常用笑容与其他表情使用基础板补充，不将工作板当全部表情来源。',[pair_m]),
 ('comparison',[(cid,'base',[i/6,0,1/6,1]) for i,cid in enumerate(['zhiwei','tangning','zhou','luyan','xuchuan','zhao'])],'combined','主代理实际查看六人同画风无标签对照：女性三人的年龄/脸型/体态清楚，男性三人的骨相/肩背/年龄清楚；男女主五官协调、肩颈姿态精致。普通角色保持结构与正常质量，背景衣装较简。对照板补正面并结合各独立角度板验收，没有仅靠发色认人。',[pair_w,pair_m]),
]
refs={}
for stem,subjects,purpose,evidence,comparisons in assets:
    qa={'checks':{key:True for key in cp.REFERENCE_CHECKS},'reviewed_ids':[cid for cid,_,_ in subjects],
        'comparisons':comparisons,'findings':[],'evidence':evidence,
        'reviewer':'root','review_method':'view_image: full image inspected; related original sheets compared'}
    qa_path=root/'reports/references'/(stem+'.json');cp.atomic_json(qa_path,qa)
    bindings={'purpose':purpose,'subjects':[{'character_id':cid,'version_id':version,'region':region} for cid,version,region in subjects]}
    bind_path=root/'design/reference-bindings'/(stem+'.json');cp.atomic_json(bind_path,bindings)
    result=call('register-reference',file=root/'art/raw'/(stem+'.png'),qa=qa_path,bindings=bind_path)
    refs[stem]=result['reference_id']
cp.atomic_json(root/'design/reference-index.json',refs)
print(refs)
