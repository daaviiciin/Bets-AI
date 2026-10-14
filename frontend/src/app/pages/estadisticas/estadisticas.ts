import { Component, computed, inject, signal } from '@angular/core';
import { finalize } from 'rxjs/operators';

import { ApiService } from '../../core/api.service';
import type { StatisticsOverview, TeamDetailStatistics, TeamStatistics } from '../../core/models';

const TARGET_COMPETITIONS = [
  { key: 'all', label: 'Todas las competiciones' },
  { key: 'soccer_epl', label: 'Premier League' },
  { key: 'soccer_spain_la_liga', label: 'La Liga' },
  { key: 'soccer_italy_serie_a', label: 'Serie A' },
  { key: 'soccer_germany_bundesliga', label: 'Bundesliga' },
  { key: 'soccer_france_ligue_one', label: 'Ligue 1' },
  { key: 'soccer_uefa_champs_league', label: 'Champions League' },
  { key: 'soccer_uefa_europa_league', label: 'Europa League' },
  { key: 'soccer_uefa_europa_conference_league', label: 'Conference League' },
] as const;

@Component({
  selector: 'app-estadisticas',
  templateUrl: './estadisticas.html',
})
export class Estadisticas {
  private readonly api = inject(ApiService);
  protected readonly competitions = TARGET_COMPETITIONS;
  protected readonly selectedCompetition = signal('all');
  protected readonly data = signal<StatisticsOverview | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly selectedTeam = signal<TeamDetailStatistics | null>(null);
  protected readonly teamLoading = signal(false);

  protected readonly standingTeams = computed(() => [...(this.data()?.teams ?? [])].sort((a, b) => {
    const pointsDiff = (b.points ?? -1) - (a.points ?? -1);
    if (pointsDiff !== 0) return pointsDiff;
    const goalDiffA = (a.goals_for ?? 0) - (a.goals_against ?? 0);
    const goalDiffB = (b.goals_for ?? 0) - (b.goals_against ?? 0);
    return goalDiffB - goalDiffA || (b.goals_for ?? 0) - (a.goals_for ?? 0);
  }));
  protected readonly totalMatchesWithStats = computed(() =>
    (this.data()?.competitions ?? []).reduce((total, competition) => total + competition.matches_with_stats, 0),
  );

  constructor() {
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.error.set(null);
    const sportKey = this.selectedCompetition() === 'all' ? undefined : this.selectedCompetition();
    this.api.getStatisticsOverview({ sportKey, limit: 50 })
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({
        next: (data) => this.data.set(data),
        error: (err) => this.error.set(err?.error?.detail ?? 'No se pudieron cargar las estadísticas.'),
      });
  }

  protected selectCompetition(event: Event): void {
    this.selectedCompetition.set((event.target as HTMLSelectElement).value);
    this.load();
  }

  protected openTeam(team: TeamStatistics): void {
    this.teamLoading.set(true);
    this.error.set(null);
    this.api.getTeamStatistics(team.team_id)
      .pipe(finalize(() => this.teamLoading.set(false)))
      .subscribe({
        next: (detail) => this.selectedTeam.set(detail),
        error: (err) => this.error.set(err?.error?.detail ?? 'No se pudo cargar el detalle del equipo.'),
      });
  }

  protected closeTeam(): void {
    this.selectedTeam.set(null);
  }

  protected resultClass(result: string): string {
    if (result === 'W') return 'text-emerald-300';
    if (result === 'L') return 'text-rose-300';
    return 'text-amber-200';
  }

  protected goalDifference(team: TeamStatistics): number | null {
    if (team.goals_for == null || team.goals_against == null) return null;
    return team.goals_for - team.goals_against;
  }

  protected initials(name: string): string {
    return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
  }
}
