from pathlib import Path
import json
repo=Path(__file__).resolve().parents[1]
fixture=repo/'novel_to_comic_skills/tests/fixtures/validation-script.json'
data=json.loads(fixture.read_text(encoding='utf-8-sig'))
style=json.loads((repo/'validation/灯塔来信/scripts/elegant-script.json').read_text(encoding='utf-8-sig'))['style']
for key in ('look','palette','art_direction'):data['style'][key]=style[key]
data['style']['selection_reason']='温暖现实题材以清透配色、细致线条、克制明暗和简洁空间表现修复与和解，人物与关键道具承载主要细节。'
fixture.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Updated isolated structural fixture style; no image generated or QA conclusion added.')
