import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import {
  AuthTokenResponse,
  AuthUserBrief,
  CalibrationReport,
  CustomBetAssessmentRequest,
  CustomBetAssessmentResponse,
  DailyRefreshSummary,
  GradedOddsLine,
  Match,
  MatchAnalysis,
  MatchPlayerProps,
  MatchStats,
  OddsRefreshSummary,
  OfficialPipelineSummary,
  Odds,
  RecommendationSet,
  RefreshSummary,
  ResultsRefreshSummary,
  FeaturedDailyPickRow,
  FeaturedPicksMetrics,
  UserParlayMetrics,
  SavedParlay,
  SavedParlayLegPersist,
  UserProfile,
  ValueBet,
  StatisticsOverview,
  TeamDetailStatistics,
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

  getStatisticsOverview(opts?: { sportKey?: string; limit?: number }): Observable<StatisticsOverview> {
    let params = new HttpParams();
    if (opts?.sportKey) params = params.set('sport_key', opts.sportKey);
    if (opts?.limit != null) params = params.set('limit', String(opts.limit));
    return this.http.get<StatisticsOverview>(`${this.base}/statistics/overview`, { params });
  }

  getTeamStatistics(teamId: number, recent = 8): Observable<TeamDetailStatistics> {
    return this.http.get<TeamDetailStatistics>(`${this.base}/statistics/teams/${teamId}`, {
      params: new HttpParams().set('recent', String(recent)),
    });
  }

  listMatches(opts?: {
    league?: string;
    scope?: 'target';
    daysAhead?: number;
    daysBack?: number;
    limit?: number;
  }): Observable<Match[]> {
    let params = new HttpParams();
    if (opts?.league) params = params.set('league', opts.league);
    if (opts?.scope) params = params.set('scope', opts.scope);
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

  listGradedOdds(opts?: {
    horizonHours?: number;
    maxLines?: number;
    bookmaker?: string;
    /** ``sport_key`` de la competición (``soccer_epl``, …). */
    sportKey?: string;
  }): Observable<GradedOddsLine[]> {
    let params = new HttpParams();
    if (opts?.horizonHours != null) {
      params = params.set('horizon_hours', String(opts.horizonHours));
    }
    if (opts?.maxLines != null) params = params.set('max_lines', String(opts.maxLines));
    if (opts?.bookmaker) params = params.set('bookmaker_key', opts.bookmaker);
    if (opts?.sportKey) params = params.set('sport_key', opts.sportKey);
    return this.http.get<GradedOddsLine[]>(`${this.base}/dashboard/graded-odds`, { params });
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

  refreshDaily(opts?: {
    statsDaysBack?: number;
    statsAllLeagues?: boolean;
    fixturesDaysAhead?: number;
  }): Observable<DailyRefreshSummary> {
    let params = new HttpParams();
    if (opts?.statsDaysBack != null) params = params.set('stats_days_back', String(opts.statsDaysBack));
    if (opts?.statsAllLeagues != null) params = params.set('stats_all_leagues', String(opts.statsAllLeagues));
    if (opts?.fixturesDaysAhead != null) {
      params = params.set('fixtures_days_ahead', String(opts.fixturesDaysAhead));
    }
    return this.http.post<DailyRefreshSummary>(`${this.base}/refresh/daily`, null, { params });
  }

  /** Sofascore + Flashscore + cuotas Winamax (sin Odds API). */
  refreshOfficialPipeline(opts?: {
    statsDaysBack?: number;
    statsAllLeagues?: boolean;
    fixturesDaysAhead?: number;
    winamaxDaysAhead?: number;
    winamaxMaxMatches?: number;
  }): Observable<OfficialPipelineSummary> {
    let params = new HttpParams();
    if (opts?.statsDaysBack != null) params = params.set('stats_days_back', String(opts.statsDaysBack));
    if (opts?.statsAllLeagues != null) params = params.set('stats_all_leagues', String(opts.statsAllLeagues));
    if (opts?.fixturesDaysAhead != null) {
      params = params.set('fixtures_days_ahead', String(opts.fixturesDaysAhead));
    }
    if (opts?.winamaxDaysAhead != null) {
      params = params.set('winamax_days_ahead', String(opts.winamaxDaysAhead));
    }
    if (opts?.winamaxMaxMatches != null) {
      params = params.set('winamax_max_matches', String(opts.winamaxMaxMatches));
    }
    return this.http.post<OfficialPipelineSummary>(`${this.base}/refresh/official`, null, { params });
  }

  /** Solo cuotas Winamax de hoy y mañana (~1 min). Las stats las trae la tarea programada. */
  refreshOdds(): Observable<OddsRefreshSummary> {
    return this.http.post<OddsRefreshSummary>(`${this.base}/refresh/odds`, null);
  }

  getLatestCalibration(): Observable<CalibrationReport> {
    return this.http.get<CalibrationReport>(`${this.base}/calibration/latest`);
  }

  postCustomBetAssessment(
    body: CustomBetAssessmentRequest,
  ): Observable<CustomBetAssessmentResponse> {
    return this.http.post<CustomBetAssessmentResponse>(`${this.base}/analysis/custom-bet`, body);
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
  }): Observable<SavedParlay> {
    return this.http.post<SavedParlay>(`${this.base}/me/parlays`, payload);
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

  refreshMyParlayGrade(id: number): Observable<SavedParlay> {
    return this.http.post<SavedParlay>(
      `${this.base}/me/parlays/${id}/refresh-grade`,
      {},
    );
  }

  getMyParlayMetricsSummary(): Observable<UserParlayMetrics> {
    return this.http.get<UserParlayMetrics>(`${this.base}/me/parlays/metrics-summary`);
  }

  getFeaturedPicksToday(): Observable<FeaturedDailyPickRow> {
    return this.http.get<FeaturedDailyPickRow>(`${this.base}/featured-picks/today`);
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
