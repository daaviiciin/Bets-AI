"""Limpia un CalibrationReport con note erróneo del intento previo."""
from __future__ import annotations

from sqlalchemy import select

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import CalibrationReport


def main() -> int:
    force_utf8_stdio()
    SF = get_session_factory()
    with SF() as s:
        rows = s.scalars(
            select(CalibrationReport)
            .where(CalibrationReport.note.like("backtest_Ligue%"))
        ).all()
        for r in rows:
            print(f"Borrando id={r.id} note={r.note!r}")
            s.delete(r)
        s.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
