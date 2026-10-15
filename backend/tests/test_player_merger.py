"""Tests de la selección de jugadores duplicados a fusionar (sin BD)."""

from __future__ import annotations

from backend.services.player_merger import PlayerRow, find_merge_pairs


def sofa(pid: int, name: str, team: int = 1, fs_id: str | None = None) -> PlayerRow:
    return PlayerRow(pid, name, team, "sofascore", fs_id)


def flash(pid: int, name: str, team: int = 1) -> PlayerRow:
    return PlayerRow(pid, name, team, "flashscore", f"fs{pid}")


class TestFindMergePairs:
    def test_par_inequivoco_se_fusiona_conservando_el_de_sofascore(self) -> None:
        pairs = find_merge_pairs([sofa(1, "Nico Paz"), flash(2, "Paz Nicolás")])
        assert [(p.keep.id, p.duplicate.id) for p in pairs] == [(1, 2)]

    def test_si_el_de_sofascore_ya_tiene_flashscore_id_no_se_toca(self) -> None:
        assert find_merge_pairs([sofa(1, "Nico Paz", fs_id="x"), flash(2, "Paz Nicolás")]) == []

    def test_equipos_distintos_no_se_fusionan(self) -> None:
        assert find_merge_pairs([sofa(1, "Nico Paz", team=1), flash(2, "Paz Nicolás", team=2)]) == []

    def test_nombre_corto_ambiguo_no_se_fusiona(self) -> None:
        # "Gabriel" de Flashscore encaja con dos jugadores del equipo: no se elige ninguno.
        players = [sofa(1, "Gabriel Magalhães"), sofa(3, "Gabriel Jesus"), flash(2, "Gabriel")]
        assert find_merge_pairs(players) == []

    def test_dos_duplicados_posibles_no_se_fusiona(self) -> None:
        players = [sofa(1, "Bremer"), flash(2, "Bremer Gleison"), flash(3, "Bremer Lucas")]
        assert find_merge_pairs(players) == []

    def test_dos_jugadores_de_flashscore_no_se_fusionan_entre_si(self) -> None:
        assert find_merge_pairs([flash(1, "Undav Deniz"), flash(2, "Deniz Undav")]) == []
