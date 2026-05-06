"""Lista los CalibrationReport actuales en BD."""
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
            select(CalibrationReport).order_by(CalibrationReport.id)
        ).all()
        if not rows:
            print("(no hay calibration reports)")
            return 0
        print(f"{'id':>3}  {'created_at':<26s}  {'n':>5}  {'note':<40s}")
        print("-" * 80)
        for r in rows:
            print(f"{r.id:>3}  {r.created_at!s:<26s}  {r.n_samples:>5}  {(r.note or '—'):<40s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
