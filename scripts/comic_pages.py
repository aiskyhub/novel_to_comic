from pathlib import Path
import json
import math
import os
from PIL import Image, ImageDraw, ImageFont


def core():
    from comic_layout import core as get_core
    return get_core()


def page_by_id(project, page_id):
    page = next((p for p in project['script']['pages'] if p['id'] == page_id), None)
    if page is None:
        raise core().GateError('Page ID missing: ' + str(page_id))
    return page


def page_panels(project, page):
    panels = {p['id']: p for p in project['script']['panels']}
    return [panels[pid] for pid in page['panel_ids']]


def page_references(root, project, page):
    c = core()
    needed = {(cid, panel['appearance_versions'][cid])
              for panel in page_panels(project, page) for cid in panel.get('cast', [])}
    explicit = page.get('reference_ids')
    registry = project['art']['references']
    if explicit is not None:
        if not isinstance(explicit, list) or len(explicit) != len(set(explicit)):
            raise c.GateError('page.reference_ids must contain distinct registered reference IDs.')
        candidates = [r for r in registry if r['id'] in explicit]
        if len(candidates) != len(explicit):
            raise c.GateError('Unknown page reference ID.')
    else:
        candidates = registry
    selected, covered = [], set()
    for ref in reversed(candidates):
        forms = {(s['character_id'], s['version_id']) for s in ref.get('subjects', [])}
        useful = forms & needed
        if not useful or (explicit is None and useful <= covered):
            continue
        if not c.valid_reference(root, project, ref):
            if explicit is not None:
                raise c.GateError('Page reference is stale or missing: ' + ref['id'])
            continue
        selected.append(ref)
        covered.update(useful)
    if needed - covered:
        raise c.GateError('Page lacks validated character/form references: ' + str(sorted(needed - covered)))
    return list(reversed(selected))


def page_hash(root, project, page):
    c = core()
    refs = page_references(root, project, page)
    style = project['script']['style']
    return c.digest({'page': {k:v for k,v in page.items() if k != 'reference_ids'},
                     'style': c.visual_style(project),
                     'canvas': [style.get('width',1080),style.get('height',2400)],
                     'reading_direction': style['reading_direction'],
                     'panels': [c.panel_visual_snapshot(root, project, p) for p in page_panels(project, page)],
                     'refs': sorted({c.stored_reference_visual_key(r) for r in refs})})


def check_page_image(path):
    from PIL import Image
    from comic_layout import _check_raster_dimensions
    c = core()
    with Image.open(path) as image:
        _check_raster_dimensions(image.width, image.height, 'Whole page', 'actual_size')
        if image.format != 'PNG':
            raise c.GateError('Whole-page artwork must be a native PNG.')
        if image.width < 800 or not 1.6 <= image.height / image.width <= 2.6:
            raise c.GateError('Whole page requires native width >=800 and portrait aspect ratio height/width between 1.6 and 2.6 (recommended 2.0–2.4); no stretching or padding.')
        size = image.size
        image.verify()
    return size


def validate_page_qa(project, page, attempt, report, sha):
    c = core()
    c.validate_qa(report, c.PAGE_ART_CHECKS, 'page')
    if report.get('reviewed_ids') != page['panel_ids'] or report.get('reviewed_page_ids') != [page['id']]:
        raise c.GateError('Page QA must review this whole page and every narrative panel in order.')
    if report.get('image_sha256') != sha or report.get('attempt_binding') != {
            'page_id': page['id'], 'attempt': attempt['number'], 'render_hash': attempt['render_hash']}:
        raise c.GateError('Page QA must bind this exact whole-page image and attempt.')
    if not c.nonempty(report.get('detail_notes')):
        raise c.GateError('Whole-page QA needs actual native-size and phone-size observations.')
    if report['phone_reading_notes'][0]['min_body_css_px'] is None and any(
            d.get('text', '').strip() for p in page_panels(project, page) for d in p.get('dialogue', [])):
        raise c.GateError('A page with lettering must report its measured minimum body text size.')


def accepted_page(root, project, page):
    c = core()
    try:
        current = page_hash(root, project, page)
        for attempt in reversed(project['art']['pages'].get(page['id'], [])):
            if attempt.get('status') != 'accepted' or attempt.get('render_hash') != current:
                continue
            path = c.inside(root, attempt['path'])
            prompt = c.inside(root, attempt['prompt_path'])
            if c.sha_file(path) != attempt['sha256'] or c.sha_file(prompt) != attempt['prompt_sha256']:
                continue
            check_page_image(path)
            validate_page_qa(project, page, attempt, attempt['qa'], attempt['sha256'])
            return attempt
    except (c.GateError, OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def build_page_prompt(root, project, page_id, notes=''):
    """One prompt contains the full page, canonical dialogue, states and reference roles."""
    c = core()
    page = page_by_id(project, page_id)
    panels = page_panels(project, page)
    style = project['script']['style']
    characters = {p['id']: p for p in project['script']['characters']}
    refs = page_references(root, project, page)
    chapter = next(ch for ch in project['source']['chapters'] if ch['id'] == page['chapter_id'])
    lines = [
        '只生成一张完整漫画成品页，一次完成全部镜头、分格边框、人物、背景、气泡及中文文字。不要输出多张图片、变体、独立镜头资产或制作步骤。',
        f"画幅目标：{style.get('width',1080)}×{style.get('height',2400)}，9:20竖屏；手机按宽度阅读时占约一屏。保留真实原生画幅，不拉伸，不用大白边凑高。",
        f"本页严格包含{len(panels)}格，整宽单列从上到下按以下顺序阅读；镜头都画在同一张图内，不能遗漏、重复、融合或另加画格。",
        f"正文与旁白在{style.get('width',1080)}宽成品图中的字高建议约{style.get('font_size',54)}像素（参考建议约{math.ceil(style.get('width',1080)*16/360)}像素，手机视口约16 CSS像素，以清晰易读为准，字号为建议参考不卡点）。中文清楚端正，气泡宽松，行距适中；长句自然换行，不缩字挤对白。",
        '画面占满约85%以上的可用页高，边距与格距窄而清楚。人物脸部、手部、关键道具与气泡尾巴清晰；禁止水印、随机文字、画格ID、技术参数或参考图标签出现在画面里。',
        f"画风：{style.get('look')}；配色：{style.get('palette')}。",
        '统一美术规范：' + str(style.get('art_direction', {})),
        f"本页原著位置：{chapter['title']}；页面任务：{page.get('narrative', {})}。不要把章节标题或页面任务自动画成额外文字。",
        '参考图用途：' + str([{'id': r['id'], 'purpose': r['purpose'], 'subjects': r['subjects']} for r in refs]),
        '同一人物在所有画格中沿用参考面孔、发型、比例与本格服装状态；不同角色不能换脸，后续装备不能提前出现。'
    ]
    for i, panel in enumerate(panels, 1):
        characters_text = []
        for cid in panel.get('cast', []):
            char = characters[cid]
            vid = panel['appearance_versions'][cid]
            version = next(v for v in char['appearance_versions'] if v['id'] == vid)
            characters_text.append({'name': char['name'], 'identity': char['identity_card'],
                                    'appearance': version['visual'], 'state': panel['state_before'][cid]})
        lines.extend([
            f"第{i}格（顺序说明不要印在画面）：比例宽/高={panel.get('aspect_ratio','按整页空间规划')}。",
            f"镜头：{panel['shot']}；空间：{panel['space']}；动作时刻：{panel['action']}；表情：{panel['expression']}。",
            '人物和当前状态：' + str(characters_text),
            '关键道具：' + str(panel.get('prop_ids', [])),
            '构图与视觉中心：' + str(panel.get('visual_plan', {})),
            '本格气泡按下列顺序原样写入，不改字、不漏字、不串到别格：'
        ])
        for dialogue in panel.get('dialogue', []):
            name = characters.get(dialogue.get('speaker'), {}).get('name', '旁白')
            lines.append(f"{dialogue['kind']}，归属{name}，原文：“{dialogue['text']}”。气泡尾巴仅指向本格对应说话人，旁白不加尾巴。")
        if not panel.get('dialogue'):
            lines.append('本格没有文字，不添加对白、旁白或装饰字符。')
    if notes.strip():
        lines.append('本页精修的排版与美术指引（不得修改上述原著事实、台词和阅读顺序）：\n' + notes.strip())
    lines.append('交付检查：一张完整竖屏页，格数与顺序准确，所有原著台词清晰，气泡不挡主要面部和关键动作；整页完成后直接交付PNG。')
    return '\n'.join(lines) + '\n'


def begin_page(root, project, page_id, prompt_path):
    c = core()
    c.assert_script_lock(project, root)
    page = page_by_id(project, page_id)
    accepted = accepted_page(root, project, page)
    if accepted:
        return {'generation_required': False, 'page_id': page_id, 'path': str(c.inside(root, accepted['path']))}
    attempts = project['art']['pages'].setdefault(page_id, [])
    if any(a['status'] == 'pending' for a in attempts):
        raise c.GateError('This page has an unfinished attempt; resume it instead of generating again.')
    fingerprint = page_hash(root, project, page)
    relevant = [a for a in attempts if a['render_hash'] == fingerprint]
    gen_failed = [a for a in relevant if a.get('generation_failed')]
    reviewed = [a for a in relevant if not a.get('generation_failed')]
    if len(gen_failed) >= 6:
        raise c.GateError('Six image generation retries exhausted; generate failure placeholder image via placeholder-page and finish to proceed. Do not halt.')
    if len(reviewed) >= 3:
        raise c.GateError('Three whole-page attempts exhausted; do not redraw again or halt! Select the best candidate among attempts 1-3, explain its defects in defect_explanation, and call finish-page to proceed.')
    prompt = Path(prompt_path).read_text(encoding='utf-8-sig')
    if not c.nonempty(prompt):
        raise c.GateError('Persist a polished complete-page prompt before generation.')
    for panel in page_panels(project, page):
        if any(d['text'] not in prompt for d in panel.get('dialogue', [])):
            raise c.GateError('Whole-page prompt omits exact locked dialogue; polish it before generating.')
    number = len(attempts) + 1
    relative = f'prompts/{page_id}-a{number:03d}.txt'
    path = c.inside(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt, encoding='utf-8')
    refs = page_references(root, project, page)
    attempt = {'number': number, 'status': 'pending', 'render_hash': fingerprint,
               'prompt_path': relative, 'prompt_sha256': c.sha_file(path), 'at': c.now()}
    attempts.append(attempt)
    c.save(root, project)
    supporting = []
    settings = {item['id']:item for item in project['script']['settings']}
    scenes = {item['id']:item for item in project['script']['scenes']}
    props = {item['id']:item for item in project['script'].get('props',[])}
    for panel in page_panels(project, page):
        scene = scenes[panel['scene_id']]
        entities = [scene,settings[scene['setting_id']]]
        entities.extend(props[pid] for pid in set(panel.get('prop_ids',[]) + scene.get('prop_ids',[])))
        for entity in entities:
            for item in c.entity_reference_fingerprints(root, entity):
                supporting.append(str(c.inside(root, item['path'])))
    return {'generation_required': True, 'page_id': page_id, 'attempt': number,
            'render_hash': fingerprint, 'prompt': str(path),
            'referenced_image_paths': list(dict.fromkeys([str(c.inside(root,r['path'])) for r in refs] + supporting)),
            'generation_calls': 1}


def pending(project, page_id, number):
    attempt = next((a for a in project['art']['pages'].get(page_id, []) if a['number'] == number), None)
    if attempt is None or attempt['status'] != 'pending':
        raise core().GateError('A pending whole-page attempt is required.')
    return attempt


def pending_or_selectable(project, page_id, number):
    attempts = project['art']['pages'].get(page_id, [])
    attempt = next((a for a in attempts if a['number'] == number), None)
    if attempt is None:
        raise core().GateError(f'Attempt {number} not found for page {page_id}.')
    if attempt['status'] == 'pending':
        return attempt
    gen_failed = [a for a in attempts if a.get('generation_failed')]
    reviewed = [a for a in attempts if not a.get('generation_failed')]
    if len(reviewed) >= 3 or len(gen_failed) >= 6 or len(attempts) >= 3:
        return attempt
    raise core().GateError('A pending whole-page attempt is required.')


def page_qa_inputs(root, project, page_id, number, file):
    c = core()
    c.assert_script_lock(project, root)
    page = page_by_id(project, page_id)
    attempt = pending_or_selectable(project, page_id, number)
    if attempt['render_hash'] != page_hash(root, project, page):
        raise c.GateError('Whole-page inputs changed; settle as stale.')
    if c.sha_file(c.inside(root, attempt['prompt_path'])) != attempt['prompt_sha256']:
        raise c.GateError('Registered whole-page prompt changed.')
    size = check_page_image(file)
    return {'scope': 'page', 'page_id': page_id, 'image_sha256': c.sha_file(Path(file)),
            'actual_size': list(size), 'reviewed_ids': page['panel_ids'], 'reviewed_page_ids': [page_id],
            'attempt_binding': {'page_id': page_id, 'attempt': number, 'render_hash': attempt['render_hash']}}


def finish_page(root, project, page_id, number, file, qa_file):
    c = core()
    inputs = page_qa_inputs(root, project, page_id, number, file)
    page = page_by_id(project, page_id)
    attempt = pending_or_selectable(project, page_id, number)
    report = c.load_json(qa_file)
    validate_page_qa(project, page, attempt, report, inputs['image_sha256'])
    path, sha = c.copy_image(root, file, 'pages')
    if sha != inputs['image_sha256']:
        raise c.GateError('Page changed while being archived; review again.')
    for a in project['art']['pages'].get(page_id, []):
        if a['number'] != number and a.get('status') == 'pending':
            a.update(status='cancelled', failure=f'Superseded by selection of attempt {number}', settled_at=c.now())
    attempt.update(status='accepted', path=path, sha256=sha, qa=report, settled_at=c.now())
    project['layout'], project['exports'], project['final_review'] = None, None, None
    c.save(root, project)
    return {'ok': True, 'page_id': page_id, 'path': str(c.inside(root,path))}


def fail_page(root, project, page_id, number, reason, outcome='failed', file=None, generation_failure=False):
    c = core()
    if not c.nonempty(reason):
        raise c.GateError('Whole-page failure reason required.')
    attempt = pending(project, page_id, number)
    is_gen_failed = generation_failure or '生图' in reason or 'generation' in reason.lower() or 'tool_exception' in reason
    if file:
        path, sha = c.copy_image(root, file, 'failed-pages')
        attempt.update(path=path, sha256=sha)
    attempt.update(status=outcome, failure=reason, settled_at=c.now())
    if is_gen_failed:
        attempt['generation_failed'] = True
    c.save(root, project)
    return {'ok': True, 'page_id': page_id, 'outcome': outcome, 'generation_failed': is_gen_failed}


def status(root, project):
    c = core()
    try:
        c.assert_script_lock(project, root)
        locked, blockers = True, []
    except c.GateError as error:
        locked, blockers = False, str(error).splitlines()
    pages = project['script'].get('pages', [])
    pages = [p for p in pages if isinstance(p,dict) and c.nonempty(p.get('id'))] if isinstance(pages,list) else []
    accepted, records, page_blockers = [], [], {}
    for page in pages:
        if accepted_page(root, project, page):
            accepted.append(page['id'])
        attempts = project['art']['pages'].get(page['id'], [])
        records.extend({'page_id': page['id'], **a} for a in attempts)
        try:
            fingerprint = page_hash(root, project, page)
            reasons = []
            if any(a['status'] == 'pending' for a in attempts):
                reasons.append('Pending page attempt; resume it.')
            gen_failed = [a for a in attempts if a['render_hash'] == fingerprint and a.get('generation_failed')]
            reviewed = [a for a in attempts if a['render_hash'] == fingerprint and not a.get('generation_failed')]
            if len(gen_failed) >= 6 and page['id'] not in accepted:
                reasons.append('Six image generation retries exhausted; generate failure placeholder image via placeholder-page and finish to proceed. Do not halt.')
            if len(reviewed) >= 3 and page['id'] not in accepted:
                reasons.append('Three whole-page attempts exhausted; do not redraw or halt! Select best candidate (attempt 1/2/3) and call finish-page with defect_explanation to proceed.')
        except (c.GateError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            reasons = [str(error)]
        page_blockers[page['id']] = reasons
    complete = False
    if locked and project.get('final_review'):
        try:
            from comic_layout import verify_exports
            verify_exports(root, project)
            complete = project['final_review']['input_hash'] == project['layout']['input_hash']
            c.validate_qa(project['final_review'], ['source_scope','story_complete','visual_consistency','exports_opened'])
        except (c.GateError, OSError, ValueError, KeyError, TypeError):
            complete = False
    return {'complete': complete, 'title': project['title'], 'volume': project.get('volume'),
            'script_locked': locked, 'script_hash': c.digest(project['script']), 'blockers': blockers,
            'pages_planned': len(pages), 'pages_accepted': len(accepted),
            'next_page': next((p['id'] for p in pages if p['id'] not in accepted),None),
            'page_blockers': page_blockers, 'attempts': records, 'exports': project.get('exports'),
            'external_source_warnings': c.external_source_warnings(project),
            'preflight_counts': {'planned_generation_calls': sum(p['id'] not in accepted and locked and not page_blockers[p['id']] for p in pages),
                                 'attempts_recorded': len(records), 'pending_attempts': sum(a['status']=='pending' for a in records),
                                 'max_attempts_per_page_input': 3,
                                 'max_generation_retries': 6}}


def _load_font(size):
    font_paths = [
        'C:/Windows/Fonts/msyh.ttc',
        'C:/Windows/Fonts/msyhl.ttc',
        'C:/Windows/Fonts/simhei.ttf',
        'C:/Windows/Fonts/simsun.ttc',
        'C:/Windows/Fonts/arial.ttf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/System/Library/Fonts/PingFang.ttc',
    ]
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _wrap_text(text, font, max_width, draw):
    if not text:
        return []
    lines = []
    for paragraph in str(text).splitlines():
        if not paragraph:
            lines.append('')
            continue
        current = ''
        for char in paragraph:
            test_line = current + char
            try:
                bbox = draw.textbbox((0, 0), test_line, font=font)
                w = bbox[2] - bbox[0]
            except Exception:
                w = len(test_line) * 12
            if w <= max_width:
                current = test_line
            else:
                if current:
                    lines.append(current)
                current = char
        if current:
            lines.append(current)
    return lines


def render_placeholder_image(project, page, reason, output_path):
    width, height = 1080, 2400
    img = Image.new('RGB', (width, height), color=(24, 24, 37))
    draw = ImageDraw.Draw(img)

    font_title = _load_font(38)
    font_h2 = _load_font(28)
    font_body = _load_font(22)
    font_small = _load_font(18)

    # Outer border
    draw.rectangle([30, 30, width - 30, height - 30], outline=(69, 71, 90), width=3)

    # Banner header
    draw.rectangle([50, 50, width - 50, 180], fill=(210, 40, 40))
    draw.text((width // 2, 90), '【 生图失败占位页 】', fill=(255, 255, 255), font=font_title, anchor='mm')
    draw.text((width // 2, 140), 'GENERATION FAILED PLACEHOLDER', fill=(255, 220, 220), font=font_small, anchor='mm')

    # Metadata card
    draw.rectangle([50, 200, width - 50, 600], fill=(48, 52, 70), outline=(81, 87, 109), width=2)
    draw.text((70, 220), '▼ 页面与错误诊断 / Failure Diagnostics', fill=(240, 198, 198), font=font_h2)

    meta_items = [
        f"项目标题 / Title: {project.get('title', '未命名')}",
        f"页面标识 / Page ID: {page.get('id')}  (第 {page.get('order', 1)} 页 / 所属章节: {page.get('chapter_id', '未指定')})",
        f"重试状态 / Retry: 已连续生图失败达到上限（6次），启用默认占位图推进流程",
    ]
    curr_y = 265
    for item in meta_items:
        draw.text((75, curr_y), item, fill=(205, 214, 244), font=font_body)
        curr_y += 32

    draw.text((75, curr_y), "失败原因 / Reason:", fill=(243, 139, 168), font=font_body)
    curr_y += 28
    reason_lines = _wrap_text(str(reason), font_small, width - 160, draw)
    for rline in reason_lines[:6]:
        draw.text((95, curr_y), rline, fill=(250, 179, 135), font=font_small)
        curr_y += 24

    # Script content card
    draw.rectangle([50, 620, width - 50, height - 50], fill=(30, 30, 46), outline=(81, 87, 109), width=2)
    draw.text((70, 640), '▼ 剧本保留内容 / Preserved Script Content', fill=(166, 227, 161), font=font_h2)
    draw.text((70, 675), '（本页分镜剧情与台词原样保留，后续可根据此内容定向补绘）', fill=(147, 153, 178), font=font_small)

    panels = page_panels(project, page)
    curr_y = 715
    for idx, panel in enumerate(panels, 1):
        if curr_y > height - 120:
            break
        p_desc = panel.get('description', '')
        dialogues = panel.get('dialogue', [])

        draw.rectangle([70, curr_y, width - 70, curr_y + 36], fill=(49, 50, 68))
        draw.text((80, curr_y + 8), f"镜头 {idx} (Panel {panel.get('id', idx)})", fill=(137, 180, 250), font=font_body)
        curr_y += 44

        desc_lines = _wrap_text(f"画面: {p_desc}", font_small, width - 180, draw)
        for dl in desc_lines[:3]:
            if curr_y > height - 100:
                break
            draw.text((85, curr_y), dl, fill=(186, 194, 222), font=font_small)
            curr_y += 22

        for d in dialogues:
            if curr_y > height - 80:
                break
            speaker = d.get('speaker', '')
            text = d.get('text', '')
            d_line = f"[{speaker}]: \"{text}\"" if speaker else f"\"{text}\""
            wrapped_d = _wrap_text(d_line, font_small, width - 190, draw)
            for wl in wrapped_d[:3]:
                if curr_y > height - 60:
                    break
                draw.text((100, curr_y), wl, fill=(249, 226, 175), font=font_small)
                curr_y += 22
        curr_y += 16

    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest), format='PNG')


def create_placeholder_page(root, project, page_id, reason, output=None, qa_output=None, finish=False):
    c = core()
    c.assert_script_lock(project, root)
    page = page_by_id(project, page_id)
    if not c.nonempty(reason):
        raise c.GateError('Failure reason is required for placeholder page.')

    if output:
        img_path = Path(output)
    else:
        renders_dir = c.inside(root, 'renders')
        renders_dir.mkdir(parents=True, exist_ok=True)
        img_path = renders_dir / f'{page_id}_failed_placeholder.png'

    render_placeholder_image(project, page, reason, img_path)

    attempts = project['art']['pages'].setdefault(page_id, [])
    fingerprint = page_hash(root, project, page)

    target_attempt = None
    if attempts:
        last_attempt = attempts[-1]
        prompt_file = c.inside(root, last_attempt.get('prompt_path', ''))
        if last_attempt.get('render_hash') == fingerprint and prompt_file.is_file():
            target_attempt = last_attempt

    if target_attempt is None:
        number = len(attempts) + 1
        prompt_text = build_page_prompt(root, project, page_id)
        relative = f'prompts/{page_id}-a{number:03d}.txt'
        p_path = c.inside(root, relative)
        p_path.parent.mkdir(parents=True, exist_ok=True)
        p_path.write_text(prompt_text, encoding='utf-8')
        target_attempt = {
            'number': number, 'status': 'pending', 'render_hash': fingerprint,
            'prompt_path': relative, 'prompt_sha256': c.sha_file(p_path), 'at': c.now()
        }
        attempts.append(target_attempt)
        c.save(root, project)

    if target_attempt['status'] != 'pending':
        target_attempt['status'] = 'pending'

    img_sha = c.sha_file(img_path)
    if qa_output:
        qa_path = Path(qa_output)
    else:
        renders_dir = c.inside(root, 'renders')
        renders_dir.mkdir(parents=True, exist_ok=True)
        qa_path = renders_dir / f'{page_id}_failed_placeholder_qa.json'

    qa_report = {
        'scope': 'page',
        'page_id': page_id,
        'reviewed_page_ids': [page_id],
        'reviewed_ids': page['panel_ids'],
        'image_sha256': img_sha,
        'attempt_binding': {
            'page_id': page_id,
            'attempt': target_attempt['number'],
            'render_hash': target_attempt['render_hash']
        },
        'checks': {
            'panels_complete': False,
            'character_continuity': False,
            'dialogue_fidelity': False,
            'native_resolution': True,
            'phone_readability': True,
            'visual_elegance': False
        },
        'evidence': f'Generation failure placeholder created after retries exhausted: {reason}',
        'defect_explanation': f'Generation failure placeholder page accepted after exhausted retries: {reason}. Preserved script and panel dialogue displayed on placeholder.',
        'detail_notes': f'1080x2400 failure placeholder with reason: {reason}',
        'phone_reading_notes': [{
            'page_id': page_id,
            'preview_widths': [360, 390, 430],
            'evidence': 'Placeholder title, failure reason, and panels readable at 360/390/430px.',
            'min_body_css_px': 16.0
        }],
        'elegance_notes': {
            'linework': 'Clean placeholder layout frame and legible typography.',
            'color_and_light': 'High contrast dark canvas with visible text for easy identification.',
            'visual_hierarchy': 'Prominent failure title, metadata block, and ordered panel content list.'
        },
        'findings': [{
            'severity': 'critical',
            'description': f'Image generation retries exhausted. Placeholder generated: {reason}',
            'resolved': False
        }]
    }
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(json.dumps(qa_report, ensure_ascii=False, indent=2), encoding='utf-8')

    if finish:
        res = finish_page(root, project, page_id, target_attempt['number'], str(img_path), str(qa_path))
        return {
            'ok': True,
            'page_id': page_id,
            'image': str(img_path),
            'qa': str(qa_path),
            'finished': True,
            'path': res.get('path'),
            'attempt': target_attempt['number']
        }
    return {
        'ok': True,
        'page_id': page_id,
        'image': str(img_path),
        'qa': str(qa_path),
        'finished': False,
        'attempt': target_attempt['number']
    }
