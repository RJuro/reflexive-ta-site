#!/usr/bin/env python3
"""G2 gate for the design preview itself: every quote in content/
must appear verbatim (normalized) in transcripts_sample/.

Normalization (documented relaxation of the spec's exact match):
whitespace collapse, curly->straight quotes, strip [PH] phonetic
markers, collapse space-before-punctuation left by the stripping.
"""
import json, re, glob, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def norm(s):
    s = s.replace('’', "'").replace('‘', "'")
    s = s.replace('“', '"').replace('”', '"')
    s = re.sub(r'\[PH\]', '', s)
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\s+([.,?!;:])', r'\1', s)
    return s.strip()

def load_transcripts():
    out = {}
    for f in glob.glob(os.path.join(ROOT, 'transcripts_sample', '*.txt')):
        with open(f, encoding='utf-8', errors='replace') as fh:
            out[os.path.basename(f)] = norm(fh.read())
    return out

def collect_checks():
    checks = []
    with open(os.path.join(ROOT, 'content', 'decisions.json')) as fh:
        for d in json.load(fh)['decisions']:
            q = d['transcript']['quote']
            if not q.startswith('['):           # firewall pseudo-quote is exempt
                checks.append((f"decisions #{d['num']} ({d['id']})", q))
    with open(os.path.join(ROOT, 'content', 'pipeline-walkthrough.json')) as fh:
        for s in json.load(fh)['stages']:
            data = s.get('data') or {}
            for key in ('text', 'quote'):
                if data.get(key):
                    checks.append((f"walkthrough stage {s['num']}", data[key]))
    for rf in sorted(glob.glob(os.path.join(ROOT, 'content', 'runs', '*.json'))):
        if rf.endswith('manifest.json'):
            continue
        with open(rf) as fh:
            run = json.load(fh)
        for p in run.get('paragraphs', []):
            checks.append((f"{run['id']} para {p['id']}", p['text']))
        for st in run.get('steps', []):
            if st.get('quote'):
                checks.append((f"{run['id']} step {st['id']}", st['quote']))
    return checks

def main():
    transcripts = load_transcripts()
    checks = collect_checks()
    fails = []
    for label, q in checks:
        nq = norm(q)
        if not any(nq in t for t in transcripts.values()):
            fails.append((label, q))
    for label, q in fails:
        print(f"FAIL  {label}\n      \"{q[:120]}{'...' if len(q) > 120 else ''}\"")
    print(f"\n{len(checks)} quotes checked, {len(fails)} failed")
    sys.exit(1 if fails else 0)

if __name__ == '__main__':
    main()
