"""Amend the existing Chapter4 prospective response count in place."""
import hashlib
import json
import tempfile
import time
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E

ROOT = Path(__file__).resolve().parent.parent
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = ROOT / 'results/RQ3a-response-preparation-v2'
    plan = json.loads((out / 'plan.json').read_text())
    acceptance = json.loads((out / 'preparation-acceptance.json').read_text())
    assert sha(out / 'plan.json') == acceptance['plan_sha256']
    assert plan['counts']['planned_responses'] == 240 and acceptance['responses_started'] == 0
    doc = ROOT / 'D-DN-3.1_EN.docx'
    before = sha(doc)
    with ZipFile(doc) as z:
        infos = z.infolist()
        parts = {i.filename: z.read(i.filename) for i in infos}
    x = E.fromstring(parts['word/document.xml'])
    body = x.find('w:body', NS)
    paragraphs = [p for p in body.findall('w:p', NS) if ''.join(
        t.text or '' for t in p.findall('.//w:t', NS)).startswith('The next planned RQ3a batch contains 600')]
    assert len(paragraphs) == 1, 'Expected precisely one old prospective response paragraph'
    paragraph = paragraphs[0]
    old = ''.join(t.text or '' for t in paragraph.findall('.//w:t', NS))
    new = ('The next planned RQ3a batch contains 240 method-controlled trials: three policies, '
           '40 matched scenarios and two repeats. These are separate from the five unchanged-candidate '
           'replays per scenario already collected for RQ1. The reduction from the earlier 600-trial '
           'plan was declared before response collection, retaining scenario and method coverage '
           'while limiting evidence for response variability. Actual revalidation costs, final execution '
           'routes and independent outcome checks will be retained. This batch and the RQ3b '
           'recurring-incident sequences remain uncollected. RQ3b additionally requires a fixed '
           'replanning implementation and declared sequence factors before its timing comparison '
           'begins. No zero rate is assigned where an eligible denominator or actual response evidence is absent.')
    texts = paragraph.findall('.//w:t', NS)
    texts[0].text = new
    for t in texts[1:]:
        t.text = ''
    modified = E.tostring(x, xml_declaration=True, encoding='UTF-8', standalone=True)
    qa = out / 'document-qa'
    qa.mkdir(exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='rq3a-count-document-') as directory:
        path = Path(directory) / 'after.docx'
        with ZipFile(path, 'w') as z:
            for i in infos:
                z.writestr(i, modified if i.filename == 'word/document.xml' else parts[i.filename])
        with ZipFile(path) as z:
            assert all(z.read(i.filename) == parts[i.filename]
                       for i in infos if i.filename != 'word/document.xml')
        doc.write_bytes(path.read_bytes())
    receipt = {'at': time.time(), 'document': str(doc), 'before_sha256': before,
               'after_sha256': sha(doc), 'section': '4.5.3', 'old': old, 'new': new,
               'amended_plan_sha256': sha(out / 'plan.json'),
               'unchanged_package_parts': 'All except word/document.xml',
               'visual_verification': 'pending'}
    (qa / 'edit-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'edited': True, 'section': '4.5.3', 'planned_responses': 240,
                      'docx_sha256': receipt['after_sha256']}))


if __name__ == '__main__':
    main()
