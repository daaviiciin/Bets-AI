import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  AuthTokenResponse,
  AuthUserBrief,
  GradedOddsLine,
  Match,
  MatchPreview,
  OddsRefreshSummary,
  FeaturedDailyPickRow,
  FeaturedPicksMetrics,
  FeaturedToday,
  UserParlayMetrics,
  SavedParlay,
  SavedParlayLegPersist,
  ResultadoManual,
} from './models';

/** Cliente HTTP centralizado para todos los endpoints de la API Bets AI. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = environment.apiBaseUrl;

  listMatches(opts?: {
    league?: string;
    scope?: 'target';
    daysAhead?: number;
    daysBack?: number;
    limit?: number;
    /** Añade el último 1X2 de Winamax de cada partido (portada). */
    withOdds?: boolean;
  }): Observable<Match[]> {
    let params = new HttpParams();
    if (opts?.league) params = params.set('league', opts.league);
    if (opts?.scope) params = params.set('scope', opts.scope);
    if (opts?.daysAhead != null) params = params.set('days_ahead', String(opts.daysAhead));
    if (opts?.daysBack != null) params = params.set('days_back', String(opts.daysBack));
    if (opts?.limit != null) params = params.set('limit', String(opts.limit));
    if (opts?.withOdds) params = params.set('with_odds', 'true');
    return this.http.get<Match[]>(`${this.base}/matches`, { params });
  }

  /** Todo lo de la página de partido: escudos, medias, mitades, forma, bajas, once y jugadores. */
  getMatchPreview(id: number): Observable<MatchPreview> {
    return this.http.get<MatchPreview>(`${this.base}/matches/${id}/preview`);
  }

  listGradedOdds(opts?: {
    horizonHours?: number;
    maxLines?: number;
    bookmaker?: string;
    /** ``sport_key`` de la competición (``soccer_epl``, …). */
    sportKey?: string;
    /** Solo las líneas de este partido (página de partido). */
    matchId?: number;
  }): Observable<GradedOddsLine[]> {
    let params = new HttpParams();
    if (opts?.horizonHours != null) {
      params = params.set('horizon_hours', String(opts.horizonHours));
    }
    if (opts?.maxLines != null) params = params.set('max_lines', String(opts.maxLines));
    if (opts?.bookmaker) params = params.set('bookmaker_key', opts.bookmaker);
    if (opts?.sportKey) params = params.set('sport_key', opts.sportKey);
    if (opts?.matchId != null) params = params.set('match_id', String(opts.matchId));
    return this.http.get<GradedOddsLine[]>(`${this.base}/dashboard/graded-odds`, { params });
  }

  /** Solo cuotas Winamax de los próximos 5 días (~1 min). Las stats las trae la tarea programada. */
  refreshOdds(): Observable<OddsRefreshSummary> {
    return this.http.post<OddsRefreshSummary>(`${this.base}/refresh/odds`, null);
  }

  login(username: string, password: string): Observable<AuthTokenResponse> {
    return this.http.post<AuthTokenResponse>(`${this.base}/auth/login`, {
      username,
      password,
    });
  }

  authMe(): Observable<AuthUserBrief> {
    return this.http.get<AuthUserBrief>(`${this.base}/auth/me`);
  }

  listMyParlays(): Observable<SavedParlay[]> {
    return this.http.get<SavedParlay[]>(`${this.base}/me/parlays`);
  }

  saveMyParlay(payload: {
    title?: string | null;
    legs: SavedParlayLegPersist[];
    stake?: string | null;
  }): Observable<SavedParlay> {
    return this.http.post<SavedParlay>(`${this.base}/me/parlays`, payload);
  }

  /** Solo cambia lo enviado; `manual_result: null` vuelve a la liquidación automática. */
  updateMyParlay(
    id: number,
    changes: { stake?: string | null; manual_result?: ResultadoManual | null },
  ): Observable<SavedParlay> {
    return this.http.patch<SavedParlay>(`${this.base}/me/parlays/${id}`, changes);
  }

  deleteMyParlay(id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}/me/parlays/${id}`);
  }

  refreshMyParlaysGrading(): Observable<{ updated_parlays: number }> {
    return this.http.post<{ updated_parlays: number }>(
      `${this.base}/me/parlays/refresh-grades`,
      {},
    );
  }

  getMyParlayMetricsSummary(): Observable<UserParlayMetrics> {
    return this.http.get<UserParlayMetrics>(`${this.base}/me/parlays/metrics-summary`);
  }

  /** Picks de hoy si ya se generaron (no genera nada). */
  getFeaturedPicksToday(): Observable<FeaturedToday> {
    return this.http.get<FeaturedToday>(`${this.base}/featured-picks/today`);
  }

  /**
   * Genera y congela los picks de hoy si faltan (primera apertura del día). Si las cuotas de
   * hoy están viejas, el backend las descarga antes: puede tardar hasta un minuto.
   */
  ensureFeaturedPicksToday(): Observable<FeaturedToday> {
    return this.http.post<FeaturedToday>(`${this.base}/featured-picks/today`, {});
  }

  getFeaturedPicksHistory(days: number): Observable<FeaturedDailyPickRow[]> {
    return this.http.get<FeaturedDailyPickRow[]>(`${this.base}/featured-picks/history`, {
      params: { days: String(days) },
    });
  }

  getFeaturedPicksMetrics(): Observable<FeaturedPicksMetrics> {
    return this.http.get<FeaturedPicksMetrics>(`${this.base}/featured-picks/metrics`);
  }

  refreshFeaturedPicksGrading(): Observable<{ updated_rows: number }> {
    return this.http.post<{ updated_rows: number }>(`${this.base}/featured-picks/refresh-grades`, {});
  }
}
