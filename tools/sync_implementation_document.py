#!/usr/bin/env python3
"""Synchronise verified preparation status into the existing dissertation in place."""
import hashlib,json,shutil,tempfile,time
from pathlib import Path
from zipfile import ZipFile
from lxml import etree as E

ROOT=Path(__file__).resolve().parent.parent
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    acceptance=ROOT/'results/S4-registration-v3/acceptance.json'
    a=json.loads(acceptance.read_text());reg=json.loads((ROOT/'candidates/registration.v1.json').read_text())
    assert a['passed'] and a['successful_qualification_trials']==25 and reg['registered_candidates']==5
    assert reg['qualification_attempts']==a['qualification_attempts'] and reg['main_replay_trials']==0
    attempts=a['qualification_attempts'];rejected=len(a['rejected_trials'])
    patches={
      'The 40 main scenarios will provide further evidence for RQ1':
        'The 40 main scenarios will provide further evidence for RQ1 and test cases for RQ3a. Their outcome labels will not be used to tune the assessor. RQ3b will evaluate repeated-incident response sequences after the same rule freeze. Development and pilot cases remain separate from the main evaluation. Source pinning, static screening, fault pairing, 40 feasible scenario constructions and registration qualification are complete. Five unchanged candidates have been registered after five successful qualification trials each. The prototype, current verifier definitions, matched controls and comparison settings still require pre-main freeze. No main replay or RQ3 comparison result has been collected. Section 3.5 records preparation evidence rather than answers to RQ3.',
      'The primary testbed uses Robot Shop (Instana, n.d.)':
        'The primary testbed uses Robot Shop (Instana, n.d.) on an Apple silicon Mac with 16 GiB of memory. The recorded sources are Robot Shop commit 55292e2 with chart 1.1.0, KubePlaybook v1.0.0 at 57136d0, and MicroRemed at f579d32. Full source identifiers, upstream digests and exact local image IDs are retained in sources/sources.lock.yaml. The three-node cluster runs k3s v1.33.5-k3s1 under k3d v5.9.0. Docker supplies eight CPUs and approximately 7.75 GiB. Application services run as native arm64; MySQL 5.7.44 remains amd64 under emulation. Locally rebuilt images include recorded runtime and dependency adaptations. Retained image IDs establish the tested binaries; byte-identical future rebuilds are not claimed.',
      'The preliminary deployment required four groups of baseline settings.':
        'The declared qualification setup is manifests/qualification.v3.yaml with its saved fingerprint. Redis uses local-path storage. Cart, catalogue, dispatch, payment, user, web and redis retain 128 Mi memory requests and 256 Mi limits. Ratings uses 192 Mi/512 Mi, shipping 640 Mi/2048 Mi with a 2000m CPU limit, and the load generator 384 Mi/1024 Mi. Five clients and random seed 20261001 remain fixed. The complete manifest records all resources and node placement. PAYMENT_GATEWAY points to http://user:8080/health as an explicit local HTTP-200 substitute for the upstream dummy call. Real payment processing and external gateway reliability are outside this evaluation. These declared envelopes are controlled settings, not minimum-resource estimates.',
      'These settings followed a pending Redis volume claim,':
        'Reset is an explicit per-trial procedure. MySQL mounts an independent work PVC at /var/lib/mysql, uses Recreate, and receives the fixed seed through a restore Job before each trial. External application-account queries through its Service confirm 948,833 city rows and zero ratings before load starts. MongoDB and RabbitMQ are recreated and Redis working data is cleared. Data stores start before application services and load starts last. Three contamination/reset cycles passed. Qualification resets check the workload set, zero restarts, ready endpoints, image IDs, configuration fingerprint and six response-content predicates. Normal writes are permitted after load starts. This evidence establishes the declared qualification baseline; current v3 replica fidelity and main outcome criteria require separate validation.',
      'The prototype tool will use Python for the evidence record,':
        'The prototype tool will use Python for evidence records, requirement checks and decision rules. Qualification used Python 3.12.13, Ansible Runner 2.4.2 and Ansible core 2.19.3. Runner events, Kubernetes queries and business responses supplied execution and recovery evidence. The prototype will accept a registered candidate and permitted current observations, then return a decision with requirement-level reasons. Its remaining dependencies, evidence policy, validation budgets and comparison settings will be fixed before main data collection.',
      'The planned sample contains five qualifying Ansible playbooks.':
        'The selected sample contains five registered Ansible playbooks covering workload recovery, replica adjustment, resource adjustment, configuration repair and Service routing repair. P1, P2 and P3 were adapted from KubePlaybook cases 12/1, 7 and 3; P4 and P5 were prepared manually. Origin, source identifiers, modifications and selection reasons are retained in candidates/registry.yaml. Code, input bindings and existing discovery rules remained unchanged throughout qualification. candidates/registration.v1.json links each frozen playbook to its successful execution evidence, declared setup, fault and four-condition requirements. Registration establishes baseline repair evidence, not successful reuse under the later changes.',
      'Qualification has four stages. S1 checks syntax,':
        f'Qualification has four stages. S1 checks syntax, non-interactive execution, supported operations and the declared repair family. S2 verifies observable and persistent faults, recovery and confirmed reset; five independent no-repair/repair pairs passed. S3 established 20 explicit requirements and 40 feasible constructions without replaying the candidates. S4 requires five successful trials per candidate under the same declared setup. All 25 required successes were obtained in {attempts} recorded attempts. Both rejected attempts are retained: one probe-exec timeout left an incomplete window; one catalogue timeout preceded execution and failed the conservative full-window gate. Neither counts as success, and candidate causation is not established. S4 retained a 210-second window, 60-second pre-repair delay, 120-second repair budget, necessary-task and current-owner checks, and confirmed resets. Its full-window gate remains distinct from the immediately-before/post-execution regression definition in Section 3.6.',
      'The configuration candidates require further validation.':
        'The original configuration candidates were not selected. Candidate 9/1 requires a positional environment-variable path absent from the baseline, and consumption of 9/4\'s USE_CONNECTION_POOLING was not found in the pinned web source. P4 instead restores CATALOGUE_HOST=catalogue by name and recreates web containers. The image declares this variable and entrypoint.sh substitutes it into the nginx template, so the setting has a verified consumption path and takes effect after recreation. Its manual identity and fixed action were recorded before qualification. Adding an unused variable solely to make a command succeed would not qualify a repair.',
      'For Service routing, a minimal manually prepared candidate will restore':
        'For Service routing, P5 is a minimal manual candidate that restores only selector.service=web on Service web. Its fault changes that selector; its action does not change targetPort. The original 14/1 remains recorded as rejected at S1. P5 underwent the same S1-S4 procedure as the repository candidates and obtained five successful qualification trials. Routing verification traversed the Service and checked its current serving workload rather than treating direct Pod connectivity as recovery.'}
    document=ROOT/'D-DN-3.1_EN.docx';before=sha(document)
    with ZipFile(document) as z:
        original={i.filename:z.read(i.filename) for i in z.infolist()};infos=z.infolist()
    parser=E.XMLParser(resolve_entities=False,no_network=True)
    tree=E.fromstring(original['word/document.xml'],parser)
    paragraphs=tree.findall('.//w:p',NS);changes=[]
    for prefix,new in patches.items():
        matches=[p for p in paragraphs if ''.join(t.text or '' for t in p.findall('.//w:t',NS)).startswith(prefix)]
        if len(matches)!=1:raise RuntimeError('Ambiguous source paragraph: '+prefix)
        p=matches[0];assert not p.findall('.//w:fldChar',NS) and not p.findall('.//w:hyperlink',NS)
        texts=p.findall('.//w:t',NS);old=''.join(t.text or '' for t in texts)
        texts[0].text=new
        for t in texts[1:]:t.text=''
        changes.append({'old':old,'new':new})
    backup_dir=Path(tempfile.mkdtemp(prefix='ddn-3.1-sync-'));backup=backup_dir/'before.docx';shutil.copy2(document,backup)
    destination=backup_dir/'updated.docx'
    modified=E.tostring(tree,xml_declaration=True,encoding='UTF-8',standalone=True)
    with ZipFile(destination,'w') as z:
        for info in infos:z.writestr(info,modified if info.filename=='word/document.xml' else original[info.filename])
    with ZipFile(destination) as z:
        assert set(z.namelist())==set(original)
        assert all(z.read(name)==value for name,value in original.items() if name!='word/document.xml')
        assert len(E.fromstring(z.read('word/document.xml')).findall('.//w:p',NS))==len(paragraphs)
    shutil.copyfile(destination,document)
    receipt=ROOT/'results/S4-registration-v3/document-sync.json'
    assert not receipt.exists()
    receipt.write_text(json.dumps({'edited_at':time.time(),'document':str(document),'before_sha256':before,
      'after_sha256':sha(document),'temporary_backup':str(backup),'qualification_acceptance':str(acceptance.relative_to(ROOT)),
      'qualification_acceptance_sha256':sha(acceptance),'changed_paragraphs':changes,
      'structure':'Same paragraph count; all package parts except document.xml remain byte-identical. Existing styles, fields, links and bibliography retained.','visual_verification':'pending'},indent=2)+'\n')
    print(json.dumps({'changed_paragraphs':len(changes),'document':str(document),'temporary_backup':str(backup)}))
    return 0
if __name__=='__main__':raise SystemExit(main())
