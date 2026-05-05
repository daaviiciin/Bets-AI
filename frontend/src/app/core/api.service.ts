import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  CalibrationReport,
  DailyRefreshSummary,
  Match,
  MatchAnalysis,
  MatchPlayerProps,
  MatchStats,
  Odds,
  RecommendationSet,
  RefreshSummary,
  ResultsRefreshSummary,
  UserProfile,
  ValueBet,
} from './models';

/** Cliente HTTP centralizado para todos los endpoints de la API Bets AI. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly base = environment.apiBaseUrl;

  health(): Observable<{ service: string; status: string; version: string; now: string }> {
    return this.http.get<{ service: string; status: string; version: string; now: string }>(
      `${this.base}/`,
    );
  }

  listMatches(opts?: {
    league?: string;
    daysAhead?: number;
    daysBack?: number;
    limit?: number;
  }): Observable<Match[]> {
    let params = new HttpParams();
    if (opts?.league) params = params.set('league', opts.league);
    if (opts?.daysAhead != null) params = params.set('days_ahead', String(opts.daysAhead));
    if (opts?.daysBack != null) params = params.set('days_back', String(opts.daysBack));
    if (opts?.limit != null) params = params.set('limit', String(opts.limit));
    return this.http.get<Match[]>(`${this.base}/matches`, { params });
  }

  getMatch(id: number): Observable<Match> {
    return this.http.get<Match>(`${this.base}/matches/${id}`);
  }

  getMatchOdds(id: number, opts?: { market?: string; bookmaker?: string }): Observable<Odds[]> {
    let params = new HttpParams();
    if (opts?.market) params = params.set('market', opts.market);
    if (opts?.bookmaker) params = params.set('bookmaker', opts.bookmaker);
    return this.http.get<Odds[]>(`${this.base}/matches/${id}/odds`, { params });
  }

  getMatchAnalysis(id: number): Observable<MatchAnalysis> {
    return this.http.get<MatchAnalysis>(`${this.base}/matches/${id}/analysis`);
  }

  getMatchStats(id: number): Observable<MatchStats> {
    return this.http.get<MatchStats>(`${this.base}/matches/${id}/stats`);
  }

  getMatchPlayerProps(id: number, opts?: { bookmaker?: string }): Observable<MatchPlayerProps> {
    let params = new HttpParams();
    if (opts?.bookmaker) params = params.set('bookmaker', opts.bookmaker);
    return this.http.get<MatchPlayerProps>(
      `${this.base}/matches/${id}/player-props`,
      { params },
    );
  }

  listValueBets(opts?: {
    minEv?: number;
    minEvModel?: number;
    requireModelAgreement?: boolean;
    horizonHours?: number;
    league?: string;
    top?: number;
  }): Observable<ValueBet[]> {
    let params = new HttpParams();
    if (opts?.minEv != null) params = params.set('min_ev', String(opts.minEv));
    if (opts?.minEvModel != null) params = params.set('min_ev_model', String(opts.minEvModel));
    if (opts?.requireModelAgreement != null) {
      params = params.set('require_model_agreement', String(opts.requireModelAgreement));
    }
    if (opts?.horizonHours != null) params = params.set('horizon_hours', String(opts.horizonHours));
    if (opts?.league) params = params.set('league', opts.league);
    if (opts?.top != null) params = params.set('top', String(opts.top));
    return this.http.get<ValueBet[]>(`${this.base}/value-bets`, { params });
  }

  listProfiles(): Observable<UserProfile[]> {
    return this.http.get<UserProfile[]>(`${this.base}/profiles`);
  }

  getProfile(name: string): Observable<UserProfile> {
    return this.http.get<UserProfile>(`${this.base}/profiles/${encodeURIComponent(name)}`);
  }

  getRecommendations(
    name: string,
    opts?: { horizonHours?: number; maxSingles?: number; maxParlays?: number; strict?: boolean },
  ): Observable<RecommendationSet> {
    let params = new HttpParams();
    if (opts?.horizonHours != null) params = params.set('horizon_hours', String(opts.horizonHours));
    if (opts?.maxSingles != null) params = params.set('max_singles', String(opts.maxSingles));
    if (opts?.maxParlays != null) params = params.set('max_parlays', String(opts.maxParlays));
    if (opts?.strict != null) params = params.set('strict', String(opts.strict));
    return this.http.get<RecommendationSet>(
      `${this.base}/profiles/${encodeURIComponent(name)}/recommendations`,
      { params },
    );
  }

  triggerRefresh(): Observable<RefreshSummary> {
    return this.http.post<RefreshSummary>(`${this.base}/refresh`, null);
  }

  refreshResults(opts?: { maxPages?: number }): Observable<ResultsRefreshSummary> {
    let params = new HttpParams();
    if (opts?.maxPages != null) params = params.set('max_pages', String(opts.maxPages));
    return this.http.post<ResultsRefreshSummary>(`${this.base}/refresh/results`, null, { params });
  }

  refreshDaily(opts?: { statsDaysBack?: number; statsAllLeagues?: boolean }): Observable<DailyRefreshSummary> {
    let params = new HttpParams();
    if (opts?.statsDaysBack != null) params = params.set('stats_days_back', String(opts.statsDaysBack));
    if (opts?.statsAllLeagues != null) params = params.set('stats_all_leagues', String(opts.statsAllLeagues));
    return this.http.post<DailyRefreshSummary>(`${this.base}/refresh/daily`, null, { params });
  }

  getLatestCalibration(): Observable<CalibrationReport> {
    return this.http.get<CalibrationReport>(`${this.base}/calibration/latest`);
  }
}
