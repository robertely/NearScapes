from nearscapes.config import get_settings


def dispatch_ingest(job_id: str, source_id: str) -> None:
    if get_settings().queue_mode == "inline":
        from nearscapes.jobs.work import ingest_source_impl

        ingest_source_impl(job_id, source_id)
        return
    from nearscapes.jobs.tasks import ingest_source

    ingest_source.send(job_id, source_id)


def dispatch_analysis(job_id: str, run_id: str) -> None:
    if get_settings().queue_mode == "inline":
        from nearscapes.jobs.work import execute_analysis_impl

        execute_analysis_impl(job_id, run_id)
        return
    from nearscapes.jobs.tasks import execute_analysis

    execute_analysis.send(job_id, run_id)


def dispatch_audacity_export(job_id: str, source_id: str) -> None:
    if get_settings().queue_mode == "inline":
        from nearscapes.exports.audacity_project import export_audacity_impl

        export_audacity_impl(job_id, source_id)
        return
    from nearscapes.jobs.audacity_tasks import export_audacity_project

    export_audacity_project.send(job_id, source_id)
