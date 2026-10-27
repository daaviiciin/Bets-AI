import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { finalize } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { BetslipService, SlipLeg, legKey } from '../../core/betslip.service';
import { competitionName, competitionOrder } from '../../core/competitions';
import { dayKey, fmtCuota, fmtDayName, fmtNum, fmtTime } from '../../core/format';
import { marketLabel, selectionLabel } from '../../core/markets';
import type { Match, OddsRefreshSummary } from '../../core/models';
import { type TeamPaint, isLight, matchPaint } from '../../core/team-colors';
import { Icon } from '../../shared/icon';
import { TeamCrest } from '../../shared/team-crest';
import { TvCifra, TvEntra } from '../../shared/tv-motion';
import { PicksDelDia } from './picks-del-dia';
import { Portada } from './portada';
import { ReglaJornada } from './regla-jornada';

/** Días de partidos que enseña la portada (lo mismo que cubren las cuotas de Winamax). */
const DAYS_AHEAD = 5;

type Side = 'home' | 'draw' | 'away';

interface CompetitionGroup {
  key: string;
  name: string;
  matches: Match[];
}

interface DayGroup {
  key: string;
  label: string;
  competitions: CompetitionGroup[];
}

/**
 * Portada: los picks del día del modelo y los partidos de los próximos 5 días, por día y
 * competición, con su 1X2. Pulsar un partido abre su página; pulsar una cuota la lleva al
 * boletín.
 */
@Component({
  selector: 'app-dashboard',
  imports: [RouterLink, Icon, TeamCrest, TvEntra, TvCifra, ReglaJornada, Portada, PicksDelDia],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './dashboard.html',
})
export class Dashboard {
  private readonly api = inject(ApiService);
  protected readonly slip = inject(BetslipService);

  protected readonly matches = signal<Match[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  /** Día visible: se abre en el primero con partidos (como Flashscore), "todos" a un clic. */
  protected readonly day = signal<string>('todos');
  protected readonly competition = signal<string>('todas');

  protected readonly refreshingOdds = signal(false);
  protected readonly oddsStatus = signal<{ ok: boolean; text: string } | null>(null);
  /**
   * Primera pintada de la lista: la entrada se escalona con calma detrás de la cartela. Luego
   * (filtros, desplazamiento) las filas entran antes y más seguidas.
   */
  protected readonly firstLoad = signal(true);

  protected readonly fmtTime = fmtTime;
  protected readonly fmtCuota = fmtCuota;
  protected readonly fmtEntero = (v: number | null) => fmtNum(v, 0);
  protected readonly sides: { side: Side; label: string }[] = [
    { side: 'home', label: '1' },
    { side: 'draw', label: 'X' },
    { side: 'away', label: '2' },
  ];

  protected readonly days = computed(() => {
    const counts = new Map<string, { label: string; n: number }>();
    for (const m of this.byCompetition()) {
      const k = dayKey(m.commence_time);
      const cur = counts.get(k) ?? { label: fmtDayName(m.commence_time), n: 0 };
      counts.set(k, { ...cur, n: cur.n + 1 });
    }
    return [...counts].sort(([a], [b]) => a.localeCompare(b)).map(([key, v]) => ({ key, ...v }));
  });

  protected readonly competitions = computed(() => {
    const counts = new Map<string, { name: string; n: number }>();
    for (const m of this.matches()) {
      const key = m.league.sport_key;
      const cur = counts.get(key) ?? { name: competitionName(key, m.league.name), n: 0 };
      counts.set(key, { ...cur, n: cur.n + 1 });
    }
    return [...counts]
      .sort(([a], [b]) => competitionOrder(a) - competitionOrder(b))
      .map(([key, v]) => ({ key, ...v }));
  });

  protected readonly byCompetition = computed(() => {
    const c = this.competition();
    return c === 'todas' ? this.matches() : this.matches().filter((m) => m.league.sport_key === c);
  });

  protected readonly groups = computed<DayGroup[]>(() => {
    const d = this.day();
    const visible = this.byCompetition().filter(
      (m) => d === 'todos' || dayKey(m.commence_time) === d,
    );
    const days = new Map<string, Map<string, CompetitionGroup>>();
    for (const m of visible) {
      const dk = dayKey(m.commence_time);
      const comps = days.get(dk) ?? new Map<string, CompetitionGroup>();
      const key = m.league.sport_key;
      const group = comps.get(key) ?? {
        key,
        name: competitionName(key, m.league.name),
        matches: [],
      };
      group.matches.push(m);
      comps.set(key, group);
      days.set(dk, comps);
    }
    return [...days]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, comps]) => ({
        key,
        label: fmtDayName(comps.values().next().value!.matches[0].commence_time),
        competitions: [...comps.values()].sort(
          (a, b) => competitionOrder(a.key) - competitionOrder(b.key),
        ),
      }));
  });

  protected readonly visibleCount = computed(() =>
    this.groups().reduce((n, d) => n + d.competitions.reduce((k, c) => k + c.matches.length, 0), 0),
  );

  private firstLoadTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.firstLoadTimer));
    this.load();
  }

  protected load(silent = false): void {
    if (!silent) this.loading.set(true);
    this.error.set(null);
    this.api
      .listMatches({ scope: 'target', daysAhead: DAYS_AHEAD, limit: 500, withOdds: true })
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({
        next: (matches) => {
          this.matches.set(matches);
          if (!silent && matches.length) {
            this.day.set(dayKey(matches[0].commence_time));
          }
          // La primera entrada de la lista dura < 1 s; a partir de ahí, re-escalonado rápido.
          if (this.firstLoad() && this.firstLoadTimer === undefined) {
            this.firstLoadTimer = setTimeout(() => this.firstLoad.set(false), 1000);
          }
        },
        error: (err) => this.error.set(this.formatError(err)),
      });
  }

  protected setDay(key: string): void {
    this.day.set(key);
  }

  protected setCompetition(key: string): void {
    this.competition.set(this.competition() === key ? 'todas' : key);
    // Si esa competición no juega el día elegido, se ven todos sus días.
    if (!this.days().some((d) => d.key === this.day())) this.day.set('todos');
  }

  protected resetFilters(): void {
    this.day.set('todos');
    this.competition.set('todas');
  }

  protected price(m: Match, side: Side): string | null {
    return m.odds_1x2?.[side] ?? null;
  }

  /** Selección tal como la escribe Winamax: así coincide con la de la página de partido. */
  protected outcomeName(m: Match, side: Side): string {
    if (side === 'draw') return 'Draw';
    return side === 'home'
      ? (m.odds_1x2?.home_name ?? m.home_team.name)
      : (m.odds_1x2?.away_name ?? m.away_team.name);
  }

  protected teamName(m: Match, side: Side): string {
    return side === 'home' ? m.home_team.name : m.away_team.name;
  }

  protected keyFor(m: Match, side: Side): string {
    return legKey({
      match_id: m.id,
      market_key: 'h2h',
      outcome_name: this.outcomeName(m, side),
      outcome_point: null,
      player_name: null,
    });
  }

  protected pick(m: Match, side: Side, button: HTMLElement): void {
    const odds = this.price(m, side);
    if (!odds) return;
    const outcome = this.outcomeName(m, side);
    const leg: SlipLeg = {
      key: this.keyFor(m, side),
      matchId: m.id,
      home: m.home_team.name,
      away: m.away_team.name,
      league: competitionName(m.league.sport_key, m.league.name),
      kickoff: m.commence_time,
      marketKey: 'h2h',
      marketLabel: marketLabel('h2h'),
      selection: selectionLabel(
        { market_key: 'h2h', outcome_name: outcome, outcome_point: null, player_name: null },
        m.home_team.name,
        m.away_team.name,
      ),
      outcomeName: outcome,
      outcomePoint: null,
      playerName: null,
      odds,
      modelProbability: null,
      modelEv: null,
    };
    this.slip.toggle(leg, button);
  }

  protected paint(m: Match) {
    return matchPaint(m.home_team, m.away_team);
  }

  /** Placa de la hora oscura si una de las franjas es clara: si no, se fundirían. */
  protected darkPlate(p: { home: TeamPaint; away: TeamPaint }): boolean {
    return isLight(p.home.accent) || isLight(p.away.accent);
  }

  protected refreshOdds(): void {
    if (this.refreshingOdds()) return;
    this.refreshingOdds.set(true);
    this.oddsStatus.set(null);
    this.api
      .refreshOdds()
      .pipe(finalize(() => this.refreshingOdds.set(false)))
      .subscribe({
        next: (res) => {
          this.oddsStatus.set({ ok: res.ok, text: this.oddsStatusText(res) });
          this.load(true);
        },
        error: (err) =>
          this.oddsStatus.set({
            ok: false,
            text: `No se pudieron actualizar las cuotas: ${this.formatError(err)}`,
          }),
      });
  }

  private oddsStatusText(res: OddsRefreshSummary): string {
    const w = res.winamax;
    const fresh = w.matches_skipped_fresh ?? 0;
    if (w.matches_matched === 0) {
      return fresh
        ? `${fmtTime(res.finished_at)} · Las cuotas ya estaban al día (${fresh} partidos)`
        : `${fmtTime(res.finished_at)} · Winamax no tiene cuotas para los próximos ${DAYS_AHEAD} días`;
    }
    const failed = w.matches_failed ? ` · ${w.matches_failed} con error` : '';
    const skipped = fresh ? ` · ${fresh} ya al día` : '';
    return `Cuotas actualizadas a las ${fmtTime(res.finished_at)} · ${w.matches_matched} partidos${skipped}${failed}`;
  }

  private formatError(err: unknown): string {
    if (
      typeof err === 'object' &&
      err &&
      'status' in err &&
      (err as { status: number }).status === 0
    ) {
      return 'la API no responde (¿está arrancada en el puerto 8000?)';
    }
    if (err instanceof Error) return err.message;
    if (typeof err === 'object' && err && 'message' in err) {
      return String((err as { message: unknown }).message);
    }
    return 'no se pudieron cargar los datos';
  }
}
