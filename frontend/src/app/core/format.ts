/** Helpers para formatear los strings/Decimals que llegan del backend. */

export function toNumber(v: string | number | null | undefined): number {
  if (v == null) return 0;
  return typeof v === 'number' ? v : Number(v);
}

export function fmtPct(v: string | number | null, digits = 1): string {
  const n = toNumber(v) * 100;
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

export function fmtAbsPct(v: string | number | null, digits = 1): string {
  const n = toNumber(v) * 100;
  return `${n.toFixed(digits)}%`;
}

export function fmtOdds(v: string | number | null, digits = 2): string {
  return toNumber(v).toFixed(digits);
}

export function fmtEur(v: string | number | null, digits = 2): string {
  return `${toNumber(v).toFixed(digits)} €`;
}

export function fmtDate(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function fmtDateShort(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('es-ES', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function evColorClass(ev: string | number | null): string {
  const n = toNumber(ev);
  if (n >= 0.1) return 'text-emerald-400';
  if (n >= 0.05) return 'text-emerald-300';
  if (n >= 0.02) return 'text-amber-300';
  if (n > 0) return 'text-slate-300';
  return 'text-rose-400';
}

export function outcomeLabel(market: string, name: string, point: string | null): string {
  if (market === 'totals') return `${name} ${point ?? ''}`.trim();
  return name;
}

export function confidenceClass(confidence: string | null | undefined): string {
  switch (confidence) {
    case 'high':
      return 'bg-emerald-500/15 text-emerald-300 border-emerald-400/30';
    case 'medium':
      return 'bg-amber-500/15 text-amber-300 border-amber-400/30';
    case 'low':
      return 'bg-rose-500/15 text-rose-300 border-rose-400/30';
    default:
      return 'bg-slate-700/30 text-slate-400 border-slate-600/40';
  }
}

export function confidenceLabel(confidence: string | null | undefined): string {
  switch (confidence) {
    case 'high':
      return 'Alta';
    case 'medium':
      return 'Media';
    case 'low':
      return 'Baja';
    default:
      return 'Sin modelo';
  }
}
