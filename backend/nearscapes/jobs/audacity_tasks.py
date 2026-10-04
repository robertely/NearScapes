import dramatiq

from nearscapes.exports.audacity_project import export_audacity_impl
from nearscapes.jobs.broker import broker  # noqa: F401


@dramatiq.actor(
    queue_name="audacity",
    max_retries=0,
    time_limit=2 * 60 * 60 * 1000,
)
def export_audacity_project(job_id: str, source_id: str) -> None:
    export_audacity_impl(job_id, source_id)
