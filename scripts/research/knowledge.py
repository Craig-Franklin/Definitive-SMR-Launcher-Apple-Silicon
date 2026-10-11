#!/usr/bin/env python3
"""Bind original scanner rules to private, matching-build static evidence.

No pseudocode or extracted identity names are printed. Scanner reports remain
private because their pre-existing payloads contain local map data and paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import corpus as c

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from smr_launcher.industry_registry import (SUPPORTED_GAME_SHA256,
    INDUSTRY_TABLE_ADDRESS, INDUSTRY_TABLE_COUNT, extract_supported_industry_registry)

CATALOG=ROOT/'docs/rea-research/knowledge-catalog.json'


def catalog():
    value=c.load(CATALOG)
    if set(value)!={'schema','findings','rules'} or value['schema']!=1:
        raise c.Refusal('catalog schema')
    ids={r['id'] for r in value['findings']}
    if len(ids)!=len(value['findings']):raise c.Refusal('duplicate finding')
    for rule, finding in value['rules'].items():
        if finding not in ids:raise c.Refusal('unknown finding reference')
    return value,c.file_sha(CATALOG)


def bind(root):
    """Independent Ghidra data-reference/string cross-check of the byte reader.

    The table address/count are the existing original reader's hypothesis.
    Compare its output to Ghidra's separately imported static data, without
    embedding the extracted names in an original finding or cloud response.
    """
    root=c.private_root(root);value=c.manifest(root);cat,revision=catalog()
    matches=[m for m in value['modules'] if m['sha256']==SUPPORTED_GAME_SHA256 and m['architecture']=='x86_64']
    if len(matches)!=1:raise c.Refusal('supported module not uniquely matched')
    m=matches[0];out=c.contained(root,'modules/'+m['id']+'/export')
    rec=c.load(out.parent/'receipt.json')
    ex=c.export_digest(out,m['sha256'])
    if rec.get('status')!='complete' or rec['export']!=ex:raise c.Refusal('unverified export')
    expected=extract_supported_industry_registry(c.read(c.contained(root,m['copy'])))
    slots={INDUSTRY_TABLE_ADDRESS+i*8:[] for i in range(INDUSTRY_TABLE_COUNT)}
    for row in c.json_lines(out/'references.jsonl'):
        try:source=int(row['from'],16);target=int(row['to'],16)
        except ValueError:continue
        if source in slots:slots[source].append(target)
    targets={target for values in slots.values() for target in values};strings={}
    for row in c.json_lines(out/'strings.jsonl'):
        try:address=int(row['address'],16)
        except ValueError:continue
        if address in targets:strings[address]=row['value']
    observed=[]
    for slot in sorted(slots):
        candidates={strings[t] for t in slots[slot] if t in strings}
        if len(candidates)!=1:raise c.Refusal('table reference/string coverage incomplete')
        observed.append(next(iter(candidates)))
    if tuple(observed)!=expected:raise c.Refusal('independent static table cross-check mismatch')
    binding={'schema':1,'catalog_sha256':revision,'game_sha256':m['sha256'],
             'architecture':m['architecture'],'image_uuid':m['image_uuid'],
             'module':m['id'],'export_digest':ex['digest'],
             'findings':['SMR-K-001'],'evidence':'independent-static-table-cross-check',
             'entries_matched':len(observed),'runtime_checks':0}
    c.write(root/'knowledge-binding.json',binding)
    return {'status':'bound','findings':1,'entries_matched':len(observed),'runtime_checks':0}


def consultation(game_sha256, rules, root=None):
    """Validate every rule reference; preserve partial coverage honestly."""
    c.digest_string(game_sha256)
    cat,revision=catalog();rules=sorted(set(rules))
    if any(rule not in cat['rules'] for rule in rules):raise c.Refusal('unmapped scanner rule')
    findings=sorted({cat['rules'][rule] for rule in rules})
    record={'status':'unavailable','catalog_sha256':revision,'game_sha256':game_sha256,
            'target_matched':False,'rule_ids':rules,'finding_ids':findings,
            'evidence_findings':[],'gaps':['private-corpus-not-supplied'],
            'runtime_checks':0}
    if root is None:return record
    root=c.private_root(root);m=c.manifest(root);binding=c.load(root/'knowledge-binding.json')
    if binding.get('catalog_sha256')!=revision:raise c.Refusal('stale knowledge catalog')
    if binding.get('game_sha256')!=game_sha256 or game_sha256!=SUPPORTED_GAME_SHA256:
        record['gaps']=['game-build-mismatch'];return record
    modules=[r for r in m['modules'] if r['id']==binding['module'] and r['sha256']==game_sha256 and r['architecture']==binding['architecture'] and r['image_uuid']==binding['image_uuid']]
    if len(modules)!=1:raise c.Refusal('knowledge module identity mismatch')
    rec=c.load(c.contained(root,'modules/'+binding['module']+'/receipt.json'))
    if rec.get('status')!='complete' or rec['export']['digest']!=binding['export_digest']:
        raise c.Refusal('knowledge export changed')
    if c.export_digest(c.contained(root,'modules/'+binding['module']+'/export'),game_sha256)!=rec['export']:
        raise c.Refusal('knowledge evidence digest mismatch')
    if binding.get('evidence')!='independent-static-table-cross-check' or binding.get('entries_matched')!=INDUSTRY_TABLE_COUNT or binding.get('findings')!=['SMR-K-001']:
        raise c.Refusal('invalid evidence binding')
    supported=sorted(set(findings)&set(binding['findings']))
    gaps=sorted(set(findings)-set(supported))
    record.update(status='partial' if gaps else 'matched',target_matched=True,
                  evidence_findings=supported,gaps=gaps,export_digest=binding['export_digest'])
    return record


def annotate_findings(findings):
    cat,_=catalog()
    for finding in findings:
        code=finding['code']
        if code not in cat['rules']:raise c.Refusal('unmapped scanner rule')
        finding['rule_id']=code;finding['finding_id']=cat['rules'][code]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['bind','catalog'])
    parser.add_argument('--root',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='bind':
            if args.root is None:raise c.Refusal('private root required')
            result=bind(args.root)
        else:
            cat,revision=catalog();result={'schema':1,'catalog_sha256':revision,'findings':[r['id'] for r in cat['findings']],'rules':len(cat['rules'])}
        print(json.dumps(result,sort_keys=True))
    except Exception:parser.exit(2,'Refused knowledge binding; raw evidence remains private.\n')


if __name__=='__main__':main()
