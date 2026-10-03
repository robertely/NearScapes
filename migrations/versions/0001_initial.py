"""Initial schema."""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_recordings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("waveform_path", sa.Text()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("sample_rate", sa.Integer()),
        sa.Column("channels", sa.Integer()),
        sa.Column("codec", sa.String(64)),
        sa.Column("embedded_metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sha256"),
    )
    op.create_index("ix_source_recordings_sha256", "source_recordings", ["sha256"])
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("source_recordings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analyzer", sa.String(128), nullable=False),
        sa.Column("analyzer_version", sa.String(64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_analysis_runs_source_id", "analysis_runs", ["source_id"])
    op.create_index("ix_analysis_runs_analyzer", "analysis_runs", ["analyzer"])
    op.create_table(
        "events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("text", sa.Text()),
        sa.Column("frequency_low_hz", sa.Float()),
        sa.Column("frequency_high_hz", sa.Float()),
        sa.Column("attributes", sa.JSON(), nullable=False),
    )
    op.create_index("ix_events_run_id", "events", ["run_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("source_id", sa.String(36)),
        sa.Column("run_id", sa.String(36)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_jobs_source_id", "jobs", ["source_id"])
    op.create_index("ix_jobs_run_id", "jobs", ["run_id"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("events")
    op.drop_table("analysis_runs")
    op.drop_table("source_recordings")
