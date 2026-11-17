"""Servidor MCP local (stdio, solo lectura) para chatear con los datos de Bets AI.

Se arranca con ``python -m backend.mcp_server`` (lo lanza Claude Code a partir de ``.mcp.json``).
Las herramientas leen la BD con los ``services/`` que ya usa la API, en sesiones de solo lectura,
y devuelven respuestas compactas con las cifras ya formateadas. No hay escritura ni red.

Módulos:

* ``formato``: funciones puras de formateo, recorte y saneado (con tests).
* ``sesion``: sesión de BD de corta duración en una conexión de solo lectura.
* ``estadisticas``: equipos, jugadores, partidos jugados, clasificación, bajas y búsquedas.
* ``cuotas``: estado de los datos, partidos, cuotas con valor, previa, picks y apuestas.
* ``servidor``: registro de las herramientas en ``MCPServer`` y arranque por stdio.
"""
