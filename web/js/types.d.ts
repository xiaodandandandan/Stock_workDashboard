type Frequency = 'day' | 'm60' | 'm30' | 'm15' | 'm5';
type SymbolId = `sh${string}` | `sz${string}` | `bj${string}` | `hk${string}`;
type StrokePoint = [number, number, number];
interface RequestTicket { id: number; signal: AbortSignal }
interface StockRef { code: string; symbol?: SymbolId; name?: string; industry?: string;
  scan_at?: string; tracking?: PriceTracking; close?: number }
interface SignalReason { k: string; v: string; need?: string; ok: boolean | number }
interface Signal {
  type: string; date: string; stop: number | null; fresh: number;
  valid: 0 | 1; trend: -1 | 0 | 1; why: SignalReason[];
  first_i: number | null; entry: number | null; alive: number | null; ret: number | null;
}
interface HistoryMarker {
  i: number; side: 'b' | 's'; t: string; ty: string; n: string;
  d: string; alive: number; live: 0 | 1; entry: number;
  stop: number | null; ret: number; why: SignalReason[];
}
interface ChartPack {
  n: number; d0: string; o: number[]; h: number[]; l: number[]; c: number[]; v: number[];
  fx?: StrokePoint[]; bi?: StrokePoint[]; biLast?: number;
  zs?: [number, number, number, number][]; md?: number[]; ar?: [number, number][];
}
interface StockPayload {
  code: string; symbol: SymbolId; freq: Frequency; name: string; industry: string;
  close: number; amt: number; bars: number; date: string; unit: string;
  cached: boolean; in_scan: boolean; elapsed_ms: number; data_version: string;
  structure: { ZD: number | null; ZG: number | null; pos: string; biCount: number; fxCount: number; zsCount: number };
  signals: { buy: Signal[]; sell: Signal[] };
  chart: { pack: ChartPack; dates: string[]; opt: Record<string, number>; hist: HistoryMarker[] };
  history_total?: number; report?: string;
}
interface ScanRow extends StockRef {
  symbol: SymbolId; close: number; amt: number; date: string;
  buy: Signal[]; sell: Signal[];
}
interface ScanPayload {
  rows: ScanRow[]; generated: string; hit: number; total_scanned: number; elapsed: number;
  lookback: number; count: number; freq: Frequency; errors: number; complete: boolean;
  industry_status?: IndustryStatus;
  scan_id?: string;
}
interface ScanSnapshot { id: string; generated: string; date: string; freq: Frequency; hit: number; total_scanned: number }
interface PriceTracking { price: number | null; date: string | null; change: number | null; change_pct: number | null;
  status: 'ok' | 'unavailable' | 'invalid_baseline'; buy_stop_breached: boolean | null; breached_types: string[] }
interface TrackingPayload { quotes: Record<string, PriceTracking>; as_of: string; errors: Record<string, string>; source: string }
interface IndustryStatus {
  status: 'ok' | 'partial' | 'failed'; matched: number; missing: number; total: number; error: string;
}
interface ScanOptions { limit?: number; workers?: number; freq?: Frequency; count?: number; lookback?: number }
interface ScanStatus {
  running: boolean; stage: string; done: number; total: number; msg: string; error: string;
  result: { hit: number; elapsed: number; errors: number; complete: boolean; industry_status?: IndustryStatus } | null;
}
