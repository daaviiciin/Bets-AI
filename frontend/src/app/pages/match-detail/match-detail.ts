import {
  ChangeDetectionStrategy,
  Component,
  Injector,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { Title } from '@angular/platform-browser';
import { RouterLink } from '@angular/router';
import { finalize } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { BetslipService, SlipLeg, legKey } from '../../core/betslip.service';
import { competitionName } from '../../core/competitions';
import {
  fmtAgo,
  fmtCuota,
  fmtEv,
  fmtKickoff,
  fmtLinea,
  fmtNum,
  fmtProb,
  fmtTime,
  toNumber,
} from '../../core/format';
import {
  MARKET_GROUPS,
  MAX_RECOMMENDABLE_ODDS,
  MarketGroup,
  h2hSide,
  isRecommendable,
  isUnderProp,
  ladderSummary,
  marketGroup,
  marketLabel,
  marketOrder,
  playerLadderText,
  propPlayerName,
  selectionLabel,
} from '../../core/markets';
import type {
  GradedOddsLine,
  KeyPlayer,
  LineupPlayer,
  MatchPreview,
  TeamAverages,
  TeamPreview,
} from '../../core/models';
import { isLight, matchPaint } from '../../core/team-colors';
import { Icon } from '../../shared/icon';
import { TeamCrest } from '../../shared/team-crest';
import { TvCifra, TvEntra, flip } from '../../shared/tv-motion';
import { Pitch } from './pitch';
import { PlayerPhoto } from './player-photo';
import { CompareRow, StatCompare } from './stat-compare';

type Tab = 'previa' | 'alineaciones' | 'estadisticas' | 'cuotas' | 'jugadores';
type PlayerSort = 'minutes' | 'sot90' | 'goals';

interface OuRow {
  point: string;
  over: GradedOddsLine | null;
  under: GradedOddsLine | null;
}

interface MarketBlock {
  key: string;
  label: string;
  group: MarketGroup;
  lines: GradedOddsLine[];
  /** Totales (más/menos de N): tabla por línea en vez de rejilla. */
  ou: OuRow[] | null;
}

/** Un mercado tal como se pinta en Cuotas: filtrado y, si está plegado, resumido. */
interface MarketView extends MarketBlock {
  /** Líneas del mercado que pasan el filtro (lo que enseña "Ver las N líneas"). */
  total: number;
  /** Tiene más líneas que el resumen: lleva botón para desplegar. */
  foldable: boolean;
  open: boolean;
}

/** Líneas que se ven de un mercado en rejilla (jugadores, resultado y goles…) al plegarlo. */
const GRID_SUMMARY = 6;
/** Jugadores por equipo antes de "Ver todos": un once. */
const SQUAD_SUMMARY = 11;
/** Cuotas por jugador en la tabla de Jugadores (los porteros enseñan todas sus paradas). */
const PROPS_PER_PLAYER = 4;
const SOT_MARKETS = ['player_sot_alt', 'player_sot'];
/** Orden de las líneas del once en texto: portero, defensas, medios y delanteros. */
const LINE_ORDER = ['G', 'D', 'M', 'F'];

const TABS: { id: Tab; label: string }[] = [
  { id: 'previa', label: 'Previa' },
  { id: 'alineaciones', label: 'Alineaciones' },
  { id: 'estadisticas', label: 'Estadísticas' },
  { id: 'cuotas', label: 'Cuotas' },
  { id: 'jugadores', label: 'Jugadores' },
];

function norm(name: string | null | undefined): string {
  return (name ?? '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .sort()
    .join(' ');
}

function isOu(lines: GradedOddsLine[]): boolean {
  return lines.every(
    (l) => l.outcome_point != null && /^(over|under)$/i.test(l.outcome_name.trim()),
  );
}

function ouRows(lines: GradedOddsLine[]): OuRow[] {
  const rows = new Map<number, OuRow>();
  for (const l of lines) {
    const p = toNumber(l.outcome_point);
    const row = rows.get(p) ?? { point: l.outcome_point!, over: null, under: null };
    if (/^over$/i.test(l.outcome_name.trim())) row.over = l;
    else row.under = l;
    rows.set(p, row);
  }
  return [...rows].sort(([a], [b]) => a - b).map(([, r]) => r);
}

/**
 * Página de partido: marcador con los colores de los dos clubes y cinco pestañas
 * (previa, alineaciones, estadísticas, cuotas y jugadores). Cualquier cuota se lleva al
 * boletín con un clic.
 *
 * Primero el resumen y el detalle a demanda: la Previa resume y enlaza a las pestañas; en
 * Cuotas cada mercado largo enseña su línea principal (y lo que tiene valor o está en el
 * boletín) con "Ver las N líneas"; en Jugadores, un once por equipo con "Ver los N".
 */
@Component({
  selector: 'app-match-detail',
  imports: [RouterLink, Icon, TeamCrest, Pitch, PlayerPhoto, StatCompare, TvEntra, TvCifra],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './match-detail.html',
  styleUrl: './match-detail.scss',
})
export class MatchDetail {
  /** ``/partido/:id`` (``withComponentInputBinding``). */
  readonly id = input.required<string>();

  private readonly api = inject(ApiService);
  private readonly title = inject(Title);
  private readonly injector = inject(Injector);
  protected readonly slip = inject(BetslipService);

  protected readonly preview = signal<MatchPreview | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly lines = signal<GradedOddsLine[]>([]);
  protected readonly linesLoading = signal(true);
  protected readonly linesError = signal(false);

  protected readonly tab = signal<Tab>('previa');
  protected readonly group = signal<MarketGroup | 'todos'>('todos');
  /** Qué líneas se ven en Cuotas: todas, solo con P del modelo o solo con EV positivo. */
  protected readonly lineFilter = signal<'todas' | 'modelo' | 'valor'>('todas');
  protected readonly statsScope = signal<'venue' | 'overall'>('venue');
  protected readonly playerSort = signal<PlayerSort>('minutes');
  /** Mercados desplegados en Cuotas (por defecto cada uno enseña su resumen). */
  protected readonly openMarkets = signal<ReadonlySet<string>>(new Set());
  /** Equipos con la plantilla entera a la vista en Jugadores. */
  protected readonly fullSquads = signal<ReadonlySet<number>>(new Set());
  protected readonly maxRecommendable = MAX_RECOMMENDABLE_ODDS;

  protected readonly fmtCuota = fmtCuota;
  protected readonly fmtEv = fmtEv;
  protected readonly fmtProb = fmtProb;
  protected readonly fmtLinea = fmtLinea;
  protected readonly fmtNum = fmtNum;
  protected readonly fmtTime = fmtTime;
  protected readonly fmtKickoff = fmtKickoff;
  protected readonly fmtAgo = fmtAgo;
  protected readonly toNumber = toNumber;
  protected readonly fmtEntero = (v: number | null) => fmtNum(v, 0);
  protected readonly marketGroups = MARKET_GROUPS;

  protected readonly paint = computed(() => {
    const p = this.preview();
    return p ? matchPaint(p.home, p.away) : null;
  });

  /**
   * Si un club tiene panel claro (blanco, amarillo), el centro del marcador pasa a oscuro
   * para que se lean tres piezas y no dos.
   */
  protected readonly darkCenter = computed(() => {
    const c = this.paint();
    return !!c && (isLight(c.home.bg) || isLight(c.away.bg));
  });

  protected readonly competition = computed(() => {
    const p = this.preview();
    return p ? competitionName(p.league_sport_key, p.league_name) : '';
  });

  protected readonly played = computed(() => {
    const s = this.preview()?.status;
    return s === 'finished' || s === 'live';
  });

  protected readonly tabs = computed(() =>
    TABS.map((t) => ({
      ...t,
      n:
        t.id === 'cuotas'
          ? this.lines().length
          : t.id === 'jugadores'
            ? (this.preview()?.home.players.length ?? 0) + (this.preview()?.away.players.length ?? 0)
            : null,
    })),
  );

  protected readonly h2h = computed(() => {
    const p = this.preview();
    const all = this.lines().filter((l) => l.market_key === 'h2h');
    if (!p) return [];
    const side = (s: 'home' | 'away') =>
      all.find((l) => h2hSide(l.outcome_name, p.home.name, p.away.name) === s) ?? null;
    return [
      { label: '1', caption: p.home.name, line: side('home') },
      { label: 'X', caption: 'Empate', line: all.find((l) => l.outcome_name === 'Draw') ?? null },
      { label: '2', caption: p.away.name, line: side('away') },
    ];
  });

  protected readonly markets = computed<MarketBlock[]>(() => {
    const byKey = new Map<string, GradedOddsLine[]>();
    for (const l of this.lines()) {
      if (l.market_key === 'h2h') continue;
      byKey.set(l.market_key, [...(byKey.get(l.market_key) ?? []), l]);
    }
    return [...byKey]
      .sort(([a], [b]) => marketOrder(a) - marketOrder(b) || a.localeCompare(b))
      .map(([key, lines]) => ({
        key,
        label: marketLabel(key),
        group: marketGroup(key),
        lines: [...lines].sort((a, b) => toNumber(a.offered_odds) - toNumber(b.offered_odds)),
        ou: isOu(lines) ? ouRows(lines) : null,
      }));
  });

  protected readonly groupCounts = computed(() => {
    const counts = new Map<MarketGroup, number>();
    for (const m of this.markets()) counts.set(m.group, (counts.get(m.group) ?? 0) + m.lines.length);
    return counts;
  });

  protected readonly valueCount = computed(() => this.lines().filter((l) => this.hasValue(l)).length);

  /** Hay cuotas por encima del límite: la leyenda explica que son solo informativas. */
  protected readonly hasHighOdds = computed(() => this.lines().some((l) => !isRecommendable(l)));

  /**
   * Mercados de Cuotas. Sin filtro de líneas, cada mercado largo se pliega a un resumen: en
   * los totales, la línea principal y sus vecinas; en las rejillas, las seis cuotas más
   * bajas. Lo que tiene valor o ya está en el boletín no se esconde nunca.
   */
  protected readonly visibleMarkets = computed<MarketView[]>(() => {
    const g = this.group();
    const f = this.lineFilter();
    const open = this.openMarkets();
    const pass = (l: GradedOddsLine | null): l is GradedOddsLine =>
      !!l && (f === 'todas' || (f === 'modelo' ? l.model_probability != null : this.hasValue(l)));
    const important = (l: GradedOddsLine | null) => this.hasValue(l) || this.picked(l);
    const out: MarketView[] = [];
    for (const m of this.markets()) {
      if (g !== 'todos' && m.group !== g) continue;
      const lines = m.lines.filter(pass);
      if (!lines.length) continue;
      const ou =
        m.ou
          ?.map((r) => ({ ...r, over: pass(r.over) ? r.over : null, under: pass(r.under) ? r.under : null }))
          .filter((r) => r.over || r.under) ?? null;
      // Con un filtro activo ya se ve poco: no se pliega nada.
      let summaryOu = ou;
      let summaryLines = lines;
      if (f === 'todas') {
        if (ou) {
          const idx = ladderSummary(ou, (r) => important(r.over) || important(r.under));
          summaryOu = ou.filter((_, i) => idx.has(i));
        } else {
          summaryLines = lines.filter((l, i) => i < GRID_SUMMARY || important(l));
        }
      }
      const shown = summaryOu
        ? summaryOu.reduce((n, r) => n + (r.over ? 1 : 0) + (r.under ? 1 : 0), 0)
        : summaryLines.length;
      const foldable = shown < lines.length;
      const isOpen = foldable && open.has(m.key);
      out.push({
        ...m,
        total: lines.length,
        lines: isOpen ? lines : summaryLines,
        ou: isOpen ? ou : summaryOu,
        foldable,
        open: isOpen,
      });
    }
    return out;
  });

  /** Mercados clave de la previa: goles 2,5 y "marcan los dos". */
  protected readonly keyLines = computed(() => {
    const ls = this.lines();
    const pick = (mk: string, name: string, point: number | null) =>
      ls.find(
        (l) =>
          l.market_key === mk &&
          l.outcome_name.toLowerCase() === name &&
          (point == null || toNumber(l.outcome_point) === point),
      ) ?? null;
    return [
      pick('totals', 'over', 2.5),
      pick('totals', 'under', 2.5),
      pick('btts', 'yes', null),
      pick('btts', 'no', null),
    ].filter((l): l is GradedOddsLine => l != null);
  });

  private readonly propsByPlayer = computed(() => {
    const map = new Map<string, GradedOddsLine[]>();
    for (const l of this.lines()) {
      if (marketGroup(l.market_key) !== 'jugadores') continue;
      const k = norm(propPlayerName(l));
      map.set(k, [...(map.get(k) ?? []), l]);
    }
    for (const ls of map.values()) ls.sort((a, b) => marketOrder(a.market_key) - marketOrder(b.market_key));
    return map;
  });

  constructor() {
    effect(() => {
      const id = Number(this.id());
      untracked(() => this.load(id));
    });
  }

  protected load(id = Number(this.id())): void {
    this.loading.set(true);
    this.error.set(null);
    this.lines.set([]);
    this.tab.set('previa');
    this.openMarkets.set(new Set());
    this.fullSquads.set(new Set());
    this.api
      .getMatchPreview(id)
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({
        next: (p) => {
          this.preview.set(p);
          this.title.setTitle(`${p.home.name} – ${p.away.name} · Bets AI`);
          this.loadLines(p);
        },
        error: (err) =>
          this.error.set(
            err?.status === 404
              ? 'Este partido no existe o ya no está en la base de datos.'
              : err?.status === 0
                ? 'La API no responde. ¿Está arrancada en el puerto 8000?'
                : 'No se pudo cargar el partido.',
          ),
      });
  }

  private loadLines(p: MatchPreview): void {
    this.linesLoading.set(true);
    this.linesError.set(false);
    const hours = (new Date(p.commence_time).getTime() - Date.now()) / 3_600_000;
    if (hours < 0) {
      this.linesLoading.set(false);
      return;
    }
    this.api
      .listGradedOdds({
        matchId: p.match_id,
        horizonHours: Math.min(336, Math.max(1, Math.ceil(hours) + 2)),
        maxLines: 15000,
      })
      .pipe(finalize(() => this.linesLoading.set(false)))
      .subscribe({
        next: (ls) => this.lines.set(ls),
        error: () => this.linesError.set(true),
      });
  }

  // ------------------------------------------------------------ pestañas

  protected selectTab(t: Tab): void {
    this.tab.set(t);
  }

  /** Ordenar jugadores: cada fila se desliza a su nuevo puesto (FLIP) en vez de saltar. */
  protected sortPlayers(s: PlayerSort, panel: HTMLElement): void {
    if (s === this.playerSort()) return;
    flip(panel, 'tr[data-flip]', () => this.playerSort.set(s), this.injector);
  }

  protected onTabKey(event: KeyboardEvent): void {
    const order = TABS.map((t) => t.id);
    const i = order.indexOf(this.tab());
    const next =
      event.key === 'ArrowRight' ? order[(i + 1) % order.length]
      : event.key === 'ArrowLeft' ? order[(i - 1 + order.length) % order.length]
      : event.key === 'Home' ? order[0]
      : event.key === 'End' ? order[order.length - 1]
      : null;
    if (!next) return;
    event.preventDefault();
    this.tab.set(next);
    document.getElementById(`pestana-${next}`)?.focus();
  }

  // ------------------------------------------------------------ boletín

  protected keyOf(l: GradedOddsLine): string {
    return legKey(l);
  }

  protected picked(l: GradedOddsLine | null): boolean {
    return !!l && this.slip.has(legKey(l));
  }

  protected label(l: GradedOddsLine): string {
    const p = this.preview();
    return selectionLabel(l, p?.home.name ?? l.home_team, p?.away.name ?? l.away_team);
  }

  protected pick(l: GradedOddsLine | null, el: HTMLElement): void {
    if (!l) return;
    const leg: SlipLeg = {
      key: legKey(l),
      matchId: l.match_id,
      home: l.home_team,
      away: l.away_team,
      league: this.competition() || l.league,
      kickoff: l.commence_time,
      marketKey: l.market_key,
      marketLabel: marketLabel(l.market_key),
      selection: this.label(l),
      outcomeName: l.outcome_name,
      outcomePoint: l.outcome_point,
      playerName: l.player_name,
      odds: l.offered_odds,
      modelProbability: l.model_probability,
      modelEv: l.model_ev,
    };
    this.slip.toggle(leg, el);
  }

  /**
   * EV positivo tal como se enseña (redondeado al %): un +0,3 % que se lee "0 %" no cuenta.
   * Una cuota alta (``recommendable: false`` o, sin ese campo, por encima de 4,00) nunca es
   * valor aunque el modelo le dé EV positivo: es solo informativa.
   */
  protected hasValue(l: GradedOddsLine | null): boolean {
    return (
      !!l &&
      isRecommendable(l) &&
      l.model_ev != null &&
      Math.round(toNumber(l.model_ev) * 100) > 0
    );
  }

  /** Cuota alta: se ve y se lleva al boletín, pero el modelo no la juzga. */
  protected highOdds(l: GradedOddsLine | null): boolean {
    return !!l && !isRecommendable(l);
  }

  /**
   * Todo lo de una línea en una frase (``title`` y nombre accesible). En la rejilla de Cuotas
   * solo se ve la P del modelo, y el EV si hay valor: el resto vive aquí.
   */
  protected lineInfo(l: GradedOddsLine): string {
    const parts = [`${this.label(l)}, cuota ${fmtCuota(l.offered_odds)}`];
    if (l.model_probability != null) {
      parts.push(`modelo ${fmtProb(l.model_probability)}`);
      if (l.model_ev != null) parts.push(`EV ${fmtEv(l.model_ev)}`);
    } else {
      parts.push('sin modelo');
    }
    if (this.highOdds(l)) parts.push('cuota alta, solo informativa');
    return parts.join(', ');
  }

  protected toggleMarket(key: string): void {
    this.openMarkets.update((s) => {
      const next = new Set(s);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }

  /** Marca de valor (EV > 0) mientras la línea no esté ya en el boletín. */
  protected valueMark(l: GradedOddsLine | null): boolean {
    return this.hasValue(l) && !this.picked(l);
  }

  /**
   * Panel de club con el degradado de un rótulo de TV: más oscuro hacia el borde exterior.
   * Un club claro apenas se oscurece: con un tercio de negro, el blanco se vuelve gris metálico.
   */
  protected panelBg(color: string, side: 'home' | 'away'): string {
    const dir = side === 'home' ? '90deg' : '270deg';
    const keep = isLight(color) ? 94 : 68;
    return `linear-gradient(${dir}, color-mix(in oklab, ${color} ${keep}%, #000) 0%, ${color} 70%)`;
  }

  // ------------------------------------------------------------ estadísticas

  /**
   * Medias de la pestaña Estadísticas: las siete del partido y, colgando de goles, córners y
   * tarjetas, su desglose por mitades (si hay datos de mitades).
   */
  protected readonly statsRows = computed<CompareRow[]>(() => {
    const p = this.preview();
    if (!p) return [];
    const venue = this.statsScope() === 'venue';
    const h = venue ? p.home.venue : p.home.overall;
    const a = venue ? p.away.venue : p.away.overall;
    const halves = (h1: keyof TeamAverages, h2: keyof TeamAverages): CompareRow[] =>
      [h[h1], h[h2], a[h1], a[h2]].every((v) => v == null)
        ? []
        : [
            { label: '1ª parte', home: h[h1], away: a[h1], sub: true },
            { label: '2ª parte', home: h[h2], away: a[h2], sub: true },
          ];
    return [
      { label: 'Goles a favor', home: h.goals_for, away: a.goals_for },
      ...halves('goals_1h', 'goals_2h'),
      { label: 'Goles en contra', home: h.goals_against, away: a.goals_against },
      { label: 'xG', home: h.xg, away: a.xg, digits: 2 },
      { label: 'Tiros', home: h.shots, away: a.shots },
      { label: 'Tiros a puerta', home: h.shots_on_target, away: a.shots_on_target },
      { label: 'Córners', home: h.corners, away: a.corners },
      ...halves('corners_1h', 'corners_2h'),
      { label: 'Tarjetas amarillas', home: h.yellow_cards, away: a.yellow_cards },
      ...halves('cards_1h', 'cards_2h'),
    ];
  });

  /** Resumen de la Previa: cinco medias en su campo; el resto, en Estadísticas. */
  protected readonly previewRows = computed<CompareRow[]>(() => {
    const p = this.preview();
    if (!p) return [];
    const h = p.home.venue;
    const a = p.away.venue;
    return [
      { label: 'Goles a favor', home: h.goals_for, away: a.goals_for },
      { label: 'Goles en contra', home: h.goals_against, away: a.goals_against },
      { label: 'Tiros a puerta', home: h.shots_on_target, away: a.shots_on_target },
      { label: 'Córners', home: h.corners, away: a.corners },
      { label: 'Tarjetas amarillas', home: h.yellow_cards, away: a.yellow_cards },
    ];
  });

  /** Leyenda de cada lado de la comparativa: club y cuántos partidos cuentan. */
  protected sampleLabel(t: TeamPreview, side: 'home' | 'away', scope = this.statsScope()): string {
    if (scope === 'venue') return `${t.name} · ${t.venue.matches} ${side === 'home' ? 'en casa' : 'fuera'}`;
    const n = t.overall.matches;
    return `${t.name} · ${n} ${n === 1 ? 'partido' : 'partidos'}`;
  }

  /** "12/10". */
  protected shortDate(iso: string): string {
    return new Date(iso).toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' });
  }

  protected resultText(r: string): string {
    return r === 'W' ? 'G' : r === 'D' ? 'E' : 'P';
  }

  protected resultName(r: string): string {
    return r === 'W' ? 'ganó' : r === 'D' ? 'empató' : 'perdió';
  }

  // ------------------------------------------------------------ jugadores

  protected sortedPlayers(players: KeyPlayer[]): KeyPlayer[] {
    const s = this.playerSort();
    const val = (p: KeyPlayer) =>
      s === 'sot90' ? (p.shots_on_target_per90 ?? -1) : s === 'goals' ? p.goals : p.minutes;
    return [...players].sort((a, b) => val(b) - val(a) || b.minutes - a.minutes);
  }

  /**
   * Jugadores de un equipo en la tabla: los once primeros según el orden elegido (más los
   * que tengan una cuota en el boletín), o la plantilla entera si se ha desplegado.
   */
  protected squad(t: TeamPreview): { players: KeyPlayer[]; total: number; foldable: boolean; open: boolean } {
    const all = this.sortedPlayers(t.players);
    const summary = all.filter(
      (p, i) => i < SQUAD_SUMMARY || this.propsOf(p.name).some((l) => this.picked(l)),
    );
    const foldable = summary.length < all.length;
    const open = foldable && this.fullSquads().has(t.team_id);
    return { players: open || !foldable ? all : summary, total: all.length, foldable, open };
  }

  protected toggleSquad(teamId: number): void {
    this.fullSquads.update((s) => {
      const next = new Set(s);
      if (!next.delete(teamId)) next.add(teamId);
      return next;
    });
  }

  protected propsOf(name: string): GradedOddsLine[] {
    return this.propsByPlayer().get(norm(name)) ?? [];
  }

  /**
   * Cuotas de un jugador en la tabla, lo que más se mira primero: marca gol y 1+ tiros a
   * puerta; luego el resto por orden de mercado. Un portero enseña sus líneas de paradas
   * (más de 0,5, 1,5, 2,5…), de la más baja a la más alta.
   */
  protected featuredProps(pl: KeyPlayer): GradedOddsLine[] {
    const all = this.propsOf(pl.name);
    const saves = all
      .filter((l) => l.market_key === 'player_saves_alt' && !isUnderProp(l))
      .sort((a, b) => toNumber(a.outcome_point) - toNumber(b.outcome_point));
    if (saves.length) return saves;
    const goal = all.find((l) => l.market_key === 'player_anytime_scorer');
    const sot1 = all.find(
      (l) =>
        SOT_MARKETS.includes(l.market_key) && !isUnderProp(l) && toNumber(l.outcome_point) === 0.5,
    );
    const first = [goal, sot1].filter((l): l is GradedOddsLine => l != null);
    const rest = all.filter((l) => !first.includes(l) && !isUnderProp(l));
    return [...first, ...rest].slice(0, PROPS_PER_PLAYER);
  }

  /** La cuota destacada (gol, 1+ tiros a puerta, paradas) se pinta con más peso. */
  protected isKeyProp(l: GradedOddsLine): boolean {
    return (
      l.market_key === 'player_anytime_scorer' ||
      l.market_key === 'player_saves_alt' ||
      (SOT_MARKETS.includes(l.market_key) && toNumber(l.outcome_point) === 0.5)
    );
  }

  protected propShort(l: GradedOddsLine): string {
    const ladder = playerLadderText(l);
    if (ladder) return ladder;
    const shorts: Record<string, string> = {
      player_anytime_scorer: 'Marca gol',
      player_scorer_and_team_wins: 'Marca y gana',
      player_to_score_2plus: '2+ goles',
      player_first_scorer: '1.er gol',
      player_last_scorer: 'Último gol',
      player_card_yes_no: 'Tarjeta',
    };
    if (shorts[l.market_key]) return shorts[l.market_key];
    if (l.outcome_point != null) return `${marketLabel(l.market_key)} ${fmtLinea(l.outcome_point)}`;
    return marketLabel(l.market_key);
  }

  protected positionName(p: string | null): string {
    return p === 'G' ? 'POR' : p === 'D' ? 'DEF' : p === 'M' ? 'MED' : p === 'F' ? 'DEL' : '–';
  }

  /** El once en texto, como en la prensa: una fila por línea (POR, DEF, MED, DEL). */
  protected lineupLines(t: TeamPreview): { pos: string | null; players: LineupPlayer[] }[] {
    const lines = new Map<string | null, LineupPlayer[]>();
    for (const p of t.lineup.starters) {
      const k = p.position && LINE_ORDER.includes(p.position) ? p.position : null;
      lines.set(k, [...(lines.get(k) ?? []), p]);
    }
    const rank = (k: string | null) => (k == null ? LINE_ORDER.length : LINE_ORDER.indexOf(k));
    return [...lines]
      .sort(([a], [b]) => rank(a) - rank(b))
      .map(([pos, players]) => ({ pos, players }));
  }

  /** Bajas y dudas de un equipo, agrupadas: "De baja: A, B" · "En duda: C". */
  protected absenceGroups(t: TeamPreview): { missing: boolean; label: string; names: string }[] {
    const missing = t.absences.filter((a) => a.status === 'missing').map((a) => a.name);
    const doubtful = t.absences.filter((a) => a.status !== 'missing').map((a) => a.name);
    return [
      { missing: true, label: 'De baja', names: missing.join(', ') },
      { missing: false, label: 'En duda', names: doubtful.join(', ') },
    ].filter((g) => g.names);
  }

  protected lineupCaption(t: TeamPreview): string {
    const l = t.lineup;
    if (l.kind === 'official') return 'Alineación oficial';
    if (l.kind === 'played') return 'Once que jugó el partido';
    if (l.kind === 'probable') {
      const out = l.starters.filter((p) => p.status === 'missing').length;
      const base = `Once probable: titulares del último partido (${l.based_on})`;
      return out ? `${base}. ${out} de ellos ${out === 1 ? 'está' : 'están'} de baja` : base;
    }
    return 'Sin alineación: aún no hay partidos suyos con titulares en la base de datos';
  }
}
