"""Tabla usuarios MVP y combinadas guardadas por usuario.

Revision ID: aa11bb22cc33
Revises: f8a2c1eeb9aa
Create Date: 2026-05-13

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "aa11bb22cc33"
down_revision: str | None = "f8a2c1eeb9aa"
branch_labels = None
depends_on = None

# PBKDF2-SHA256(120k) password "1234" con sal fija (MVP desarrollo — cambiar en producción).
_ADMIN_HASH = (
    "betsai_pw_v1_admin:"
    "86dae23f99baa1942c0d9c54c45a4dd08af2b8e1a458801fb7f2eff21d7fab1d"
)


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "saved_parlays",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("combined_odds", sa.Numeric(14, 4), nullable=False),
        sa.Column("model_joint_probability", sa.Numeric(16, 8), nullable=True),
        sa.Column("legs_snapshot", sa.JSON(), nullable=False),
        sa.Column("grading_snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "overall_result",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("last_graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(("user_id",), ["app_users.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_saved_parlays_user_id"), "saved_parlays", ["user_id"], unique=False)

    op.get_bind().execute(
        sa.text("INSERT INTO app_users (username, password_hash) VALUES (:u, :ph)"),
        {"u": "admin", "ph": _ADMIN_HASH},
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_saved_parlays_user_id"), table_name="saved_parlays")
    op.drop_table("saved_parlays")
    op.drop_table("app_users")
