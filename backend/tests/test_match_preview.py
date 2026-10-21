"""Tests de la página de partido sin BD ni red: escudos, alineaciones y previa."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from decimal import Decimal

from backend.models import Team
from backend.services.match_listing import Odds1x2, pick_1x2
from backend.services.match_lineups import (
    LineupPlayer,
    by_line,
    mark_absences,
    same_player,
    should_fetch_official,
)
from backend.services.match_preview import (
    PlayerRow,
    TeamSample,
    aggregate_players,
    average,
    result_letter,
    season_start,
)
from backend.services.player_photos import player_photo_url
from backend.services.team_crests import crest_colors, flashscore_logo_url, set_team_logo

T0 = datetime(2026, 10, 16, 19, 0, tzinfo=timezone.utc)


def png(stripes: list[tuple[tuple[int, int, int, int], int]]) -> bytes:
    """PNG de 100x100 en franjas horizontales: ``[(rgba, alto), ...]``."""
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    y = 0
    for rgba, height in stripes:
        for yy in range(y, y + height):
            for x in range(100):
                img.putpixel((x, yy), rgba)
        y += height
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestEscudos:
    def test_url_del_escudo(self) -> None:
        assert flashscore_logo_url("EBfZuwme-bRmKmISE.png") == (
            "https://static.flashscore.com/res/image/data/EBfZuwme-bRmKmISE.png"
        )

    @pytest.mark.parametrize("code", [None, "", "sin-extension", "../x.png"])
    def test_codigos_invalidos_no_dan_url(self, code: str | None) -> None:
        assert flashscore_logo_url(code) is None

    def test_escudo_nuevo_borra_los_colores_para_recalcularlos(self) -> None:
        team = Team(name="Everton", logo_url=None, primary_color="#123456")
        assert set_team_logo(team, "EBfZuwme-bRmKmISE.png") is True
        assert team.primary_color is None
        assert set_team_logo(team, "EBfZuwme-bRmKmISE.png") is False  # mismo escudo

    def test_gana_el_color_saturado_al_gris_del_contorno(self) -> None:
        crest = png([((200, 16, 46, 255), 40), ((128, 128, 128, 255), 45), ((0, 0, 0, 0), 15)])
        primary, _ = crest_colors(crest)
        assert primary == "#c8102e"

    def test_secundario_distinto_y_el_blanco_cuenta_como_secundario(self) -> None:
        crest = png([((0, 82, 159, 255), 60), ((255, 255, 255, 255), 40)])
        assert crest_colors(crest) == ("#00529f", "#ffffff")

    def test_escudo_en_blanco_y_negro(self) -> None:
        crest = png([((0, 0, 0, 255), 50), ((255, 255, 255, 255), 50)])
        assert crest_colors(crest) == ("#000000", "#ffffff")

    def test_escudo_transparente_no_rompe(self) -> None:
        assert crest_colors(png([((0, 0, 0, 0), 100)])) == ("#ffffff", None)


class TestAlineacionOficial:
    @pytest.mark.parametrize(
        ("falta", "esperado"),
        [(timedelta(minutes=70), True), (timedelta(hours=2), False),
         (timedelta(minutes=-30), True), (timedelta(hours=-4), False)],
    )
    def test_ventana_alrededor_del_inicio(self, falta: timedelta, esperado: bool) -> None:
        assert should_fetch_official(T0 + falta, T0, None) is esperado

    def test_no_repite_la_peticion_antes_de_10_min(self) -> None:
        kickoff = T0 + timedelta(minutes=40)
        assert not should_fetch_official(kickoff, T0, T0 - timedelta(minutes=5))
        assert should_fetch_official(kickoff, T0, T0 - timedelta(minutes=12))


class TestOnceProbable:
    def test_ordena_por_lineas(self) -> None:
        players = [LineupPlayer("Delantero", "F"), LineupPlayer("Medio", "M"),
                   LineupPlayer("Portero", "G"), LineupPlayer("Defensa", "D")]
        assert [p.name for p in by_line(players)] == ["Portero", "Defensa", "Medio", "Delantero"]

    def test_sin_posicion_completan_la_linea_que_falta(self) -> None:
        # Chelsea del 17/10: 3 defensas, 4 medios, 1 delantero y 2 sin posición → 3-4-3.
        once = ([LineupPlayer("P", "G")] + [LineupPlayer(f"D{i}", "D") for i in range(3)]
                + [LineupPlayer(f"M{i}", "M") for i in range(4)] + [LineupPlayer("F0", "F"),
                LineupPlayer("Pedro Neto", None), LineupPlayer("Otro", None)])
        lines = [p.position for p in by_line(once)]
        assert lines.count("D") == 3 and lines.count("M") == 4 and lines.count("F") == 3

    def test_sin_portero_el_primero_sin_posicion_lo_es(self) -> None:
        once = [LineupPlayer("A", None), LineupPlayer("B", "D")]
        assert by_line(once)[0] == LineupPlayer("A", "G")

    def test_marca_bajas_por_id_y_por_nombre_en_cualquier_orden(self) -> None:
        once = mark_absences(
            [("Lewandowski Robert", "F", "fs9"), ("Pedri", "M", None), ("Raphinha", "F", "fs7")],
            [("fs9", "Robert Lewandowski", "missing"), ("x1", "pedri", "doubtful")],
        )
        estados = {p.name: p.status for p in once}
        assert estados == {"Lewandowski Robert": "missing", "Pedri": "doubtful", "Raphinha": None}

    @pytest.mark.parametrize(
        ("baja", "jugador", "esperado"),
        [("Soriano M.", "Mario Soriano", True), ("Aubameyang P.", "Pierre-Emerick Aubameyang", True),
         ("Aubameyang P.", "Pierre Emerick Aubameyang", True), ("Soriano M.", "Roberto Soriano", False),
         ("Etta Eyong K.", "Karl Etta Eyong", True), ("Pedri", "Pedri", True)],
    )
    def test_mismo_jugador_con_apellido_e_inicial(
        self, baja: str, jugador: str, esperado: bool,
    ) -> None:
        assert same_player(baja, jugador) is esperado


class TestPrevia:
    def test_medias_ignoran_los_partidos_sin_ese_dato(self) -> None:
        samples = [
            TeamSample(True, 2, 0, corners=6, goals_2h=1),
            TeamSample(False, 1, 1, corners=None, goals_2h=0),
            TeamSample(True, 0, 3),
        ]
        avg = average(samples)
        assert avg.matches == 3
        assert avg.goals_for == 1.0 and avg.goals_against == 1.33
        assert avg.corners == 6.0  # solo un partido con córners
        assert avg.goals_2h == 0.5
        assert avg.xg is None

    def test_sin_partidos(self) -> None:
        assert average([]).matches == 0 and average([]).goals_for is None

    @pytest.mark.parametrize(("gf", "ga", "res"), [(2, 1, "W"), (1, 1, "D"), (0, 3, "L")])
    def test_resultado(self, gf: int, ga: int, res: str) -> None:
        assert result_letter(gf, ga) == res

    def test_temporada_empieza_en_julio(self) -> None:
        assert season_start(T0) == datetime(2026, 7, 1, tzinfo=timezone.utc)
        assert season_start(datetime(2027, 3, 1, tzinfo=timezone.utc)).year == 2026

    def test_jugadores_por_minutos_y_tiros_a_puerta_por_90(self) -> None:
        rows = [
            PlayerRow(1, "Mbappé", "F", True, 90, 1, 5, 3, 0),
            PlayerRow(1, "Mbappé", "F", True, 90, 0, 3, 1, 1),
            PlayerRow(2, "Suplente", "M", False, 20, 0, 1, 1, 0),
        ]
        players = aggregate_players(rows)
        assert [p.name for p in players] == ["Mbappé", "Suplente"]
        mbappe = players[0]
        assert (mbappe.apps, mbappe.starts, mbappe.shots_on_target) == (2, 2, 4)
        assert mbappe.shots_on_target_per90 == 2.0
        assert players[1].shots_on_target_per90 is None  # menos de 90' jugados


class TestUnoEquisDosDeLaLista:
    def test_coge_el_snapshot_mas_nuevo(self) -> None:
        antes, ahora = T0 - timedelta(hours=6), T0
        rows = [
            ("Sevilla", Decimal("2.10"), antes), ("Draw", Decimal("3.30"), antes),
            ("Sevilla", Decimal("2.05"), ahora), ("Draw", Decimal("3.40"), ahora),
            ("Real Betis", Decimal("3.75"), ahora),
        ]
        assert pick_1x2(rows, "Sevilla", "Real Betis") == Odds1x2(
            Decimal("2.05"), Decimal("3.40"), Decimal("3.75"), "Sevilla", "Real Betis",
        )

    @pytest.mark.parametrize(
        ("local", "visitante", "winamax", "lado"),
        [("Espanyol", "Atlético Madrid", "Atl. Madrid", "away"),
         ("Venezia", "SSC Napoli", "Nápoles", "away"),
         ("Hamburger SV", "VfB Stuttgart", "Hamburgo", "home")],
    )
    def test_nombres_de_winamax_en_espanol(
        self, local: str, visitante: str, winamax: str, lado: str,
    ) -> None:
        picked = pick_1x2([(winamax, Decimal("2.10"), T0)], local, visitante)
        assert picked is not None
        assert getattr(picked, lado) == Decimal("2.10")
        assert getattr(picked, f"{lado}_name") == winamax

    def test_sin_cuotas_o_con_nombres_que_no_casan(self) -> None:
        assert pick_1x2([], "A", "B") is None
        assert pick_1x2([("Otro", Decimal("2"), T0)], "Sevilla", "Real Betis") is None


class TestFotosDeJugador:
    def test_url_de_la_foto_en_el_cdn(self) -> None:
        assert player_photo_url("zJsYDqgl-URqd4A5l.png") == (
            "https://static.flashscore.com/res/image/data/zJsYDqgl-URqd4A5l.png"
        )

    @pytest.mark.parametrize("path", [None, "", "sin-extension", "a/b.png"])
    def test_sin_ruta_valida_no_hay_url(self, path) -> None:
        assert player_photo_url(path) is None

    def test_el_agregado_lleva_la_foto_de_alguna_aparicion(self) -> None:
        def row(photo):
            return PlayerRow(
                player_id=1, name="García Joan", position="G", is_starter=True, minutes=90,
                goals=0, shots=0, shots_on_target=0, yellow_cards=0, photo_path=photo,
            )

        [p] = aggregate_players([row(None), row("zJsYDqgl-URqd4A5l.png")])
        assert p.photo_url == player_photo_url("zJsYDqgl-URqd4A5l.png")

    def test_deducir_la_linea_no_pierde_la_foto(self) -> None:
        sin_pos = LineupPlayer("Nuevo", None, photo_url="https://x/y.png")
        [p] = [p for p in by_line([LineupPlayer("Portero", "G"), sin_pos]) if p.name == "Nuevo"]
        assert p.position in {"D", "M", "F"}
        assert p.photo_url == "https://x/y.png"
