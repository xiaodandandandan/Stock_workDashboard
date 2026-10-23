"""Export saved scans only; no network calls or real-time quotes."""
import argparse
import json
import re
import shutil
import sys
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
    sys.path.insert(0, str(ROOT))
    from core import engine, store
    chart_dir = destination / 'charts'
    chart_dir.mkdir(exist_ok=True)
    symbols = set()
    for item in snapshots:
        snapshot = json.loads((destination / item['file']).read_text(encoding='utf-8'))
        for row in snapshot['rows']:
            code = row['code']
            if re.fullmatch(r'\d{6}', str(code)):
                symbols.add(code)
    available = {}
    for code in sorted(symbols):
        symbol = ('sh' if code.startswith(('6', '5')) else 'sz' if code.startswith(('0', '1', '2', '3')) else 'bj') + code
        bars = store.load_kline('day', symbol, max_age=10**12)
        if not bars or len(bars) < 40:
            continue
        result = engine.analyze(bars, lookback=250)
        if not result:
            continue
        payload = {key: result[key] for key in ('date', 'chart', 'signals', 'bars')}
        payload['symbol'] = symbol
        (chart_dir / (code + '.json')).write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        available[code] = result['date']
    (chart_dir / 'index.json').write_text(json.dumps(available), encoding='utf-8')
    shutil.copyfile(ROOT / 'web/vendor/kline_canvas.js', destination / 'kline_canvas.js')
    shutil.copyfile(ROOT / 'deploy/pages-chart.js', destination / 'pages-chart.js')
    shutil.copyfile(ROOT / 'deploy/pages.html', destination / 'index.html')
    (destination / '.nojekyll').touch()
    print('Exported %d snapshots to %s' % (len(snapshots), destination))
    print('Exported %d cached daily charts (no network fetch)' % len(available))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT.parent / 'pages-site'))
    args = parser.parse_args()
    export(args.output)
