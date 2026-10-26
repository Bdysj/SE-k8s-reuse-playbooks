"""Durable local continuation: audited calibration, freeze, then serial collection.

This is a user-authorised experiment process, not a scheduled automation.
It never silently retries rejected trials and never uses a replanning API.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
import formal_collection_v2 as collection
import prepare_formal_main as prepare

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--calibration',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    calibration=args.calibration.resolve();out=args.output.resolve()
    # Runtime directory is separate from the immutable protocol and trial files.
    runtime=out/'runtime';runtime.mkdir(parents=True,exist_ok=True)
    collection.save(runtime/'coordinator.json',{'pid':os.getpid(),'at':time.time(),
                                               'mode':'calibration -> freeze -> formal queue'})
    try:
        deadline=time.monotonic()+7200
        while not (calibration/'collection-acceptance.json').exists():
            progress=calibration/'runtime/progress.json'
            if progress.exists() and json.loads(progress.read_text()).get('state')=='halted':
                raise RuntimeError('Calibration halted; formal protocol not frozen and no main trial launched')
            worker=calibration/'runtime/worker.json'
            if worker.exists():
                pid=json.loads(worker.read_text())['pid']
                try:os.kill(pid,0)
                except ProcessLookupError:raise RuntimeError('Calibration worker exited without acceptance')
            if time.monotonic()>=deadline:raise RuntimeError('Calibration wait exceeded2h; no main trial launched')
            collection.save(runtime/'launch-state.json',{'state':'waiting for supplementary calibration',
                'at':time.time(),'formal_main_replays_completed':0})
            time.sleep(10)
        tests=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(collection.ROOT/'tools'),
                              '-p','test_formal_collection.py','-v'],capture_output=True,text=True)
        (runtime/'pre-freeze-checks.log').write_text(tests.stdout+tests.stderr)
        if tests.returncode:raise RuntimeError('Prospective formal boundary checks failed')
        if (out/'protocol.json').exists():collection.check(out)
        else:prepare.prepare(out,calibration)
        collection.save(runtime/'launch-state.json',{'state':'formal queue started','at':time.time(),
                                                    'protocol_sha256':collection.sha(out/'protocol.json')})
        collection.run(out)
    except Exception as error:
        collection.save(runtime/'coordinator-error.json',{'at':time.time(),'error':str(error),
                                                         'traceback':traceback.format_exc()})
        raise

if __name__=='__main__':main()
