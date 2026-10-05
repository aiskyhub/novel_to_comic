"""Page assembly/lettering and offline exports; illustration pixels are not redrawn."""
from __future__ import annotations

import html
import io
import json
import math
import os
import shutil
import uuid
import zipfile
from pathlib import Path

LAYOUT_VERSION = 5
PAPER_COLOR = '#faf9f5'
INK_COLOR = '#2e3035'
MAX_RASTER_DIMENSION = 12_000
MAX_RASTER_PIXELS = 24_000_000


def core():
    # Avoid a second __main__ module when invoked through the CLI.
    import sys
    main = sys.modules.get('__main__')
    if hasattr(main, 'GateError') and hasattr(main, 'accepted_panel'):
        return main
    import comic_pipeline
    return comic_pipeline


def _effective_font(root, project, explicit_font=None):
    """Resolve the font once so fingerprinting and rendering use identical bytes."""
    c = core()
    style = project.get('script', {}).get('style', {})
    style_font_path = style.get('font_path')
    layout = project.get('layout') or {}

    if explicit_font is not None:
        return find_font(explicit_font), True
    if (layout.get('font_override') is True and
            layout.get('style_font_path') == style_font_path and layout.get('font_path')):
        path = find_font(layout['font_path'])
        expected = layout.get('font_sha256')
        if not expected or c.sha_file(path) != expected:
            raise c.GateError('Stored layout font is missing or changed; re-compose with a valid font.')
        return path, True
    if style_font_path is not None:
        return find_font(style_font_path), False
    return find_font(None), False


def layout_fingerprint(root, project, explicit_font=None):
    c = core()
    font_path, _ = _effective_font(root, project, explicit_font)
    font_sha = c.sha_file(font_path)
    images = []
    for panel in project['script']['panels']:
        attempt = c.accepted_panel(root, project, panel)
        if not attempt:
            raise c.GateError('Missing, stale, or unreviewed illustration: ' + panel['id'])
        images.append((panel['id'], attempt['sha256']))
    return c.digest({'layout_version': LAYOUT_VERSION,
                     'title': project['title'], 'source_scope': project['source']['scope_note'],
                     'script': project['script'], 'images': images,
                     'font': {'path': font_path, 'sha256': font_sha}})


def find_font(explicit=None):
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise core().GateError('Selected CJK font does not exist: ' + str(explicit))
        return str(path.resolve())
    candidates = []
    candidates += [os.environ.get('COMIC_FONT'), 'C:/Windows/Fonts/simhei.ttf',
                   '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
                   '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise core().GateError('CJK font missing. Supply --font with a font covering the dialogue language.')


def wrap_text(text, font, width):
    """Measured Unicode wrapping, keeping source characters and explicit newlines."""
    lines = []
    for paragraph in text.split('\n'):
        current = ''
        for character in paragraph:
            if font.getlength(character) > width:
                raise core().GateError('Lettering cell narrower than one glyph; increase page width.')
            if current and font.getlength(current + character) > width:
                lines.append(current)
                current = ''
            current += character
        lines.append(current)
    return lines


def _panel_error(panel_id, field, detail):
    return core().GateError(f'Panel {panel_id} {field}: {detail}')


def _check_raster_dimensions(width, height, context, style_field):
    """Reject unreasonable raster allocations before asking Pillow to create them."""
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0 or
            width > MAX_RASTER_DIMENSION or height > MAX_RASTER_DIMENSION or
            width * height > MAX_RASTER_PIXELS):
        raise core().GateError(
            f'{context}: {style_field} would require an unsafe {width}x{height} raster; '
            f'limits are {MAX_RASTER_DIMENSION}px per dimension and {MAX_RASTER_PIXELS:,} pixels.'
        )


def _dialogue_render_text(panel, index, dialogue, names):
    kind, text = dialogue.get('kind'), dialogue.get('text')
    if not isinstance(text, str):
        raise _panel_error(panel['id'], f'dialogue[{index}].text', 'must be text.')
    prefix = ''
    speaker = dialogue.get('speaker')
    if kind in ('speech', 'thought'):
        if speaker not in names:
            raise _panel_error(panel['id'], f'dialogue[{index}].speaker', 'must name a present character.')
        prefix = names[speaker] + (' · 内心：' if kind == 'thought' else '：')
    return prefix + text, speaker


def _rect(value, panel_id, field):
    if (not isinstance(value, list) or len(value) != 4 or
            any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in value)):
        raise _panel_error(panel_id, field, 'must be [x,y,width,height] with finite normalized numbers.')
    x, y, width, height = map(float, value)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise _panel_error(panel_id, field, 'must be a positive rectangle within the picture canvas.')
    return [x, y, width, height]


def _overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _segment_intersects_rect(start, end, rect):
    # Liang-Barsky clipping against an axis-aligned normalized rectangle.
    dx, dy = end[0] - start[0], end[1] - start[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, start[0] - rect[0]), (dx, rect[0] + rect[2] - start[0]),
                 (-dy, start[1] - rect[1]), (dy, rect[1] + rect[3] - start[1])):
        if p == 0:
            if q < 0:
                return False
            continue
        value = q / p
        if p < 0:
            low = max(low, value)
        else:
            high = min(high, value)
        if low > high:
            return False
    return True


def _bubble_specs(panel, dialogue, names, default_mode):
    panel_id = panel['id']
    mode = panel.get('lettering_mode', default_mode)
    if mode not in ('band', 'bubbles'):
        raise _panel_error(panel_id, 'lettering_mode', "must be 'band' or 'bubbles'.")
    for index, item in enumerate(dialogue):
        _dialogue_render_text(panel, index, item, names)
    if mode == 'band':
        return mode, [{'dialogue_index': i, 'order': i,
                       'speaker': item.get('speaker'), 'text': _dialogue_render_text(panel, i, item, names)[0]}
                      for i, item in enumerate(dialogue)]

    bubbles = panel.get('bubbles')
    if not isinstance(bubbles, list):
        raise _panel_error(panel_id, 'bubbles', 'must be an array for bubbles lettering.')
    if len(bubbles) != len(dialogue):
        raise _panel_error(panel_id, 'bubbles', 'each dialogue entry must have exactly one bubble.')
    protected_values = panel.get('protected_regions', [])
    if not isinstance(protected_values, list):
        raise _panel_error(panel_id, 'protected_regions', 'must be an array of normalized rectangles.')
    protected = [_rect(r, panel_id, f'protected_regions[{i}]') for i, r in enumerate(protected_values)]
    specs, dialogue_indexes, orders = [], [], []
    for bubble_index, bubble in enumerate(bubbles):
        field = f'bubbles[{bubble_index}]'
        if not isinstance(bubble, dict):
            raise _panel_error(panel_id, field, 'must be an object.')
        dialogue_index = bubble.get('dialogue_index')
        if type(dialogue_index) is not int or not 0 <= dialogue_index < len(dialogue):
            raise _panel_error(panel_id, field + '.dialogue_index', 'must reference one dialogue entry.')
        order = bubble.get('order')
        if type(order) is not int or order < 0:
            raise _panel_error(panel_id, field + '.order', 'must be a nonnegative integer.')
        rect = _rect(bubble.get('rect'), panel_id, field + '.rect')
        tail = bubble.get('tail')
        if tail is not None:
            if (not isinstance(tail, list) or len(tail) != 2 or
                    any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0 or n > 1 for n in tail)):
                raise _panel_error(panel_id, field + '.tail', 'must be null or a bounded normalized [x,y] point.')
            tail = [float(tail[0]), float(tail[1])]
        assigned = dialogue[dialogue_index]
        if 'speaker' in bubble and bubble.get('speaker') != assigned.get('speaker'):
            raise _panel_error(panel_id, field + '.speaker', 'must exactly match the referenced dialogue speaker.')
        if 'text' in bubble:
            raise _panel_error(panel_id, field + '.text', 'text comes only from the referenced dialogue entry.')
        for region_index, region in enumerate(protected):
            if _overlap(rect, region):
                raise _panel_error(panel_id, field + '.rect', f'geometrically occludes protected_regions[{region_index}].')
            if tail is not None:
                edge = (min(max(tail[0], rect[0]), rect[0] + rect[2]),
                        min(max(tail[1], rect[1]), rect[1] + rect[3]))
                if _segment_intersects_rect(edge, tail, region):
                    raise _panel_error(panel_id, field + '.tail', f'geometrically crosses protected_regions[{region_index}].')
        text, speaker = _dialogue_render_text(panel, dialogue_index, assigned, names)
        specs.append({'dialogue_index': dialogue_index, 'order': order, 'rect': rect,
                      'tail': tail, 'speaker': speaker, 'text': text})
        dialogue_indexes.append(dialogue_index)
        orders.append(order)
    if sorted(dialogue_indexes) != list(range(len(dialogue))):
        raise _panel_error(panel_id, 'bubbles.dialogue_index', 'every dialogue entry must appear exactly once.')
    if sorted(orders) != list(range(len(bubbles))):
        raise _panel_error(panel_id, 'bubbles.order', 'orders must be unique and contiguous starting at zero.')
    return mode, specs


def _manifest_panel(panel, style, names):
    dialogue = panel.get('dialogue', [])
    if not isinstance(dialogue, list):
        raise _panel_error(panel['id'], 'dialogue', 'must be an array.')
    mode, specs = _bubble_specs(panel, dialogue, names, style.get('lettering_mode', 'band'))
    if mode == 'band':
        entries = [{'dialogue_index': item['dialogue_index'], 'kind': dialogue[item['dialogue_index']].get('kind'),
                    'speaker': item['speaker'], 'text': item['text']} for item in specs]
    else:
        entries = [{'dialogue_index': item['dialogue_index'], 'order': item['order'], 'rect': item['rect'],
                    'tail': item['tail'], 'kind': dialogue[item['dialogue_index']].get('kind'),
                    'speaker': item['speaker'], 'text': item['text']}
                   for item in sorted(specs, key=lambda x: x['order'])]
    return {'panel_id': panel['id'], 'lettering_mode': mode, 'dialogue': entries}


def _panel_target_ratio(panel):
    value = panel.get('aspect_ratio')
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise _panel_error(panel['id'], 'aspect_ratio', 'must be a positive finite width/height ratio.')
    return float(value)


def panel_tile(root, project, panel, width, font_path, font_size, *,
               available_height, style_field, context):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    c = core()
    attempt = c.accepted_panel(root, project, panel)
    path = c.inside(root, attempt['path'])
    font = ImageFont.truetype(font_path, font_size)
    names = {item['id']: item['name'] for item in project['script']['characters']}
    style = project['script']['style']
    mode, specs = _bubble_specs(panel, panel.get('dialogue', []), names, style.get('lettering_mode', 'band'))
    manifest_panel = _manifest_panel(panel, style, names)
    line_height = math.ceil(font_size * 1.4)
    padding = max(14, font_size // 2)
    stroke = max(1, round(width / 768))
    blocks, band_height = [], 0
    if mode == 'band':
        for index, dialogue in enumerate(panel.get('dialogue', [])):
            text = _dialogue_render_text(panel, index, dialogue, names)[0]
            try:
                lines = wrap_text(text, font, width - padding * 4)
            except c.GateError as error:
                raise _panel_error(panel['id'], f'dialogue[{index}]', str(error)) from error
            block_height = line_height * len(lines) + padding * 2
            blocks.append((dialogue, text, lines, block_height))
        band_height = sum(height + padding for _, _, _, height in blocks) + (padding if blocks else 0)
    with Image.open(path) as source_image:
        _check_raster_dimensions(source_image.width, source_image.height, context, style_field)
        ratio = _panel_target_ratio(panel)
        if ratio is None:
            picture_height = max(1, round(source_image.height * width / source_image.width))
        else:
            picture_height = max(1, round(width / ratio))
        tile_height = picture_height + band_height
        _check_raster_dimensions(width, picture_height, context, style_field)
        _check_raster_dimensions(width, tile_height, context, style_field)
        if tile_height > available_height:
            raise c.GateError(
                f'{context}: {style_field} allows at most {available_height}px for this panel row, '
                f'but its composed tile needs {tile_height}px.'
            )
        source_image = source_image.convert('RGB')
        picture = Image.new('RGB', (width, picture_height), PAPER_COLOR)
        contained = ImageOps.contain(source_image, (width, picture_height), method=Image.Resampling.LANCZOS)
        picture.paste(contained, ((width - contained.width) // 2, (picture_height - contained.height) // 2))
    if mode == 'band':
        tile = Image.new('RGB', (width, picture_height + band_height), PAPER_COLOR)
        tile.paste(picture, (0, 0))
        draw = ImageDraw.Draw(tile)
        y = picture_height + padding
        for index, (dialogue, _, lines, height) in enumerate(blocks):
            bounds = (padding, y, width - padding - 1, y + height)
            if dialogue['kind'] == 'speech':
                anchor = dialogue.get('anchor', [0.5, 0.8])
                x = max(padding * 2, min(width - padding * 2, int(width * float(anchor[0]))))
                draw.polygon([(x - padding // 2, y), (x, max(5, picture_height - padding)),
                              (x + padding // 2, y)], fill='white', outline=INK_COLOR)
                draw.rounded_rectangle(bounds, radius=padding, fill='white', outline=INK_COLOR, width=stroke)
            elif dialogue['kind'] == 'thought':
                draw.rounded_rectangle(bounds, radius=padding * 2, fill='#f5f4f0', outline='#747069', width=stroke)
            else:
                draw.rectangle(bounds, fill='#eeece6', outline='#89867e', width=stroke)
            for number, line in enumerate(lines):
                draw.text((padding * 2, y + padding + number * line_height), line, font=font,
                          fill='#25262a', anchor='lt')
            y += height + padding
    else:
        _check_raster_dimensions(width, picture_height, context, style_field)
        tile = picture.copy()
        draw = ImageDraw.Draw(tile)
        for item in sorted(specs, key=lambda x: x['order']):
            dialogue_index = item['dialogue_index']
            rect = item['rect']
            left, top = round(rect[0] * width), round(rect[1] * picture_height)
            right = round((rect[0] + rect[2]) * width) - 1
            bottom = round((rect[1] + rect[3]) * picture_height) - 1
            bubble_width, bubble_height = right - left + 1, bottom - top + 1
            bubble_padding = max(6, font_size // 3)
            text_width = bubble_width - 2 * bubble_padding
            try:
                lines = wrap_text(item['text'], font, text_width)
            except c.GateError as error:
                raise _panel_error(panel['id'], f'bubbles[{item["order"]}].rect', str(error)) from error
            needed_height = line_height * len(lines) + 2 * bubble_padding
            if needed_height > bubble_height:
                raise _panel_error(panel['id'], f'bubbles[{item["order"]}].rect',
                                   f'text requires {needed_height}px high, rectangle provides {bubble_height}px.')
            dialogue = panel['dialogue'][dialogue_index]
            if item['tail'] is not None:
                tail_x, tail_y = round(item['tail'][0] * width), round(item['tail'][1] * picture_height)
                center_x, center_y = (left + right) // 2, (top + bottom) // 2
                dx, dy = tail_x - center_x, tail_y - center_y
                if abs(dx) / max(1, bubble_width) > abs(dy) / max(1, bubble_height):
                    edge_x = left if dx < 0 else right
                    base_y = max(top + bubble_padding, min(bottom - bubble_padding, center_y))
                    half = max(3, font_size // 5)
                    draw.polygon([(edge_x, base_y - half), (tail_x, tail_y), (edge_x, base_y + half)],
                                 fill='white', outline=INK_COLOR)
                else:
                    edge_y = top if dy < 0 else bottom
                    base_x = max(left + bubble_padding, min(right - bubble_padding, center_x))
                    half = max(3, font_size // 5)
                    draw.polygon([(base_x - half, edge_y), (tail_x, tail_y), (base_x + half, edge_y)],
                                 fill='white', outline=INK_COLOR)
            if dialogue['kind'] == 'thought':
                fill, outline, radius = '#f5f4f0', '#747069', bubble_height // 2
            elif dialogue['kind'] == 'speech':
                fill, outline, radius = 'white', INK_COLOR, max(8, bubble_height // 3)
            else:
                fill, outline, radius = '#eeece6', '#89867e', 0
            if radius:
                draw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=fill, outline=outline, width=stroke)
            else:
                draw.rectangle((left, top, right, bottom), fill=fill, outline=outline, width=stroke)
            first_y = top + max(bubble_padding, (bubble_height - line_height * len(lines)) // 2)
            for number, line in enumerate(lines):
                draw.text((left + bubble_padding, first_y + number * line_height), line, font=font,
                          fill='#25262a', anchor='lt')
    draw = ImageDraw.Draw(tile)
    draw.rectangle((0, 0, width - 1, picture_height - 1), outline=INK_COLOR, width=stroke)
    return tile, manifest_panel


def _row_weights(c, page, rows):
    page_id = page.get('id', '<unknown>')
    provided = page.get('row_weights')
    helper = getattr(c, 'page_row_weights', None)
    if callable(helper):
        values = [helper(page, row_index) for row_index in range(len(rows))]
    elif provided is not None:
        if not isinstance(provided, list) or len(provided) != len(rows):
            raise c.GateError(f'Page {page_id} row_weights must parallel its rows.')
        values = provided
    else:
        values = [[1] * len(ids) for ids in rows]
    if not isinstance(values, list) or len(values) != len(rows):
        raise c.GateError(f'Page {page_id} row_weights must parallel its rows.')
    result = []
    for row_index, (ids, weights) in enumerate(zip(rows, values), 1):
        if (not isinstance(weights, list) or len(weights) != len(ids) or
                any(isinstance(weight, bool) or not isinstance(weight, (int, float)) or
                    not math.isfinite(weight) or weight <= 0 for weight in weights)):
            raise c.GateError(f'Page {page_id} row {row_index} weights must be positive finite numbers parallel to its panel IDs.')
        result.append([float(weight) for weight in weights])
    return result


def _allocated_widths(total, weights):
    maximum = max(weights)
    scaled = [weight / maximum for weight in weights]
    exact = [total * weight / sum(scaled) for weight in scaled]
    widths = [math.floor(value) for value in exact]
    remainder = total - sum(widths)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - widths[i]), i))
    for index in order[:remainder]:
        widths[index] += 1
    return widths


def _atomic_bytes(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _manifest_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def compose(root, project, explicit_font=None):
    from PIL import Image, ImageDraw, ImageFont
    c = core()
    font_path, font_override = _effective_font(root, project, explicit_font)
    font_sha = c.sha_file(font_path)
    fingerprint = layout_fingerprint(root, project, font_path)
    style = project['script']['style']
    width = style.get('width', 1536)
    size = style.get('font_size', 46)
    if type(width) is not int or type(size) is not int:
        raise c.GateError('Page width/font size must be integers.')
    if width < 600 or width > 6000 or size < 16 or size > width // 8:
        raise c.GateError('style.width/style.font_size invalid for readable layout.')
    if style.get('format') not in ('pages', 'strip'):
        raise c.GateError('Style format must be pages or strip.')
    fixed_height = style.get('height', 2176)
    max_segment_height = style.get('max_segment_height', 6000)
    if (type(fixed_height) is not int or type(max_segment_height) is not int or
            fixed_height <= 0 or max_segment_height <= 0):
        raise c.GateError('style.height and style.max_segment_height must be positive integers.')
    height_field = 'style.height' if style['format'] == 'pages' else 'style.max_segment_height'
    configured_height = fixed_height if style['format'] == 'pages' else max_segment_height
    # Validate the final page/segment before any panel-tile or canvas Image.new call.
    if style['format'] == 'pages':
        _check_raster_dimensions(width, configured_height, 'Layout canvas', height_field)
    else:
        # A strip's configured ceiling is not itself allocated; check its height bound
        # here, then check each measured row/segment against the full pixel budget.
        _check_raster_dimensions(1, configured_height, 'Strip segment limit', height_field)
    names = {item['id']: item['name'] for item in project['script']['characters']}
    # Check only rasterized chapter headings and text actually emitted in dialogue cells.
    try:
        from reportlab.pdfbase.ttfonts import TTFont
        font = TTFont('comic-font-coverage', font_path, subfontIndex=0)
        cmap = font.face.charToGlyph
        chapter_titles = {chapter['id']: chapter['title'] for chapter in project['source']['chapters']}
        texts = [chapter_titles[page['chapter_id']] for page in project['script']['pages']]
        for panel in project['script']['panels']:
            mode, specs = _bubble_specs(panel, panel.get('dialogue', []), names, style.get('lettering_mode', 'band'))
            texts.extend(item['text'] for item in specs)
        missing = {ch for text in texts for ch in text if not ch.isspace() and ord(ch) not in cmap}
        if missing:
            raise c.GateError('Chosen font lacks emitted glyphs: ' + ''.join(sorted(missing))[:80])
    except ImportError as error:
        raise c.GateError('Font coverage verification requires reportlab in the layout runtime.') from error
    panels = {p['id']: p for p in project['script']['panels']}
    chapters = {chapter['id']: chapter for chapter in project['source']['chapters']}
    margin, gutter = max(28, width // 28), max(18, width // 55)
    title_size = max(16, round(size * 0.85))
    title_font = ImageFont.truetype(font_path, title_size)
    old_layout = project.get('layout') or {}
    cache_repairs = list(old_layout.get('cache_repairs', []))
    rendered, order = [], 0
    for page in project['script']['pages']:
        page_id = page.get('id', '<unknown>')
        source_rows = c.page_rows(page)
        weights_by_row = _row_weights(c, page, source_rows)
        available = width - 2 * margin
        header = margin + title_size * 2 + gutter
        base_height = header + margin
        content_limit = fixed_height if style['format'] == 'pages' else max_segment_height
        rows = []
        for row_index, (ids, weights) in enumerate(zip(source_rows, weights_by_row), 1):
            if len(ids) == 2:
                available -= gutter
            cell_widths = _allocated_widths(available, weights)
            if len(ids) == 2:
                available += gutter
            tiles, manifest_panels = [], []
            for panel_id, cell_width in zip(ids, cell_widths):
                ratio = _panel_target_ratio(panels[panel_id])
                if ratio is not None:
                    target_height = cell_width / ratio
                    if not math.isfinite(target_height) or target_height + base_height > content_limit:
                        raise c.GateError(f'Page {page_id} row {row_index} panel {panel_id}: {height_field} is too small for its aspect ratio; re-plan the row.')
                try:
                    tile, manifest_panel = panel_tile(
                        root, project, panels[panel_id], cell_width, font_path, size,
                        available_height=content_limit - base_height,
                        style_field=height_field,
                        context=f'Page {page_id} row {row_index} panel {panel_id}')
                except c.GateError as error:
                    if str(error).startswith('Panel '):
                        raise
                    raise c.GateError(f'Page {page_id} row {row_index}: {error}') from error
                tiles.append(tile)
                manifest_panels.append(manifest_panel)
            rows.append({'ids': ids, 'tiles': tiles, 'widths': cell_widths,
                         'height': max(tile.height for tile in tiles), 'manifest_panels': manifest_panels})
        if style['format'] == 'pages':
            segments = [rows]
            segment_height = base_height + sum(row['height'] for row in rows) + gutter * max(0, len(rows) - 1)
            if segment_height > fixed_height:
                overflow_row = 1
                accumulated = base_height
                for row_index, row in enumerate(rows, 1):
                    accumulated += row['height'] + (gutter if row_index > 1 else 0)
                    if accumulated > fixed_height:
                        overflow_row = row_index
                        break
                raise c.GateError(f'Page {page_id} row {overflow_row} exceeds fixed height {fixed_height}px; re-plan rows or panel ratios.')
        else:
            segments, current, current_height = [], [], base_height
            for row_index, row in enumerate(rows, 1):
                if base_height + row['height'] > max_segment_height:
                    raise c.GateError(f'Page {page_id} row {row_index}: style.max_segment_height {max_segment_height}px is too small; re-plan panel ratio or lettering.')
                next_height = current_height + row['height'] + (gutter if current else 0)
                if current and next_height > max_segment_height:
                    segments.append(current)
                    current, current_height = [], base_height
                    next_height = current_height + row['height']
                current_height = next_height
                current.append(row)
            if current:
                segments.append(current)
        for segment_number, segment in enumerate(segments, 1):
            order += 1
            content_height = base_height + sum(row['height'] for row in segment) + gutter * max(0, len(segment) - 1)
            height = fixed_height if style['format'] == 'pages' else content_height
            if style['format'] == 'strip' and height > max_segment_height:
                raise c.GateError(f'Page {page_id} segment {segment_number}: style.max_segment_height {max_segment_height}px exceeded.')
            _check_raster_dimensions(width, height, f'Page {page_id} segment {segment_number}', height_field)
            canvas = Image.new('RGB', (width, height), PAPER_COLOR)
            draw = ImageDraw.Draw(canvas)
            chapter = chapters.get(page['chapter_id'])
            if chapter is None:
                raise c.GateError(f'Page {page_id} has unknown chapter {page.get("chapter_id")}.')
            title_lines = wrap_text(chapter['title'], title_font, width - 2 * margin)
            if len(title_lines) > 2:
                raise c.GateError(f'Page {page_id} chapter title exceeds its header; use a wider page or smaller header font.')
            for i, line in enumerate(title_lines):
                draw.text((margin, margin + i * title_size), line, font=title_font, fill='#5b5c60', anchor='lt')
            y, included, manifest_panels = header, [], []
            for row in segment:
                indexes = list(range(len(row['tiles'])))
                if style['reading_direction'] == 'rtl':
                    indexes.reverse()
                x = margin
                for index in indexes:
                    tile = row['tiles'][index]
                    canvas.paste(tile, (x, y))
                    x += tile.width + gutter
                if x - gutter != width - margin:
                    raise c.GateError(f'Page {page_id} row width allocation does not fill its available width exactly.')
                included.extend(row['ids'])
                manifest_panels.extend(row['manifest_panels'])
                y += row['height'] + gutter
            identifier = page_id + (f'-s{segment_number:03d}' if len(segments) > 1 else '')
            suffix = c.digest({'input': fingerprint, 'font': font_sha, 'id': identifier})[:12]
            relative = f'pages/{order:06d}-{identifier}-{suffix}.png'
            path = c.inside(root, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            output = io.BytesIO()
            canvas.save(output, format='PNG', optimize=False, compress_level=9)
            image_bytes = output.getvalue()
            fresh_sha = hashlib_bytes(image_bytes)
            old_sha = c.sha_file(path) if path.is_file() else None
            if old_sha is not None and old_sha != fresh_sha:
                repair_dir = c.inside(root, 'tmp/cache-repairs')
                repair_dir.mkdir(parents=True, exist_ok=True)
                archived = repair_dir / (f'{uuid.uuid4().hex}-{path.name}')
                shutil.copy2(path, archived)
                archived_sha = c.sha_file(archived)
                if archived_sha != old_sha or c.sha_file(path) != old_sha:
                    raise c.GateError(f'Page {page_id} cache changed while its repair copy was archived.')
                cache_repairs.append({'page_id': identifier,
                                      'archived_path': archived.relative_to(root).as_posix(),
                                      'archived_sha256': archived_sha,
                                      'fresh_sha256': fresh_sha})
            if old_sha != fresh_sha:
                _atomic_bytes(path, image_bytes)
            lettering = {'manifest_version': 1, 'composition_id': identifier, 'panel_ids': included,
                         'panels': manifest_panels}
            manifest_relative = relative[:-4] + '.lettering.json'
            manifest_path = c.inside(root, manifest_relative)
            manifest_data = _manifest_bytes(lettering)
            _atomic_bytes(manifest_path, manifest_data)
            rendered.append({'id': identifier, 'order': order, 'chapter_id': page['chapter_id'],
                             'panel_ids': included, 'path': relative, 'sha256': fresh_sha,
                             'lettering_manifest_path': manifest_relative,
                             'lettering_manifest_sha256': hashlib_bytes(manifest_data),
                             'width': width, 'height': height})
    return {'input_hash': fingerprint, 'font_path': font_path, 'font_sha256': font_sha,
            'font_override': font_override, 'style_font_path': style.get('font_path'),
            'pages': rendered, 'cache_repairs': cache_repairs, 'qa': None}


def export(root, project):
    c = core()
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    root = Path(root)
    layout = project['layout']
    fingerprint = layout['input_hash']
    destination = root / 'exports' / fingerprint[:12]
    destination.mkdir(parents=True, exist_ok=True)
    title = project['title']
    if project.get('volume'):
        title = f"{title} · {project['volume']}"
    pages = layout['pages']
    # file:// compatible: manifest is embedded; no fetch, server, CDN or third-party script.
    cards = ''.join('<figure data-chapter="' + html.escape(page['chapter_id'], quote=True) + '">'
                    '<img loading="lazy" src="../../' + html.escape(page['path'], quote=True) +
                    '" alt="第' + str(page['order']) + '页"><figcaption>' + str(page['order']) +
                    ' / ' + str(len(pages)) + '</figcaption></figure>' for page in pages)
    used = {p['chapter_id'] for p in pages}
    options = '<option value="">全部章节</option>' + ''.join(
        '<option value="' + html.escape(ch['id'], quote=True) + '">' + html.escape(ch['title']) + '</option>'
        for ch in project['source']['chapters'] if ch['id'] in used)
    reader = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    reader += '<title>' + html.escape(title) + '</title><style>body{margin:0;background:#202329;color:#eee;font:16px system-ui}'
    reader += 'header{position:sticky;top:0;background:#202329;padding:12px;z-index:1}h1{font-size:18px;margin:0 0 8px}'
    reader += 'main{max-width:1000px;margin:auto}figure{margin:16px 0}img{display:block;width:100%;height:auto}'
    reader += 'figcaption{text-align:center;padding:8px}select,button{font:inherit;margin-right:8px}a{color:#a9d6ff}</style>'
    reader += '<header><h1>' + html.escape(title) + '</h1><select id="chapter">' + options + '</select>'
    reader += '<button id="zoom">放大 / 适合屏幕</button><a href="comic.pdf">PDF</a> · <a href="comic.cbz">CBZ</a></header>'
    reader += '<main>' + cards + '</main><script>document.getElementById("chapter").onchange=function(){'
    reader += 'document.querySelectorAll("figure").forEach(f=>f.hidden=!!this.value&&f.dataset.chapter!==this.value);scrollTo(0,0)};'
    reader += 'document.getElementById("zoom").onclick=()=>{const m=document.querySelector("main");m.style.maxWidth=m.style.maxWidth?"":"none"};'
    reader += '</script></html>'
    reader_path = destination / 'reader.html'
    reader_path.write_text(reader, encoding='utf-8')
    pdf_path = destination / 'comic.pdf'
    pdf = canvas.Canvas(str(pdf_path))
    pdf.setTitle(title)
    for page in pages:
        w, h = page['width'] / 2, page['height'] / 2
        pdf.setPageSize((w, h))
        pdf.drawImage(ImageReader(str(c.inside(root, page['path']))), 0, 0, width=w, height=h)
        pdf.showPage()
    pdf.save()
    cbz_path = destination / 'comic.cbz'
    with zipfile.ZipFile(cbz_path, 'w', compression=zipfile.ZIP_STORED) as archive:
        for page in pages:
            archive.write(c.inside(root, page['path']), f'{page["order"]:06d}.png')
        info = '<ComicInfo><Title>' + html.escape(title) + '</Title><PageCount>' + str(len(pages)) + '</PageCount></ComicInfo>'
        archive.writestr('ComicInfo.xml', info)
    records = []
    for kind, path in [('reader', reader_path), ('pdf', pdf_path), ('cbz', cbz_path)]:
        records.append({'kind': kind, 'path': path.relative_to(root).as_posix(), 'sha256': c.sha_file(path)})
    return {'input_hash': fingerprint, 'page_count': len(pages), 'files': records}


def verify_exports(root, project):
    c = core()
    from pypdf import PdfReader
    from PIL import Image
    exports, layout = project.get('exports'), project.get('layout')
    fingerprint = layout_fingerprint(root, project)
    if not exports or not layout or exports['input_hash'] != fingerprint or layout['input_hash'] != fingerprint:
        raise c.GateError('Exports missing/stale.')
    if not layout.get('qa') or layout['qa'].get('input_hash') != fingerprint:
        raise c.GateError('Current page review missing.')
    c.validate_qa(layout['qa'], c.LAYOUT_CHECKS)
    repair_records = layout.get('cache_repairs')
    if not isinstance(repair_records, list):
        raise c.GateError('Cache repair archive records are missing or invalid.')
    recorded_archives = set()
    for index, repair in enumerate(repair_records):
        if not isinstance(repair, dict):
            raise c.GateError(f'Cache repair archive record {index + 1} is invalid.')
        archive_path = repair.get('archived_path')
        archive_sha = repair.get('archived_sha256')
        fresh_sha = repair.get('fresh_sha256')
        if (not isinstance(archive_path, str) or not archive_path or
                not isinstance(archive_sha, str) or len(archive_sha) != 64 or
                not isinstance(fresh_sha, str) or len(fresh_sha) != 64 or
                archive_sha == fresh_sha or archive_path in recorded_archives):
            raise c.GateError(f'Cache repair archive record {index + 1} is malformed.')
        archived = c.inside(root, archive_path)
        if not archived.is_file() or c.sha_file(archived) != archive_sha:
            raise c.GateError(f'Cache repair archive missing/changed: {archive_path}')
        recorded_archives.add(archive_path)
    repair_dir = c.inside(root, 'tmp/cache-repairs')
    if repair_dir.exists():
        actual_archives = {path.relative_to(root).as_posix() for path in repair_dir.iterdir() if path.is_file()}
        if actual_archives != recorded_archives:
            raise c.GateError('Cache repair archive records do not match the files on disk.')
    font_path = layout.get('font_path')
    if not font_path or not Path(font_path).is_file() or c.sha_file(font_path) != layout.get('font_sha256'):
        raise c.GateError('Stored layout font is missing or changed.')
    names = {item['id']: item['name'] for item in project['script']['characters']}
    panels = {item['id']: item for item in project['script']['panels']}
    ordered = []
    for page in layout['pages']:
        path = c.inside(root, page['path'])
        if not path.is_file() or c.sha_file(path) != page['sha256']:
            raise c.GateError('Final page missing/changed.')
        with Image.open(path) as image:
            if image.size != (page['width'], page['height']):
                raise c.GateError('Final page dimensions changed.')
            image.verify()
        manifest_path = c.inside(root, page.get('lettering_manifest_path', ''))
        if (not manifest_path.is_file() or
                c.sha_file(manifest_path) != page.get('lettering_manifest_sha256')):
            raise c.GateError('Editable lettering manifest missing/changed: ' + page['id'])
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise c.GateError('Editable lettering manifest is invalid: ' + page['id']) from error
        expected_manifest = {'manifest_version': 1, 'composition_id': page['id'],
                             'panel_ids': page['panel_ids'],
                             'panels': [_manifest_panel(panels[pid], project['script']['style'], names)
                                        for pid in page['panel_ids']]}
        if manifest != expected_manifest:
            raise c.GateError('Editable lettering manifest no longer matches canonical panel/dialogue data: ' + page['id'])
        ordered.extend(page['panel_ids'])
    if ordered != [p['id'] for p in project['script']['panels']]:
        raise c.GateError('Final page manifest omits/reorders/duplicates panels.')
    files = {f['kind']: f for f in exports['files']}
    if set(files) != {'reader', 'pdf', 'cbz'}:
        raise c.GateError('Reader/PDF/CBZ all required.')
    for item in files.values():
        path = c.inside(root, item['path'])
        if not path.is_file() or c.sha_file(path) != item['sha256']:
            raise c.GateError('Export missing/changed: ' + item['kind'])
    reader = c.inside(root, files['reader']['path']).read_text(encoding='utf-8')
    if any('../../' + html.escape(p['path'], quote=True) not in reader for p in layout['pages']):
        raise c.GateError('Offline reader misses a page.')
    pdf_reader = PdfReader(c.inside(root, files['pdf']['path']))
    pdf_pages = len(pdf_reader.pages)
    if pdf_pages != len(layout['pages']):
        raise c.GateError('PDF page count mismatch.')
    for index, (pdf_page, page) in enumerate(zip(pdf_reader.pages, layout['pages']), 1):
        expected_size = (page['width'], page['height'])
        media = pdf_page.mediabox
        if (abs(float(media.width) * 2 - expected_size[0]) > 0.02 or
                abs(float(media.height) * 2 - expected_size[1]) > 0.02):
            raise c.GateError(f'PDF page {index} dimensions differ from its reviewed PNG.')
        try:
            extracted = list(pdf_page.images)
            if len(extracted) != 1:
                raise c.GateError(f'PDF page {index} does not contain exactly one composed page image.')
            extracted_image = extracted[0].image.convert('RGB')
            with Image.open(c.inside(root, page['path'])) as source_image:
                expected_pixels = source_image.convert('RGB')
                if (extracted_image.size != expected_pixels.size or
                        extracted_image.tobytes() != expected_pixels.tobytes()):
                    raise c.GateError(f'PDF page {index} image differs from its reviewed PNG.')
        except (AttributeError, KeyError, OSError, ValueError) as error:
            if isinstance(error, c.GateError):
                raise
            raise c.GateError(f'PDF page {index} image could not be verified against its reviewed PNG.') from error
    with zipfile.ZipFile(c.inside(root, files['cbz']['path'])) as archive:
        names = [n for n in archive.namelist() if n.endswith('.png')]
        expected = [f'{p["order"]:06d}.png' for p in layout['pages']]
        if names != expected or archive.testzip() is not None:
            raise c.GateError('CBZ page order/integrity mismatch.')
        for name, page in zip(names, layout['pages']):
            if hashlib_bytes(archive.read(name)) != page['sha256']:
                raise c.GateError('CBZ image differs from the reviewed page.')
    return {'ok': True, 'page_count': pdf_pages, 'formats': ['png', 'reader', 'pdf', 'cbz']}


def hashlib_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()
