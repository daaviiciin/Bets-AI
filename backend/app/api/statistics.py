"""Resumen de estadísticas para la sección de ayuda del dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from backend.app.database import get_db
from backend.app.schemas.responses import (
    CompetitionStatsOut,
    PlayerStatisticsOut,
    StatisticsOverviewOut,
    TeamDetailStatisticsOut,
    TeamRecentMatchOut,
    TeamStatisticsOut,
)
from backend.models import League, Match, MatchStats, PlayerSeasonStats, Season, Team, TeamSeasonStats

router = APIRouter(prefix="/statistics", tags=["statistics"])


def _logo_url(source: str | None, external_id: str | None) -> str | None:
    if source == "sofascore" and external_id:
        return f"https://img.sofascore.com/api/v1/team/{external_id}/image"
    return None


def _player_image_url(source: str | None, external_id: str | None) -> str | None:
    if source == "sofascore" and external_id:
        return f"https://img.sofascore.com/api/v1/player/{external_id}/image"
    return None


@router.get("/overview", response_model=StatisticsOverviewOut)
def statistics_overview(
    sport_key: str | None = Query(None, description="Filtra por sport_key de la competición."),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> StatisticsOverviewOut:
    leagues = list(db.scalars(select(League).order_by(League.name)).all())
    if sport_key:
        leagues = [league for league in leagues if league.sport_key == sport_key]

    competitions: list[CompetitionStatsOut] = []
    teams: list[TeamStatisticsOut] = []
    players: list[PlayerStatisticsOut] = []
    for league in leagues:
        seasons = list(db.scalars(
            select(Season).where(Season.league_id == league.id).order_by(Season.year_start.desc())
        ).all())
        season = seasons[0] if seasons else None
        team_rows = list(db.scalars(
            select(TeamSeasonStats).options(joinedload(TeamSeasonStats.team))
            .where(TeamSeasonStats.season_id == season.id)
            .order_by(TeamSeasonStats.points.desc().nullslast(), TeamSeasonStats.goals_for.desc().nullslast())
            .limit(limit)
        ).all()) if season else []
        player_rows = list(db.scalars(
            select(PlayerSeasonStats).options(
                joinedload(PlayerSeasonStats.player), joinedload(PlayerSeasonStats.team),
            ).where(PlayerSeasonStats.season_id == season.id)
            .order_by(PlayerSeasonStats.goals.desc().nullslast(), PlayerSeasonStats.shots_on_target.desc().nullslast())
            .limit(limit)
        ).all()) if season else []
        match_stats_count = db.scalar(
            select(func.count(MatchStats.id)).join(Match, Match.id == MatchStats.match_id)
            .where(Match.league_id == league.id)
        ) or 0
        competitions.append(CompetitionStatsOut(
            sport_key=league.sport_key,
            name=league.name,
            teams_count=len(team_rows),
            players_count=len(player_rows),
            matches_with_stats=int(match_stats_count // 2),
        ))
        for row in team_rows:
            teams.append(TeamStatisticsOut(
                team_id=row.team_id,
                name=row.team.name,
                logo_url=_logo_url(row.team.external_source, row.team.external_id),
                matches_played=row.matches_played,
                wins=row.wins,
                draws=row.draws,
                losses=row.losses,
                goals_for=row.goals_for,
                goals_against=row.goals_against,
                points=row.points,
                xg_for=row.xg_for,
                xg_against=row.xg_against,
            ))
        for row in player_rows:
            players.append(PlayerStatisticsOut(
                player_id=row.player_id,
                name=row.player.name,
                image_url=_player_image_url(row.player.external_source, row.player.external_id),
                team_name=row.team.name if row.team else None,
                goals=row.goals,
                assists=row.assists,
                shots=row.shots,
                shots_on_target=row.shots_on_target,
                xg=row.xg,
                matches_played=row.matches_played,
            ))
        if sport_key:
            break

    return StatisticsOverviewOut(
        selected_sport_key=sport_key,
        competitions=competitions,
        teams=teams[:limit],
        players=players[:limit],
    )


@router.get("/teams/{team_id}", response_model=TeamDetailStatisticsOut)
def team_statistics_detail(
    team_id: int,
    recent: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
) -> TeamDetailStatisticsOut:
    team = db.scalar(select(Team).where(Team.id == team_id))
    if team is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Equipo no encontrado")

    season_row = db.execute(
        select(TeamSeasonStats, Season, League)
        .join(Season, Season.id == TeamSeasonStats.season_id)
        .join(League, League.id == Season.league_id)
        .where(TeamSeasonStats.team_id == team_id)
        .order_by(Season.year_start.desc())
    ).first()
    season_stats = season_row[0] if season_row else None
    selected_season_id = season_row[1].id if season_row else None
    league_name = season_row[2].name if season_row else None

    matches = list(db.scalars(
        select(Match).where(
            (Match.home_team_id == team_id) | (Match.away_team_id == team_id),
            Match.status == "finished",
        ).options(joinedload(Match.home_team), joinedload(Match.away_team))
        .order_by(Match.commence_time.desc()).limit(recent)
    ).all())
    match_stats = list(db.scalars(
        select(MatchStats).where(
            MatchStats.team_id == team_id,
            MatchStats.match_id.in_([match.id for match in matches]),
        )
    ).all()) if matches else []
    stats_by_match = {row.match_id: row for row in match_stats}

    recent_rows: list[TeamRecentMatchOut] = []
    form: list[str] = []
    total_sot = total_corners = total_yellows = 0
    stats_samples = 0
    home_played = home_wins = away_played = away_wins = 0
    for match in matches:
        is_home = match.home_team_id == team_id
        team_score = match.home_score if is_home else match.away_score
        opponent_score = match.away_score if is_home else match.home_score
        if team_score is None or opponent_score is None:
            result = "pending"
        elif team_score > opponent_score:
            result = "W"
        elif team_score == opponent_score:
            result = "D"
        else:
            result = "L"
        form.append(result)
        if is_home:
            home_played += 1
            home_wins += result == "W"
        else:
            away_played += 1
            away_wins += result == "W"
        row = stats_by_match.get(match.id)
        if row:
            stats_samples += 1
            total_sot += row.shots_on_target or 0
            total_corners += row.corners or 0
            total_yellows += row.yellow_cards or 0
        opponent = match.away_team.name if is_home else match.home_team.name
        recent_rows.append(TeamRecentMatchOut(
            match_id=match.id,
            opponent=opponent,
            is_home=is_home,
            commence_time=match.commence_time,
            team_score=team_score,
            opponent_score=opponent_score,
            result=result,
            shots_on_target=row.shots_on_target if row else None,
            corners=row.corners if row else None,
            yellow_cards=row.yellow_cards if row else None,
        ))

    team_out = TeamStatisticsOut(
        team_id=team.id,
        name=team.name,
        logo_url=_logo_url(team.external_source, team.external_id),
        matches_played=season_stats.matches_played if season_stats else None,
        wins=season_stats.wins if season_stats else None,
        draws=season_stats.draws if season_stats else None,
        losses=season_stats.losses if season_stats else None,
        goals_for=season_stats.goals_for if season_stats else None,
        goals_against=season_stats.goals_against if season_stats else None,
        points=season_stats.points if season_stats else None,
        xg_for=season_stats.xg_for if season_stats else None,
        xg_against=season_stats.xg_against if season_stats else None,
    )
    player_stmt = select(PlayerSeasonStats).options(
        joinedload(PlayerSeasonStats.player), joinedload(PlayerSeasonStats.team),
    ).where(PlayerSeasonStats.team_id == team_id)
    if selected_season_id is not None:
        player_stmt = player_stmt.where(PlayerSeasonStats.season_id == selected_season_id)
    player_rows = list(db.scalars(
        player_stmt.order_by(
            PlayerSeasonStats.goals.desc().nullslast(),
            PlayerSeasonStats.shots_on_target.desc().nullslast(),
        ).limit(100)
    ).all())
    unique_players: dict[int, PlayerStatisticsOut] = {}
    for row in player_rows:
        unique_players[row.player_id] = PlayerStatisticsOut(
            player_id=row.player_id,
            name=row.player.name,
            image_url=_player_image_url(row.player.external_source, row.player.external_id),
            team_name=team.name,
            goals=row.goals,
            assists=row.assists,
            shots=row.shots,
            shots_on_target=row.shots_on_target,
            xg=row.xg,
            matches_played=row.matches_played,
        )
    players = list(unique_players.values())
    return TeamDetailStatisticsOut(
        team=team_out,
        competition_name=league_name,
        home_win_pct=round(100 * home_wins / home_played, 1) if home_played else None,
        away_win_pct=round(100 * away_wins / away_played, 1) if away_played else None,
        recent_form=form,
        recent_matches=recent_rows,
        totals={
            "shots_on_target": total_sot if stats_samples else None,
            "corners": total_corners if stats_samples else None,
            "yellow_cards": total_yellows if stats_samples else None,
            "stats_matches": stats_samples,
        },
        players=players,
    )