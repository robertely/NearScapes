import dramatiq

from nearscapes.jobs.broker import broker  # noqa: F401
from nearscapes.jobs.work import execute_analysis_impl, ingest_source_impl


@dramatiq.actor(queue_name="analysis", max_retries=1, time_limit=60 * 60 * 1000)
def ingest_source(job_id: str, source_id: str) -> None:
    ingest_source_impl(job_id, source_id)


@dramatiq.actor(queue_name="analysis", max_retries=1, time_limit=60 * 60 * 1000)
def execute_analysis(job_id: str, run_id: str) -> None:
    execute_analysis_impl(job_id, run_id)
