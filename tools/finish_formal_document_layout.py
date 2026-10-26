"""Preserve existing field/navigation structure and refresh its displayed pages."""
import argparse
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'


def txt(p):
    return ''.join(t.text or '' for t in p.findall('.//w:t', NS))


def norm(value):
    return re.sub(r'\s+', '', value).replace('\u00ad', '').replace('\u2011', '-')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh-pages-only', action='store_true')
    args = ap.parse_args()
    doc = ROOT / 'D-DN-3.1_EN.docx'
    qa = ROOT / 'results/formal-results-document-v1'
    with ZipFile(doc) as z:
        infos = z.infolist(); parts = {i.filename: z.read(i.filename) for i in infos}
    x = E.fromstring(parts['word/document.xml'])
    body = x.find('w:body', NS)
    if not args.refresh_pages_only:
        template = next(p for p in body.findall('w:p', NS)
                        if txt(p).startswith('Table 4.6') and p.find('w:hyperlink', NS) is not None)
        at = body.index(template) + 1
        bookmark_id = max(int(b.get(W + 'id')) for b in x.findall('.//w:bookmarkStart', NS)) + 1
        captions = [p for p in body.findall('w:p', NS)
                    if re.match(r'^Table 4\.(?:7|8|9|10) ', txt(p)) and p.find('w:hyperlink', NS) is None]
        assert len(captions) == 4
        for offset, p in enumerate(captions):
            value = txt(p); m = re.match(r'^Table 4\.(\d+) (.+)$', value)
            number, label = m.groups(); anchor = '_FormalResultsTable4_' + number
            assert not x.xpath('.//w:bookmarkStart[@w:name=$name]', namespaces=NS, name=anchor)
            for c in list(p):
                if c.tag != W + 'pPr': p.remove(c)
            E.SubElement(p, W + 'bookmarkStart', {W+'id': str(bookmark_id), W+'name': anchor})
            E.SubElement(E.SubElement(p, W+'r'), W+'t').text = 'Table 4.'
            seq = E.SubElement(p, W+'fldSimple', {W+'instr': ' SEQ Table \\* ARABIC '})
            E.SubElement(E.SubElement(seq, W+'r'), W+'t').text = number
            t = E.SubElement(E.SubElement(p, W+'r'), W+'t'); t.text = ' ' + label
            t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            E.SubElement(p, W+'bookmarkEnd', {W+'id': str(bookmark_id)})
            bookmark_id += 1
            navigation = copy.deepcopy(template)
            hyperlink = navigation.find('w:hyperlink', NS)
            hyperlink.set(W+'anchor', anchor)
            texts = hyperlink.findall('.//w:t', NS)
            texts[0].text = value; texts[-1].text = '0'
            for t in hyperlink.findall('.//w:instrText', NS):
                t.text = ' PAGEREF ' + anchor + ' \\h '
            body.insert(at + offset, navigation)
        # Match numeric alignment and alternating row fill in the retained template.
        for t in body.findall('w:tbl', NS)[17:]:
            for i, row in enumerate(t.findall('w:tr', NS)):
                for j, c in enumerate(row.findall('w:tc', NS)):
                    if i:
                        shading = c.find('w:tcPr/w:shd', NS)
                        if shading is not None: shading.set(W+'fill', 'F2F2F2' if i % 2 == 0 else 'FFFFFF')
                    if j and re.fullmatch(r'\d+', txt(c)):
                        p = c.find('w:p', NS); pr = p.find('w:pPr', NS)
                        if pr is None: pr = E.Element(W+'pPr'); p.insert(0, pr)
                        jc = pr.find('w:jc', NS)
                        if jc is None: jc = E.SubElement(pr, W+'jc')
                        jc.set(W+'val', 'center')
    pdf = PdfReader(qa / 'render/D-DN-3.1_EN.pdf')
    pages = [norm(p.extract_text() or '') for p in pdf.pages]
    updated = []; unresolved = []
    for p in body.findall('w:p', NS):
        h = p.find('w:hyperlink', NS)
        if h is None or not h.findall('.//w:instrText', NS): continue
        texts = h.findall('.//w:t', NS)
        if len(texts) < 2: continue
        label = ''.join(t.text or '' for t in texts[:-1])
        candidates = [i+1-8 for i, page in enumerate(pages) if i >= 8 and norm(label) in page]
        if not candidates:
            # Front matter retains its original Roman-page references.
            if not re.fullmatch(r'[ivxlcdm]+', texts[-1].text or '', re.I): unresolved.append(label)
            continue
        number = str(candidates[0])
        if texts[-1].text != number:
            updated.append({'label': label, 'before': texts[-1].text, 'after': number})
            texts[-1].text = number
    assert not unresolved, unresolved
    modified = E.tostring(x, xml_declaration=True, encoding='UTF-8', standalone=True)
    with tempfile.NamedTemporaryFile(dir=qa, suffix='.docx', delete=False) as stream:
        temporary = Path(stream.name)
    with ZipFile(temporary, 'w') as z:
        for i in infos: z.writestr(i, modified if i.filename == 'word/document.xml' else parts[i.filename])
    temporary.replace(doc)
    receipt = {'navigation_updated': updated, 'new_table_navigation_added': not args.refresh_pages_only,
               'docx_sha256': hashlib.sha256(doc.read_bytes()).hexdigest(), 'unresolved': unresolved}
    name = 'page-refresh-receipt.json' if args.refresh_pages_only else 'layout-receipt.json'
    (qa/name).write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({'navigation_changes': len(updated), 'new_tables': 0 if args.refresh_pages_only else 4}))


if __name__ == '__main__': main()
