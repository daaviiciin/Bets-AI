"""Tests de la elección del escudo HD en la respuesta de la API de Wikipedia."""

from __future__ import annotations

from backend.services.escudos_hd import elegir_escudo, logo_de_wikidata, url_commons

_UTM = "?utm_source=en.wikipedia.org&utm_campaign=api&utm_content=original"


def _respuesta(*paginas: tuple[str, str | None, str | None]) -> dict:
    """Respuesta de ``generator=search`` con las páginas en ese orden (título, descripción, imagen)."""
    return {
        "query": {
            "pages": {
                str(100 + i): {
                    "index": i + 1,
                    "title": titulo,
                    "description": descripcion,
                    **({"original": {"source": imagen + _UTM}} if imagen else {}),
                }
                for i, (titulo, descripcion, imagen) in enumerate(paginas)
            }
        }
    }


# Recortes de respuestas reales (2026-10-28).
BETIS = _respuesta(
    ("Real Betis", "Association football club in Spain",
     "https://upload.wikimedia.org/wikipedia/en/2/2f/Real_Betis_2022_logo.svg"),
    ("2025–26 Real Betis season", "Real Betis 2025–26 football season", None),
    ("History of Real Betis", "History of Spanish association football club Real Betis Balompié",
     "https://upload.wikimedia.org/wikipedia/commons/4/47/Betiscoparey2022.jpg"),
)
INTER = _respuesta(
    ("Inter Milan", "Association football club in Italy",
     "https://upload.wikimedia.org/wikipedia/commons/0/05/FC_Internazionale_Milano_2021.svg"),
    ("Football Inter Club Association", "Association football club in Haiti",
     "https://upload.wikimedia.org/wikipedia/en/7/78/Football_Inter_Club_Association_logo.png"),
)


class TestElegirEscudo:
    def test_club_con_logo_svg(self):
        assert elegir_escudo(BETIS, "Real Betis") == (
            "https://upload.wikimedia.org/wikipedia/en/2/2f/Real_Betis_2022_logo.svg"
        )

    def test_quita_el_query_de_seguimiento(self):
        assert "?" not in elegir_escudo(BETIS, "Real Betis")

    def test_svg_sin_palabra_logo_vale(self):
        # El fichero del Inter no dice "logo": basta con que sea un SVG.
        assert elegir_escudo(INTER, "Inter").endswith("FC_Internazionale_Milano_2021.svg")

    def test_no_baja_a_un_homonimo_si_el_primero_no_tiene_escudo(self):
        sin_imagen = _respuesta(
            ("Inter Milan", "Association football club in Italy", None),
            ("Football Inter Club Association", "Association football club in Haiti",
             "https://upload.wikimedia.org/wikipedia/en/7/78/Football_Inter_Club_Association_logo.png"),
        )
        assert elegir_escudo(sin_imagen, "Inter") is None

    def test_foto_que_no_es_escudo_no_vale(self):
        foto = _respuesta(
            ("Real Betis", "Association football club in Spain",
             "https://upload.wikimedia.org/wikipedia/commons/4/47/Betiscoparey2022.jpg"),
        )
        assert elegir_escudo(foto, "Real Betis") is None

    def test_salta_temporadas_y_seccion_femenina(self):
        resp = _respuesta(
            ("Real Betis Féminas", "Spanish women's football club",
             "https://upload.wikimedia.org/wikipedia/en/1/11/Betis_Feminas_logo.svg"),
            ("2025–26 Real Betis season", "Real Betis 2025–26 football season", None),
            ("Real Betis", "Association football club in Spain",
             "https://upload.wikimedia.org/wikipedia/en/2/2f/Real_Betis_2022_logo.svg"),
        )
        assert elegir_escudo(resp, "Real Betis").endswith("Real_Betis_2022_logo.svg")

    def test_club_sin_palabras_en_comun_se_descarta(self):
        resp = _respuesta(
            ("Sevilla FC", "Association football club in Spain",
             "https://upload.wikimedia.org/wikipedia/en/3/3b/Sevilla_FC_logo.svg"),
        )
        assert elegir_escudo(resp, "Real Betis") is None

    def test_respuesta_vacia(self):
        assert elegir_escudo({}, "Real Betis") is None
        assert elegir_escudo({"batchcomplete": ""}, "Real Betis") is None

    def test_tildes_no_impiden_el_emparejamiento(self):
        resp = _respuesta(
            ("Atlético Madrid", "Association football club in Spain",
             "https://upload.wikimedia.org/wikipedia/en/f/f9/Atletico_Madrid_Logo_2024.svg"),
        )
        assert elegir_escudo(resp, "Atletico Madrid").endswith("Atletico_Madrid_Logo_2024.svg")


class _SesionFalsa:
    """Lo justo de una Session para BuscadorEscudosHD: devuelve equipos y cuenta commits."""

    def __init__(self, equipos):
        self.equipos = equipos
        self.commits = 0

    def scalars(self, _consulta):
        equipos = self.equipos

        class _R:
            def all(self):
                return equipos

        return _R()

    def commit(self):
        self.commits += 1


class _Equipo:
    def __init__(self, name):
        self.name = name
        self.logo_hd_url = None
        self.logo_hd_checked_at = None


class TestBuscadorEscudosHD:
    def test_un_429_corta_la_tanda_y_deja_el_resto_sin_marcar(self):
        from backend.services.escudos_hd import BuscadorEscudosHD, LimiteWikipedia

        betis, sevilla, inter = _Equipo("Real Betis"), _Equipo("Sevilla"), _Equipo("Inter")
        llamadas = []

        def consultar(nombre):
            llamadas.append(nombre)
            if nombre == "Sevilla":
                raise LimiteWikipedia("HTTP 429")
            return BETIS

        r = BuscadorEscudosHD(_SesionFalsa([betis, sevilla, inter]), consultar=consultar, pausa=0).run()
        assert llamadas == ["Real Betis", "Sevilla"]
        assert betis.logo_hd_url.endswith("Real_Betis_2022_logo.svg")
        assert betis.logo_hd_checked_at is not None
        assert sevilla.logo_hd_checked_at is None and inter.logo_hd_checked_at is None
        assert (r.encontrados, r.fallidos) == (1, 1)

    def test_sin_escudo_queda_marcado_para_no_repetir(self):
        from backend.services.escudos_hd import BuscadorEscudosHD

        raro = _Equipo("Club Desconocido")
        r = BuscadorEscudosHD(_SesionFalsa([raro]), consultar=lambda n: {}, pausa=0).run()
        assert raro.logo_hd_url is None
        assert raro.logo_hd_checked_at is not None
        assert r.sin_escudo == 1


class TestCasosReales:
    def test_real_sociedad_se_describe_como_sports_club(self):
        # Respuesta real (2026-10-28): la página del club dice "Spanish sports club".
        resp = _respuesta(
            ("Real Sociedad", "Spanish sports club",
             "https://upload.wikimedia.org/wikipedia/en/f/f1/Real_Sociedad_logo.svg"),
            ("Real Sociedad B", "Spanish football team based in San Sebastián", None),
            ("C.D. Real Sociedad", "Association football club in Honduras",
             "https://upload.wikimedia.org/wikipedia/en/4/40/C.D._Real_Sociedad_logo.png"),
        )
        assert elegir_escudo(resp, "Real Sociedad").endswith("Real_Sociedad_logo.svg")

    def test_filial_por_el_titulo_se_salta(self):
        resp = _respuesta(
            ("Real Sociedad B", "Spanish football team based in San Sebastián", None),
            ("Real Sociedad", "Association football club in Spain",
             "https://upload.wikimedia.org/wikipedia/en/f/f1/Real_Sociedad_logo.svg"),
        )
        assert elegir_escudo(resp, "Real Sociedad").endswith("Real_Sociedad_logo.svg")

    def test_titulo_identico_que_no_es_un_club_no_vale(self):
        resp = _respuesta(("Sevilla", "City in Andalusia, Spain", "https://upload.wikimedia.org/x/Sevilla_crest.svg"))
        assert elegir_escudo(resp, "Sevilla") is None


class TestNombresDificiles:
    def test_letras_nordicas(self):
        resp = _respuesta(
            ("Lillestrøm SK", "Norwegian association football club",
             "https://upload.wikimedia.org/wikipedia/en/1/1a/Lillestrom_SK_logo.svg"),
            ("Lillestrøm", "Municipality in Akershus, Norway", "https://upload.wikimedia.org/x/Lillestrom.jpg"),
        )
        assert elegir_escudo(resp, "Lillestrom").endswith("Lillestrom_SK_logo.svg")

    def test_ae_danesa(self):
        resp = _respuesta(
            ("FC Nordsjælland", "Danish association football club",
             "https://upload.wikimedia.org/wikipedia/en/9/9e/FC_Nordsjaelland_logo.svg"),
        )
        assert elegir_escudo(resp, "Nordsjaelland").endswith("FC_Nordsjaelland_logo.svg")

    def test_prefijo_ferencvarosi(self):
        resp = _respuesta(
            ("Ferencvárosi TC", "Hungarian sports club",
             "https://upload.wikimedia.org/wikipedia/en/8/8e/Ferencvarosi_TC_logo.svg"),
        )
        # "Hungarian sports club" no dice "football": vale por el título (prefijo) solo si
        # la descripción es de club de fútbol; aquí no lo es y no es título idéntico.
        assert elegir_escudo(resp, "Ferencvaros") is None
        resp_futbol = _respuesta(
            ("Ferencvárosi TC", "Hungarian association football club",
             "https://upload.wikimedia.org/wikipedia/en/8/8e/Ferencvarosi_TC_logo.svg"),
        )
        assert elegir_escudo(resp_futbol, "Ferencvaros").endswith("Ferencvarosi_TC_logo.svg")

    def test_prefijo_corto_no_cuenta(self):
        resp = _respuesta(("FC Ajax Cape Town", "South African football club", "https://upload.wikimedia.org/x/Ajax_CT_logo.svg"))
        assert elegir_escudo(resp, "Aja") is None


class TestWikidata:
    def test_url_commons_por_md5(self):
        # Dato público: Example.jpg vive en /a/a9/ de Commons.
        assert url_commons("Example.jpg") == "https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg"

    def test_url_commons_espacios_a_guiones_bajos(self):
        assert url_commons("SK Brann logo.svg").endswith("/SK_Brann_logo.svg")

    def test_logo_de_wikidata(self):
        resp = {"claims": {"P154": [{"mainsnak": {"datavalue": {"value": "SK Brann logo.svg"}}}]}}
        assert logo_de_wikidata(resp) == "SK Brann logo.svg"
        assert logo_de_wikidata({"claims": {}}) is None

    def test_buscador_cae_a_wikidata_si_la_pagina_no_tiene_imagen(self):
        from backend.services.escudos_hd import BuscadorEscudosHD

        brann = _Equipo("Brann")
        sin_imagen = _respuesta(("SK Brann", "Norwegian association football club", None))
        sin_imagen["query"]["pages"]["100"]["pageprops"] = {"wikibase_item": "Q47286"}
        pedidos = []

        def wikidata(item):
            pedidos.append(item)
            return {"claims": {"P154": [{"mainsnak": {"datavalue": {"value": "SK Brann logo.svg"}}}]}}

        BuscadorEscudosHD(_SesionFalsa([brann]), consultar=lambda n: sin_imagen, consultar_wikidata=wikidata, pausa=0).run()
        assert pedidos == ["Q47286"]
        assert brann.logo_hd_url == url_commons("SK Brann logo.svg")

    def test_nombre_abreviado_se_busca_por_su_alias(self):
        from backend.services.escudos_hd import BuscadorEscudosHD

        zagreb = _Equipo("Din. Zagreb")
        buscados = []
        resp = _respuesta(("GNK Dinamo Zagreb", "Croatian association football club",
                           "https://upload.wikimedia.org/wikipedia/commons/1/1b/NK_Dinamo_Zagreb.svg"))

        def consultar(n):
            buscados.append(n)
            return resp

        BuscadorEscudosHD(_SesionFalsa([zagreb]), consultar=consultar, pausa=0).run()
        assert buscados == ["Dinamo Zagreb"]
        assert zagreb.logo_hd_url.endswith("NK_Dinamo_Zagreb.svg")
