"""Carry instruction-only review across proven identical text; retain all old artifacts."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'project-evidence'
old=json.loads((E/'t1-output-contract-review.json').read_text(encoding='utf-8-sig'))
new={}
for unit in sorted((ROOT/'.validation/track1-20260924/units').iterdir()):
    prior=ROOT/'.validation/track1-current/units'/unit.name/'instruction.md'
    current=unit/'instruction.md'
    assert prior.read_text(encoding='utf-8')==current.read_text(encoding='utf-8'),unit.name
    row=dict(old[unit.name])
    assert hashlib.sha256(prior.read_bytes()).hexdigest()==row['instruction_sha256'],unit.name
    row['instruction_sha256']=hashlib.sha256(current.read_bytes()).hexdigest()
    row['review_provenance']='Prior manual review carried forward after exact decoded-text equality; raw LF SHA updated. No checker or answer data read.'
    new[unit.name]=row
assert len(new)==86
destination=E/'t1-roster-20260924'
destination.mkdir(exist_ok=False)
(destination/'t1-output-contract-review.json').write_text(json.dumps(new,indent=2)+'\n',encoding='utf-8')
print('86 instruction reviews verified against unchanged text')
