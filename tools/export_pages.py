"""Export saved scans only; no network calls or real-time quotes."""
import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export(destination, cache=None):
    cache = Path(cache or ROOT / 'cache/scan')
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT.is_relative_to(destination):
        raise ValueError('输出目录不能是项目根目录或其上级')
    latest = json.loads((cache / 'latest.json').read_text(encoding='utf-8'))
    snapshots = []
    seen = set()
    files = [cache / 'latest.json'] + sorted(
        [p for p in cache.glob('*.json') if re.fullmatch(r'\d{8}-\d{6}(?:-\d+)?', p.stem)], reverse=True)
    data = destination / 'data'
    data.mkdir(parents=True, exist_ok=True)
    # Only public market result fields are published, never logs or local paths.
    for path in files:
        obj = latest if path.name == 'latest.json' else json.loads(path.read_text(encoding='utf-8'))
        stamp = obj.get('generated', '')
        if not stamp or stamp in seen:
            continue
        seen.add(stamp)
        rows = []
        for row in obj.get('rows', []):
            rows.append({key: row.get(key) for key in
                         ('code', 'name', 'industry', 'close', 'amt', 'buy', 'sell')})
        name = '%02d.json' % len(snapshots)
        payload = {'generated': stamp, 'freq': obj.get('freq', 'day'),
                   'total_scanned': obj.get('total_scanned'), 'errors': obj.get('errors', 0),
                   'sell_rules_version': obj.get('sell_rules_version'), 'rows': rows}
        (data / name).write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        snapshots.append({'generated': stamp, 'file': 'data/' + name, 'count': len(rows)})
        if len(snapshots) >= 14:
            break
    if not snapshots:
        raise ValueError('没有可导出的扫描结果')
    (data / 'index.json').write_text(json.dumps(snapshots, ensure_ascii=False), encoding='utf-8')
    shutil.copyfile(ROOT / 'deploy/pages.html', destination / 'index.html')
    (destination / '.nojekyll').touch()
    print('Exported %d snapshots to %s' % (len(snapshots), destination))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT.parent / 'pages-site'))
    args = parser.parse_args()
    export(args.output)
