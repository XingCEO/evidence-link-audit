"""Detect stale file-and-line evidence in AI-generated engineering reports."""

import hashlib
from pathlib import PurePosixPath


def analyze(data):
    files=data['files']; results=[]; ids=set()
    for record in data['evidence']:
        key=record['id']; path=record['path']
        if key in ids: raise ValueError('duplicate evidence id: '+key)
        ids.add(key)
        p=PurePosixPath(path)
        if p.is_absolute() or '..' in p.parts: raise ValueError('evidence paths must be repository-relative')
        result={'id':key,'path':path,'issues':[]}
        if path not in files:
            result['issues'].append('missing_file')
        else:
            lines=files[path].splitlines(); start=record['start']; end=record.get('end',start)
            if type(start) is not int or type(end) is not int or start<1 or end<start or end>len(lines):
                result['issues'].append('invalid_line_range')
            else:
                excerpt='\n'.join(lines[start-1:end]); digest=hashlib.sha256(excerpt.encode()).hexdigest()
                result.update(start=start,end=end,sha256=digest)
                if 'sha256' in record and record['sha256']!=digest: result['issues'].append('content_changed')
                if 'contains' in record and record['contains'] not in excerpt: result['issues'].append('snippet_missing')
        result['valid']=not result['issues']; results.append(result)
    return {'valid':all(r['valid'] for r in results),'checked':len(results),'evidence':results}


def main():
    """Read one UTF-8 JSON document and emit one machine-readable report."""
    import argparse
    import json
    import sys
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="JSON input path, or - for stdin")
    args = parser.parse_args()
    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("input must be a JSON object")
        result = analyze(data)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        print(f"Invalid input: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
