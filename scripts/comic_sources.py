"""Local novel extraction. Source documents are read, never rewritten."""
from __future__ import annotations

import codecs
import hashlib
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET


class SourceError(ValueError):
    pass


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def decode_text(raw):
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return raw.decode('utf-32'), 'utf-32'
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode('utf-16'), 'utf-16'
    if b'\x00' in raw:
        raise SourceError('Text has NUL bytes without a Unicode BOM; encoding must be clarified.')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise SourceError('Unable to decode source without data loss.')


def text_records(text, locator):
    """Preserve every nonempty line, including headings; keep line provenance."""
    return [{'text': line.strip(), 'locator': {**locator, 'line': number}}
            for number, line in enumerate(text.splitlines(), 1) if line.strip()]


class ParagraphHTML(HTMLParser):
    BLOCKS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'li', 'blockquote', 'section', 'article', 'tr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.current, self.hidden = [], [], 0

    def flush(self):
        line = ''.join(self.current).strip()
        if line:
            self.lines.append(line)
        self.current = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if not self.hidden and (tag in self.BLOCKS or tag == 'br'):
            self.flush()

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in self.BLOCKS:
            self.flush()

    def handle_data(self, data):
        if not self.hidden:
            self.current.append(data)


def epub_records(path):
    records = []
    with zipfile.ZipFile(path) as book:
        container = ET.fromstring(book.read('META-INF/container.xml'))
        rootfile = next((node.get('full-path') for node in container.iter()
                         if node.tag.endswith('rootfile')), None)
        if not rootfile:
            raise SourceError('EPUB container has no package document.')
        package = ET.fromstring(book.read(rootfile))
        manifest = {node.get('id'): node for node in package.iter() if node.tag.endswith('}item')}
        spine = [node.get('idref') for node in package.iter() if node.tag.endswith('}itemref')]
        if not spine:
            raise SourceError('EPUB has no readable spine; encrypted/unsupported EPUB cannot be assumed complete.')
        base = PurePosixPath(rootfile).parent
        import posixpath
        from urllib.parse import unquote
        for order, identifier in enumerate(spine, 1):
            item = manifest.get(identifier)
            if item is None or item.get('media-type') not in ('application/xhtml+xml', 'text/html'):
                raise SourceError('EPUB spine contains missing or unsupported content.')
            member = posixpath.normpath(str(base / unquote(item.get('href', '').split('#')[0])))
            parser = ParagraphHTML()
            parser.feed(book.read(member).decode('utf-8-sig'))
            parser.flush()
            if not parser.lines:
                records.append({'text': '', 'locator': {'file': str(path), 'spine': order, 'member': member},
                                'issue': 'EPUB spine item has no extractable text; inspect it before confirming.'})
            records.extend({'text': line, 'locator': {'file': str(path), 'spine': order,
                            'member': member, 'paragraph': i}} for i, line in enumerate(parser.lines, 1))
    return records


def docx_records(path):
    try:
        from docx import Document
    except ImportError as error:
        raise SourceError('DOCX extraction requires python-docx in the selected runtime.') from error
    document = Document(path)
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    paragraphs = document.element.body.findall('.//w:p', ns)
    records = []
    for number, paragraph in enumerate(paragraphs, 1):
        text = ''.join(node.text or '' for node in paragraph.findall('.//w:t', ns)).strip()
        if text:
            records.append({'text': text, 'locator': {'file': str(path), 'paragraph': number}})
    # Footnotes/endnotes can contain narrative. Include them explicitly, not silently discard them.
    with zipfile.ZipFile(path) as archive:
        for member in ('word/footnotes.xml', 'word/endnotes.xml'):
            if member not in archive.namelist():
                continue
            root = ET.fromstring(archive.read(member))
            for number, paragraph in enumerate(root.findall('.//w:p', ns), 1):
                text = ''.join(node.text or '' for node in paragraph.findall('.//w:t', ns)).strip()
                if text:
                    records.append({'text': text, 'locator': {'file': str(path), 'member': member,
                                    'paragraph': number}})
    return records


def pdf_records(path):
    try:
        import pdfplumber
    except ImportError as error:
        raise SourceError('Text PDF extraction requires pdfplumber in the selected runtime.') from error
    records = []
    with pdfplumber.open(path) as document:
        for number, page in enumerate(document.pages, 1):
            text = page.extract_text() or ''
            if not text.strip():
                records.append({'text': '', 'locator': {'file': str(path), 'page': number},
                                'issue': 'PDF page has no extractable text; verify blank/illustrated page or obtain OCR text.'})
            else:
                records.extend(text_records(text, {'file': str(path), 'page': number}))
    return records


HEADING = re.compile(r'^(?:第[0-9零〇一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰\s]+[章节回卷部篇集].*'
                     r'|(?:chapter|book|volume|part)\s+[\wIVXLC]+.*'
                     r'|(?:序言|序章|前言|引子|楔子|后记|尾声|终章|番外|附录)(?:\s.*|[：:·—\-].*)?)$', re.I)


def extract(paths):
    files, records, issues = [], [], []
    for original in paths:
        path = Path(original).resolve()
        if not path.is_file():
            raise SourceError(f'Source file missing: {path}')
        suffix = path.suffix.lower()
        item = {'path': str(path), 'sha256': sha_file(path), 'format': suffix.lstrip('.')}
        if suffix == '.txt':
            text, item['encoding'] = decode_text(path.read_bytes())
            part = text_records(text, {'file': str(path)})
        elif suffix == '.epub':
            part = epub_records(path)
        elif suffix == '.docx':
            part = docx_records(path)
        elif suffix == '.pdf':
            part = pdf_records(path)
        else:
            raise SourceError(f'Unsupported source format: {suffix}')
        files.append(item)
        for record in part:
            if record.get('issue'):
                issues.append({'id': f'issue{len(issues)+1:04d}', 'message': record['issue'],
                               'locator': record['locator'], 'resolved': False, 'evidence': ''})
            elif record['text']:
                records.append(record)
    if not records:
        raise SourceError('No source text extracted; do not create a script from an unreadable novel.')
    chapters, units, current = [], [], None
    previous_file = None
    for record in records:
        text, filename = record['text'], record['locator']['file']
        is_heading = bool(HEADING.match(text))
        if is_heading or current is None or filename != previous_file:
            current = {'id': f'ch{len(chapters)+1:06d}',
                       'title': text if is_heading else Path(filename).stem + ' · 未分章正文',
                       'unit_ids': [], 'has_body': False, 'read': False, 'read_note': ''}
            chapters.append(current)
        unit = {'id': f'u{len(units)+1:07d}', 'chapter_id': current['id'],
                'kind': 'heading' if is_heading else 'body', 'text': text, 'locator': record['locator']}
        units.append(unit)
        current['unit_ids'].append(unit['id'])
        if not is_heading:
            current['has_body'] = True
        previous_file = filename
    for chapter in chapters:
        if not chapter['has_body']:
            issues.append({'id': f'issue{len(issues)+1:04d}', 'message': 'Heading has no body; do not invent content.',
                           'chapter_id': chapter['id'], 'resolved': False, 'evidence': ''})
    return {'files': files, 'chapters': chapters, 'units': units, 'issues': issues,
            'confirmed': False, 'confirmation_note': '', 'scope_note': '以实际提供的原文范围为准'}
