"""Insert audited collection results into the retained dissertation package."""
import copy
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as E

ROOT = Path(__file__).resolve().parent.parent
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def content(p):
    return ''.join(t.text or '' for t in p.findall('.//w:t', NS))


def main():
    analysis_dir = ROOT / 'results/formal-main-analysis-v1'
    audit = json.loads((analysis_dir / 'final-audit.json').read_text())
    a = json.loads((analysis_dir / 'analysis.json').read_text())
    freeze = json.loads((analysis_dir / 'analysis-freeze.json').read_text())
    assert audit['accepted'] and audit['accepted_records_reaudited'] == 295
    assert a['main']['outcomes'] == {'Successful repair': 125, 'Confirmed failure': 75}
    # Verify the complete immutable derivation, rather than a hand-copied count.
    for name, digest in freeze['files'].items():
        assert sha(analysis_dir / name) == digest, name
    flow = json.loads((ROOT / 'results/reuse-flow-development-v8/final-acceptance.json').read_text())
    assert flow['accepted'] and len(flow['records']) == 11
    target = ROOT / 'D-DN-3.1_EN.docx'
    qa = ROOT / 'results/formal-results-document-v1'
    qa.mkdir(exist_ok=True)
    assert not (qa / 'edit-receipt.json').exists(), 'Preserve prior document edit receipt'
    with ZipFile(target) as z:
        infos = z.infolist()
        original = {i.filename: z.read(i.filename) for i in infos}
    tree = E.fromstring(original['word/document.xml'], E.XMLParser(resolve_entities=False, no_network=True))
    body = tree.find('w:body', NS)
    changes = []

    def find(prefix):
        matches = [p for p in body.findall('w:p', NS) if content(p).startswith(prefix)]
        assert len(matches) == 1, (prefix, len(matches))
        return matches[0]

    def text(p, value):
        for child in list(p):
            if child.tag != W + 'pPr':
                p.remove(child)
        r = E.SubElement(p, W + 'r')
        t = E.SubElement(r, W + 't')
        t.text = value
        return p

    def patch(prefix, value):
        p = find(prefix)
        changes.append({'old': content(p), 'new': value})
        text(p, value)

    body_template = copy.deepcopy(find('This chapter reports the experimental evidence'))
    caption_template = copy.deepcopy(next(p for p in body.findall('w:p', NS)
                                          if content(p) == 'Table 4.6 Coverage of feasible scenario constructions'))
    table_template = copy.deepcopy(body.findall('w:tbl', NS)[16])

    def paragraph(value, caption=False):
        p = text(copy.deepcopy(caption_template if caption else body_template), value)
        if caption:
            pr = p.find('w:pPr', NS)
            if pr.find('w:keepNext', NS) is None:
                E.SubElement(pr, W + 'keepNext')
        return p

    def table(rows, widths):
        t = copy.deepcopy(table_template)
        grid = t.find('w:tblGrid', NS)
        for x in list(grid):
            grid.remove(x)
        for width in widths:
            E.SubElement(grid, W + 'gridCol', {W + 'w': str(width)})
        row_templates = t.findall('w:tr', NS)
        header, normal = copy.deepcopy(row_templates[0]), copy.deepcopy(row_templates[1])
        for r in row_templates:
            t.remove(r)
        for index, values in enumerate(rows):
            r = copy.deepcopy(header if index == 0 else normal)
            cells = r.findall('w:tc', NS)
            cell_template = copy.deepcopy(cells[0])
            for c in cells:
                r.remove(c)
            pr = r.find('w:trPr', NS)
            if pr is None:
                pr = E.Element(W + 'trPr'); r.insert(0, pr)
            if pr.find('w:cantSplit', NS) is None:
                E.SubElement(pr, W + 'cantSplit')
            if index == 0 and pr.find('w:tblHeader', NS) is None:
                E.SubElement(pr, W + 'tblHeader')
            for value, width in zip(values, widths):
                c = copy.deepcopy(cell_template)
                c.find('w:tcPr/w:tcW', NS).set(W + 'w', str(width))
                ps = c.findall('w:p', NS)
                p = ps[0]
                for extra in ps[1:]:
                    c.remove(extra)
                text(p, str(value))
                # Retain the source table's typography and spacing; only replace text.
                source_run = cell_template.find('w:p/w:r/w:rPr', NS)
                if source_run is not None:
                    p.find('w:r', NS).insert(0, copy.deepcopy(source_run))
                r.append(c)
            t.append(r)
        return t

    def replace_block(prefix, entries):
        p = find(prefix)
        index = body.index(p)
        changes.append({'old': content(p), 'new_blocks': len(entries)})
        body.remove(p)
        for offset, entry in enumerate(entries):
            body.insert(index + offset, paragraph(entry) if isinstance(entry, str) else entry)

    patch('The 40 main scenarios will provide further evidence for RQ1',
          'The formal collection completed on 6 October 2026 comprises 40 changed-condition scenarios and five unchanged-candidate replays per scenario. These 200 main replays provide evidence for RQ1 and matched inputs for RQ3a; 95 separate controls support interpretation. The assessor, verifier definitions, comparison policies and budgets were frozen before collection. Main outcomes are not used to tune them. RQ3a initial decisions have been recorded, while actual method-controlled response trials and RQ3b recurring-incident sequences remain separate evaluations. Development and pilot records are excluded from the main sample. Section 3.5 records preparation evidence; Chapter 4 reports the completed collection.')
    patch('The prototype tool will use Python for evidence records',
          'The implemented prototype uses Python for evidence records, requirement checks and bounded reuse decisions. The retained environment uses Python 3.12.13, Ansible Runner 2.4.2 and Ansible core 2.19.3. Runner events, Kubernetes queries and business responses supply execution and recovery evidence. The prototype accepts a registered candidate and permitted current observations, then records requirement-level states, a decision and its subsequent route. Eleven separately recorded development cases verified direct execution, refresh, isolated replay, failed validation, contradiction and budget exhaustion. The formal collection froze the executable rules, 30-second evidence lifetime, one refresh, one isolated replay, 600-second decision budget and six-assessment limit before main data collection.')
    patch('This chapter reports the experimental evidence completed by',
          'This chapter reports evidence completed by 6 October 2026. The formal collection contains 200 unchanged-candidate replays across 40 changed-condition scenarios and 95 controls. Of the 200 complete main replays, 125 achieved Successful repair and 75 produced Confirmed failure. The implemented assessor and two comparison policies also produced initial decisions on the same recorded inputs. These initial decisions support a diagnostic comparison; actual method-controlled revalidation and response trials, independent evidence review and RQ3b recurring-incident comparisons are not yet complete.')
    patch('The counts are supported by the stage-specific acceptance records',
          'Stage-specific acceptance records and candidates/registration.v1.json support the preparation counts. The frozen formal protocol and results/formal-main-v1/collection-acceptance.json link all 295 accepted collection records to their inputs and outcome evidence. An independent offline audit in results/formal-main-analysis-v1/final-audit.json rechecked every record, recomputed 750 saved initial decisions across the 250 repair records and three policies, and reproduced 800 main ablation decisions. Three additional interrupted attempts remain archived. No raw record, threshold or decision rule was rewritten for this analysis.')
    stage_table = body.findall('w:tbl', NS)[11]
    cells = stage_table.findall('w:tr', NS)[-1].findall('w:tc', NS)
    for c, value in zip(cells, ['Formal collection', '40 scenarios; 200 main replays; 95 controls', '295 accepted; 3 extra attempts retained']):
        text(c.find('w:p', NS), value)
    patch('The qualification baseline is manifests/qualification.v3.yaml.',
          'The formal baseline retains the resources, image identities and placement declared in manifests/qualification.v3.yaml. Five Locust users and seed 20261001 are fixed. Ratings retains a 512 Mi memory limit, shipping 2048 Mi and a 2000m CPU limit, and the generator 1024 Mi. Before formal collection, the accepted common resolver profile added single-request-reopen to cart, catalogue and user; it is retained in results/formal-main-v1/baseline.yaml and is not a candidate repair. These envelopes are controlled settings rather than minimum resource estimates. Exact tested image IDs and source commits are recorded in sources/sources.lock.yaml.')
    patch('The final audit in results/S4-registration-v3/acceptance.json retains',
          'The final audit in results/S4-registration-v3/acceptance.json retains all qualification records. Registration links each unchanged candidate to five successful baseline traces and its four-condition requirements. Qualification supplies historical evidence for subsequent reuse assessment. Section 4.5.1 reports the separately collected changed-condition outcomes; those outcomes do not retrospectively alter registration.')
    patch('No frozen playbook was executed during S3.',
          'No frozen playbook was executed during S3. The feasibility evidence in results/S3-feasibility-v1/acceptance.json therefore establishes neither successful reuse nor failed reuse. These 40 constructions were subsequently registered in the frozen formal protocol with current serving verifiers, changed replica minima, calibrated resource faults and same-fault controls. Their later repair outcomes are reported separately in Section 4.5.1.')

    replace_block('RQ1 currently has five registered candidates', [
        'The frozen collection queue comprised 40 scenarios, each replayed five times with its registered candidate unchanged, and 95 controls. Every accepted record required a confirmed baseline reset, a healthy changed environment before injection, a persistent observable fault, current outcome verification and a confirmed final reset. The full observation window was 210 seconds, with at least 30 seconds of sustained recovery required. Measured complete windows ranged from 210.61 to 215.54 seconds; no complete result was assigned from an unfinished window.',
        'The main outcomes were 125 Successful repairs (62.5%) and 75 Confirmed failures (37.5%). Each playbook contributed 40 trials, with 25 successes and 15 failures. At scenario level, 25 cases succeeded in all five repeats and 15 failed in all five repeats. Table 4.7 groups results by the primary construction condition. These labels identify construction groups; recorded overlaps prevent treating the four groups as independent causal effects.',
        paragraph('Table 4.7 Main replay outcomes by primary construction condition', True),
        table([['Primary condition', 'Trials', 'Successful', 'Failed'],
               ['Target objects', 50, 25, 25], ['Referenced fields', 50, 25, 25],
               ['Resource relationships', 50, 25, 25], ['Verification criteria', 50, 50, 0],
               ['Total', 200, 125, 75]], [3600, 1050, 1400, 1350]),
        'Among the 75 failures, 35 lacked complete necessary-action execution, all 75 failed current target recovery, and four had newly failing non-target checks after execution. These components overlap and must not be added as exclusive outcomes. All four non-target flags occurred in P2 resource-relationship cases. The checks establish the operational regression label, without proving that the candidate was the unique cause of every observed failure.',
        paragraph('Table 4.8 Separate formal controls', True),
        table([['Control arm', 'Trials', 'Observed result'],
               ['Original-condition repair', 25, '25 Successful repairs'],
               ['Unrelated-change repair', 25, '25 Successful repairs'],
               ['Changed-condition no repair', 40, '40 persistent faults'],
               ['Original-condition no repair', 5, '5 persistent faults']], [3100, 1000, 3300]),
        'All original-condition and unrelated-change repair controls succeeded. Every no-repair control retained the target fault throughout its window, so withdrawal of the injection is not evidence of repair. The original-condition repair controls comprise five repeats per playbook shared by its eight variants; they are not 200 independent paired controls. The unrelated changes were unused ConfigMap changes declared before collection.',
        'Three additional collection attempts were retained separately. At position 76, a collector query used a historical subject name absent after a declared replacement; no candidate action occurred. At position 144, the candidate ran but a probe-exec transport interruption left an incomplete window, giving Indeterminate. At position 211, baseline acceptance failed before injection or action, and that original attempt did not confirm final reset. A separately recorded recovery later restored the baseline before retry. Both setup rejections remain visible and are excluded from the candidate-execution denominator.',
        'Consequently, the all-attempt main execution denominator is 201: 200 determinate executions and one Indeterminate execution, with 99.50% evidence availability. The 62.5% success rate is conditional on determinate evidence. Counting the uncertain attempt at either extreme gives an all-attempt success range of 62.19% to 62.69%. Its accepted retry does not erase the incomplete attempt. In total, 298 collection attempts produced the 295 planned complete records.',
        'Repeated trials remain grouped within their scenario and playbook. A descriptive sensitivity analysis resampled the eight scenario groups within each of the five fixed playbooks, retaining all five repeats, for 5000 replicates with seed 20261006. The 95% percentile interval for the main success fraction was 47.5% to 77.5%. This interval is conditional on the constructed sample and does not estimate a population-wide success rate.',
        'The 295 accepted records consumed 44.65 hours of active collection time; elapsed collection time including interruptions was 48.30 hours. The mean accepted cycle lasted 544.86 seconds and included setup, injection, observation and reset. These durations are collection costs, not comparative incident-to-recovery times.'
    ])
    replace_block('The registration records provide frozen actions', [
        'The executable method maps frozen requirements to attributed observations of logical targets, consumed configuration fields, serving and scheduling relationships, and current business criteria. Each requirement produces Satisfied, Contradicted or Unknown with its reason and source identity. The workflow retains its initial decision, refresh and isolated-validation events, final permission, action outcome and reset receipts. A common gate checks candidate identity, permitted operations, current resource revisions and fresh evidence immediately before execution.',
        'The eleven development cases accepted under the common resolver profile included seven successful live repairs and four cases with no live candidate action. Five cases used successful isolated replays before live execution; another isolated replay produced Confirmed failure and blocked live reuse. Separate cases checked contradiction, stale-observation refresh, exhausted budgets and controlled validation timeout. Certificates were published from completed replica outcomes before cleanup, and successful live actions consumed them within their 30-second lifetime. These checks establish implementation behaviour for the declared development cases; they do not establish replica fidelity for every main scenario.',
        'The frozen formal configuration allowed one observation refresh, one isolated replay, at most six assessments and a 600-second decision budget. Missing or stale observations did not become Satisfied by default. A separate offline evidence experiment performed 50 computations using five calibration observations, five missing-observation forms and five stale ages per candidate. Every computation yielded Unknown states and Revalidate. No cluster mutation, physical evidence refresh or isolated repair occurred in these 50 computations.',
        'For independent review, 251 evidence packets were prepared from 200 complete main repair records, 50 repaired controls and the additional Indeterminate execution. They omit recorded assessor states, decisions and outcomes. At the reporting cutoff no independent reviewer had completed this review. Automated labels are therefore not presented as independent expert annotations.'
    ])
    replace_block('There are no main decision records comparing', [
        'The three frozen policies assessed the same permitted pre-action observations in each of the 200 main replays. Table 4.9 reports their initial decisions. The proposed policy used explicit four-condition requirements. Fault matching used its previously calibrated incident-similarity threshold; whole-environment comparison requested revalidation whenever its canonical environment differed. The threshold and normalisation settings were not fitted to the main outcomes.',
        paragraph('Table 4.9 Initial decisions on the 200 main inputs', True),
        table([['Policy', 'Direct reuse', 'Revalidate', 'Stop reuse'],
               ['Proposed method', 95, 35, 70], ['Fault matching', 150, 0, 50],
               ['Whole-environment comparison', 0, 200, 0]], [3100, 1400, 1400, 1500]),
        'Cross-tabulation against the independently executed unchanged candidate showed that all 95 proposed Direct reuse decisions corresponded to Successful repair. Its 35 Revalidate decisions corresponded to 30 successes and five failures, while its 70 Stop reuse decisions corresponded to 70 failures. Fault matching selected Direct reuse for 95 successful and 55 failed replays, and Stop reuse for 30 successful and 20 failed replays. Both policies initially selected 95 of the 125 successful reuse opportunities (76%). Whole-environment comparison selected none directly, but this does not imply that it would retain none after revalidation.',
        'These are diagnostic initial-decision comparisons. The collection deliberately executed the unchanged candidate independently of the policy recommendation to obtain RQ1 outcomes. It did not follow each policy through its actual refresh, isolated replay and final live permission. The cross-tabulation is therefore not a measured invalid-execution rate for the complete method-controlled response process, nor evidence of comparative recovery-time savings.',
        paragraph('Table 4.10 Offline omission of one condition family', True),
        table([['Omitted condition', 'Changed decisions', 'Direct reuse', 'Revalidate', 'Stop reuse'],
               ['Target objects', 5, 95, 40, 65], ['Referenced fields', 25, 110, 40, 50],
               ['Resource relationships', 20, 115, 35, 50], ['Verification criteria', 30, 125, 5, 70]],
              [2700, 1200, 1150, 1150, 1200]),
        'The 800 offline ablation computations omitted one condition family at a time without changing the candidate or observed environment. Compared with the complete rule, omission changed five, 25, 20 and 30 decisions respectively. Field, relationship and criterion omissions increased initial Direct reuse counts. These computations show sensitivity of the implemented decision rule in this sample; no ablated policy was executed live, and the results do not establish that the condition families are independent or exhaustive.',
        'The next planned RQ3a batch contains 600 method-controlled trials: three policies, 40 matched scenarios and five repeats. It must retain actual revalidation costs, final execution routes and independent outcome checks. This batch and the RQ3b recurring-incident sequences remain uncollected. RQ3b additionally requires a fixed replanning implementation and declared sequence factors before its timing comparison begins. No zero rate is assigned where an eligible denominator or actual response evidence is absent.'
    ])
    patch('The completed evidence establishes an accepted laboratory baseline',
          'The complete main records show that an action with repeated historical baseline success may fail after a feasible changed condition. The 75 failures coexist with successful original-condition and unrelated-change controls and persistent no-repair faults. Forty failed trials completed the necessary action but did not recover the current target, showing why command completion alone is insufficient. All 50 verification-criterion trials succeeded under current checks, while some required stronger evidence before reuse could be justified. An evidence requirement can change without making the unchanged action ineffective.')
    patch('Before main collection, the protocol must freeze',
          'The formal collection froze candidates, current verifiers, controls, policies and budgets before replay. The historical-subject collector correction and operational recoveries remain versioned, with interrupted attempts preserved rather than converted into complete successes. The 210-second observation window supports bounded short-term recovery only. Shared controls, five repeats within a scenario, constructed rather than randomly sampled conditions and recorded condition overlaps limit generalisation. Independent evidence review, actual method-controlled RQ3a responses and recurring RQ3b sequences remain necessary before claiming complete decision reliability or response-time savings. None of the reported main outcomes authorises retrospective rule tuning.')
    patch('At the reporting cutoff, baseline reset acceptance',
          'Baseline acceptance and S1-S4 qualification supplied five frozen candidates and 40 feasible scenarios. The completed formal collection then produced 200 main replays and 95 controls. Main repair outcomes were 125 successes and 75 confirmed failures, with a separate executed but incomplete attempt retained as Indeterminate. The RQ2 workflow passed its declared development checks, and initial policy comparisons and offline ablations are available. Actual method-controlled RQ3a response trials, independent evidence review and RQ3b recurring-incident results remain pending; this chapter makes no comparative recovery-time claim.')

    before = sha(target)
    temporary = Path(tempfile.mkdtemp(prefix='ddn-formal-results-'))
    backup = temporary / 'before.docx'
    shutil.copy2(target, backup)
    output = temporary / 'updated.docx'
    modified = E.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True)
    with ZipFile(output, 'w') as z:
        for info in infos:
            z.writestr(info, modified if info.filename == 'word/document.xml' else original[info.filename])
    with ZipFile(output) as z:
        assert all(z.read(name) == value for name, value in original.items() if name != 'word/document.xml')
    shutil.copyfile(output, target)
    receipt = {'edited_at': time.time(), 'document': str(target), 'before_sha256': before,
               'after_sha256': sha(target), 'temporary_backup': str(backup),
               'analysis_freeze_sha256': sha(analysis_dir / 'analysis-freeze.json'),
               'changes': changes, 'unchanged_package_parts': 'All except word/document.xml',
               'visual_verification': 'pending'}
    (qa / 'edit-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'document': str(target), 'changed_paragraphs': len(changes), 'added_tables': 4,
                      'backup': str(backup)}))


if __name__ == '__main__':
    main()
