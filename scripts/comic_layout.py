"""Native whole-page archive, phone previews and offline export. No page assembly."""
from __future__ import annotations
import html
import io
import math
import os
import shutil
import uuid
import zipfile
from pathlib import Path

LAYOUT_VERSION = 7
MAX_RASTER_DIMENSION = 12_000
MAX_RASTER_PIXELS = 24_000_000
PHONE_WIDTH, PHONE_HEIGHT, PHONE_FONT_SIZE = 1080, 2400, 54


def core():
    import sys
    main = sys.modules.get('__main__')
    if hasattr(main, 'GateError') and hasattr(main, 'script_errors'):
        return main
    import comic_pipeline
    return comic_pipeline


def phone_style_errors(style):
    errors = []
    if style.get('format') != 'pages':
        errors.append('style.format: whole-page production requires pages.')
    width, height = style.get('width', PHONE_WIDTH), style.get('height', PHONE_HEIGHT)
    size = style.get('font_size', PHONE_FONT_SIZE)
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        return errors + ['style.width/style.height must be positive integers.']
    if not 1.6 * width <= height <= 2.6 * width:
        errors.append('style.height/style.width: phone pages require portrait aspect ratio height/width between 1.6 and 2.6 (recommended 2.0–2.4).')
    if type(size) is not int or size * 360 < width * 16:
        errors.append('style.font_size: body text must target at least 16 CSS px at 360 CSS px width.')
    return errors


def check_phone_rows(page, rows):
    if (page.get('columns',1) != 1 or not 1 <= len(page.get('panel_ids',[])) <= 5 or any(len(row)!=1 for row in rows)):
        raise core().GateError('Phone pages require 1–5 narrative panels in full-width single-column rows.')


def _check_raster_dimensions(width,height,context,field):
    if (type(width) is not int or type(height) is not int or min(width,height)<=0 or
        max(width,height)>MAX_RASTER_DIMENSION or width*height>MAX_RASTER_PIXELS):
        raise core().GateError(f'{context}: {field} would require an unsafe {width}x{height} raster.')


def validate_phone_reading_report(project,report):
    panels={p['id']:p for p in project['script']['panels']}
    pages={p['id']:p for p in project['layout']['pages']}
    for note in report['phone_reading_notes']:
        page=pages.get(note['page_id'])
        if page is None:
            raise core().GateError('Unknown whole page in phone reading review.')
        if note['min_body_css_px'] is None and any(d.get('text','').strip() for pid in page['panel_ids'] for d in panels[pid].get('dialogue',[])):
            raise core().GateError('A page with lettering must report minimum body text size.')


def _atomic_bytes(path,data):
    path=Path(path)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary,path)
    finally:
        if temporary.exists():
            temporary.unlink()


def layout_fingerprint(root,project):
    from comic_pages import accepted_page
    c=core()
    images=[]
    for page in project['script']['pages']:
        attempt=accepted_page(root,project,page)
        if attempt is None:
            raise c.GateError('Missing/stale/unreviewed whole page: '+page['id'])
        images.append((page['id'],attempt['sha256']))
    return c.digest({'layout_version':LAYOUT_VERSION,'title':project['title'],'script':project['script'],'images':images})


def prepare_pages(root,project):
    from PIL import Image
    from comic_pages import accepted_page,check_page_image
    c=core()
    fingerprint=layout_fingerprint(root,project)
    pages=[]
    for order,page in enumerate(project['script']['pages'],1):
        attempt=accepted_page(root,project,page)
        source=c.inside(root,attempt['path'])
        width,height=check_page_image(source)
        relative=f'pages/{order:06d}-{page["id"]}-{attempt["sha256"][:12]}.png'
        destination=c.inside(root,relative)
        destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.is_file() or c.sha_file(destination)!=attempt['sha256']:
            shutil.copy2(source,destination)
        if c.sha_file(destination)!=attempt['sha256']:
            raise c.GateError('Whole-page bytes changed during archival.')
        previews=[]
        with Image.open(source) as image:
            for preview_width in (360,390,430):
                preview_height=round(height*preview_width/width)
                preview=image.resize((preview_width,preview_height),Image.Resampling.LANCZOS)
                output=io.BytesIO()
                preview.save(output,format='PNG')
                data=output.getvalue()
                preview_relative=f'pages/phone-previews/{order:06d}-{page["id"]}-{attempt["sha256"][:12]}-{preview_width}.png'
                path=c.inside(root,preview_relative)
                path.parent.mkdir(parents=True,exist_ok=True)
                _atomic_bytes(path,data)
                previews.append({'width':preview_width,'height':preview_height,'path':preview_relative,'sha256':hashlib_bytes(data)})
        pages.append({'id':page['id'],'order':order,'chapter_id':page['chapter_id'],'panel_ids':page['panel_ids'],
                      'path':relative,'sha256':attempt['sha256'],'width':width,'height':height,'phone_previews':previews})
    return {'input_hash':fingerprint,'pages':pages,'qa':None}


def export(root, project):
    c = core()
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    root = Path(root)
    layout = project['layout']
    validate_phone_reading_report(project, layout['qa'])
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


def verify_exports(root,project):
    from PIL import Image
    from pypdf import PdfReader
    from comic_pages import check_page_image
    c=core()
    exports,layout=project.get('exports'),project.get('layout')
    fingerprint=layout_fingerprint(root,project)
    if not exports or not layout or exports['input_hash']!=fingerprint or layout['input_hash']!=fingerprint:
        raise c.GateError('Whole-page exports missing/stale.')
    c.validate_qa(layout.get('qa'),c.LAYOUT_CHECKS)
    if layout['qa'].get('input_hash')!=fingerprint:
        raise c.GateError('Page review hash changed.')
    validate_phone_reading_report(project,layout['qa'])
    if [p['id'] for p in layout['pages']] != [p['id'] for p in project['script']['pages']]:
        raise c.GateError('Whole-page order differs from the locked script.')
    for page in layout['pages']:
        path=c.inside(root,page['path'])
        if not path.is_file() or c.sha_file(path)!=page['sha256']:
            raise c.GateError('Final whole page missing/changed.')
        if check_page_image(path)!=(page['width'],page['height']):
            raise c.GateError('Whole-page dimensions changed.')
        previews=page.get('phone_previews')
        if not isinstance(previews,list) or [p.get('width') for p in previews]!=[360,390,430]:
            raise c.GateError('Phone previews missing or invalid.')
        for preview in previews:
            path=c.inside(root,preview['path'])
            if not path.is_file() or c.sha_file(path)!=preview['sha256']:
                raise c.GateError('Phone preview missing/changed.')
            with Image.open(path) as image:
                if image.size!=(preview['width'],round(page['height']*preview['width']/page['width'])):
                    raise c.GateError('Phone preview dimensions changed.')
                image.verify()
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
