"""Streamlit UI: scrape AI training jobs, review/edit annotations, export JSON."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

import streamlit as st

from live_jobs import (
    DEFAULT_KEYWORDS,
    fetch_live_ai_jobs,
    keywords_to_text,
    matches_keywords,
    parse_keywords,
)
from models import JobAnnotation
from scraper import scrape_many
from storage import JobStore

st.set_page_config(
    page_title="AI Job Scraper",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


def get_store() -> JobStore:
    if "store" not in st.session_state:
        st.session_state.store = JobStore()
    return st.session_state.store


def refresh_jobs() -> None:
    st.session_state.jobs = get_store().list_jobs()
    jobs = st.session_state.jobs
    if not jobs:
        st.session_state.job_index = 0
        st.session_state.review_done = False
        st.session_state.selected_job_id = None
        return

    # Keep index in range unless the user finished the review pass.
    if not st.session_state.get("review_done"):
        st.session_state.job_index = min(
            max(0, st.session_state.get("job_index", 0)),
            len(jobs) - 1,
        )
        st.session_state.selected_job_id = jobs[st.session_state.job_index].id


def init_state() -> None:
    if "jobs" not in st.session_state:
        refresh_jobs()
    if "job_index" not in st.session_state:
        st.session_state.job_index = 0
    if "review_done" not in st.session_state:
        st.session_state.review_done = False
    if "selected_job_id" not in st.session_state:
        st.session_state.selected_job_id = None
    if "last_errors" not in st.session_state:
        st.session_state.last_errors = []
    if "keywords_input" not in st.session_state:
        st.session_state.keywords_input = keywords_to_text(DEFAULT_KEYWORDS)


def reset_review_to_start() -> None:
    st.session_state.review_done = False
    st.session_state.job_index = 0
    if st.session_state.jobs:
        st.session_state.selected_job_id = st.session_state.jobs[0].id


def go_to_index(index: int) -> None:
    jobs = st.session_state.jobs
    if not jobs:
        return
    if index < 0:
        index = 0
    if index >= len(jobs):
        st.session_state.review_done = True
        return
    st.session_state.review_done = False
    st.session_state.job_index = index
    st.session_state.selected_job_id = jobs[index].id


def export_blob(reviewed_only: bool = False) -> tuple[dict, str]:
    payload = get_store().export_json(reviewed_only=reviewed_only)
    blob = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "count": len(payload),
        "jobs": payload,
    }
    return blob, json.dumps(blob, indent=2, ensure_ascii=False)


def job_label(job) -> str:
    ann = job.annotations
    mark = "✓" if ann.reviewed else "•"
    title = ann.title or "(untitled)"
    company = ann.company or "Unknown company"
    return f"{mark} {title} — {company}"


def render_sidebar() -> None:
    st.sidebar.title("AI Job Scraper")
    st.sidebar.caption("Scrape → review with Back/Next → download JSON")

    st.sidebar.subheader("Keywords")
    st.sidebar.caption(
        "Jobs must match at least one keyword in the title, description, or tags. "
        "Edit freely — comma or newline separated."
    )
    keywords_text = st.sidebar.text_area(
        "Search keywords",
        height=140,
        key="keywords_input",
        help="Defaults cover AI training / ML roles. Add your own terms anytime.",
    )
    if st.sidebar.button("Reset keywords to defaults", use_container_width=True):
        st.session_state.keywords_input = keywords_to_text(DEFAULT_KEYWORDS)
        st.rerun()

    parsed_keywords = parse_keywords(keywords_text)
    st.sidebar.caption(f"Using {len(parsed_keywords)} keyword(s)")

    st.sidebar.divider()
    st.sidebar.subheader("Pull live jobs")
    st.sidebar.caption(
        "Fetches real postings from Remotive, RemoteOK, Arbeitnow, and Jobicy, "
        "then keeps roles that match your keywords."
    )
    live_limit = st.sidebar.slider(
        "Max jobs to import",
        min_value=25,
        max_value=200,
        value=75,
        step=25,
    )
    source_choices = st.sidebar.multiselect(
        "Sources",
        options=["remotive", "remoteok", "arbeitnow", "jobicy"],
        default=["remotive", "remoteok", "arbeitnow", "jobicy"],
    )
    fetch_live_clicked = st.sidebar.button(
        "Fetch live jobs",
        type="primary",
        use_container_width=True,
    )

    if fetch_live_clicked:
        if not source_choices:
            st.sidebar.warning("Pick at least one source.")
        elif not parsed_keywords:
            st.sidebar.warning("Add at least one keyword.")
        else:
            with st.spinner("Fetching jobs matching your keywords..."):
                results, errors, counts = fetch_live_ai_jobs(
                    limit=live_limit,
                    sources=source_choices,
                    keywords=parsed_keywords,
                )
            created, skipped = get_store().create_many_skip_duplicates(results)
            refresh_jobs()
            reset_review_to_start()
            st.session_state.last_errors = errors
            st.session_state.last_source_counts = counts
            if created:
                st.sidebar.success(
                    f"Imported {len(created)} new job(s)"
                    + (f" ({skipped} duplicates skipped)." if skipped else ".")
                )
            elif results:
                st.sidebar.info(f"No new jobs — {skipped} already in your library.")
            else:
                st.sidebar.warning("No matching jobs found for these keywords right now.")
            if counts:
                st.sidebar.caption(
                    "Matched per source: "
                    + ", ".join(f"{name}={n}" for name, n in counts.items())
                )

    if st.session_state.get("last_source_counts") and not fetch_live_clicked:
        counts = st.session_state.last_source_counts
        st.sidebar.caption(
            "Last fetch: " + ", ".join(f"{name}={n}" for name, n in counts.items())
        )

    st.sidebar.divider()
    st.sidebar.subheader("Or scrape specific URLs")
    urls_text = st.sidebar.text_area(
        "Job posting URLs (one per line)",
        height=120,
        placeholder="https://boards.greenhouse.io/...\nhttps://jobs.lever.co/...",
        help="Paste public job posting pages. Works best with Greenhouse, Lever, and pages that include JobPosting JSON-LD.",
    )
    keyword_filter_urls = st.sidebar.checkbox(
        "Only keep URL results that match keywords",
        value=True,
    )
    scrape_clicked = st.sidebar.button("Scrape URLs", use_container_width=True)

    if scrape_clicked:
        urls = [line.strip() for line in urls_text.splitlines() if line.strip()]
        if not urls:
            st.sidebar.warning("Add at least one URL.")
        else:
            with st.spinner(f"Scraping {len(urls)} URL(s)..."):
                results, errors = scrape_many(urls, ai_only=False)
            if keyword_filter_urls:
                kept = []
                for ann in results:
                    if matches_keywords(
                        ann.title,
                        f"{ann.description}\n{ann.raw_text}",
                        ann.skills.split(", ") if ann.skills else [],
                        parsed_keywords,
                    ):
                        kept.append(ann)
                    else:
                        errors.append(
                            {
                                "url": ann.source_url,
                                "error": "Scraped, but did not match your keywords.",
                            }
                        )
                results = kept
            if results:
                created, skipped = get_store().create_many_skip_duplicates(results)
                refresh_jobs()
                reset_review_to_start()
                st.sidebar.success(
                    f"Saved {len(created)} job(s)"
                    + (f" ({skipped} duplicates skipped)." if skipped else ".")
                )
            st.session_state.last_errors = errors
            if errors:
                st.sidebar.warning(f"{len(errors)} URL(s) failed or filtered.")

    if st.session_state.last_errors:
        with st.sidebar.expander("Fetch / scrape issues", expanded=False):
            for err in st.session_state.last_errors:
                st.markdown(f"- `{err['url']}`  \n  {err['error']}")

    st.sidebar.divider()
    st.sidebar.subheader("Library")
    jobs = st.session_state.jobs
    if not jobs:
        st.sidebar.info("No jobs yet. Set keywords and click **Fetch live jobs**.")
        return

    st.sidebar.caption(f"{len(jobs)} job(s) loaded")
    jump = st.sidebar.number_input(
        "Jump to job #",
        min_value=1,
        max_value=len(jobs),
        value=min(st.session_state.job_index + 1, len(jobs)),
        step=1,
    )
    if st.sidebar.button("Go", use_container_width=True):
        go_to_index(int(jump) - 1)
        st.rerun()

    if st.sidebar.button("Restart review from first job", use_container_width=True):
        reset_review_to_start()
        st.rerun()

    if st.sidebar.button("Delete current job", use_container_width=True):
        current_id = st.session_state.selected_job_id
        if current_id:
            get_store().delete(current_id)
            refresh_jobs()
            if st.session_state.jobs:
                go_to_index(min(st.session_state.job_index, len(st.session_state.jobs) - 1))
            else:
                reset_review_to_start()
            st.rerun()

    if st.sidebar.button("Clear all jobs", use_container_width=True):
        get_store().clear()
        refresh_jobs()
        reset_review_to_start()
        st.rerun()


def render_nav(jobs: list, index: int, *, where: str) -> None:
    total = len(jobs)
    is_last = index >= total - 1
    progress = (index + 1) / total if total else 0

    st.progress(progress, text=f"Job {index + 1} of {total}")

    back_col, status_col, next_col = st.columns([1, 2, 1])
    with back_col:
        back_clicked = st.button(
            "← Back",
            use_container_width=True,
            disabled=index <= 0,
            key=f"back-{where}-{index}",
        )
    with status_col:
        ann = jobs[index].annotations
        mark = "reviewed" if ann.reviewed else "not reviewed"
        st.markdown(
            f"<div style='text-align:center;padding-top:0.45rem;color:#5b6575;'>"
            f"{html.escape(ann.title or 'Untitled')} · {mark}"
            f"</div>",
            unsafe_allow_html=True,
        )
    with next_col:
        next_label = "Finish →" if is_last else "Next →"
        next_clicked = st.button(
            next_label,
            type="primary",
            use_container_width=True,
            key=f"next-{where}-{index}",
        )

    if back_clicked:
        go_to_index(index - 1)
        st.rerun()
    if next_clicked:
        go_to_index(index + 1)
        st.rerun()


def render_job_preview(ann: JobAnnotation) -> None:
    """Show a readable preview of the original job posting."""
    st.markdown("#### Job preview")
    st.caption("Original posting content — use this to check the annotations on the right.")

    chips = []
    if ann.company:
        chips.append(ann.company)
    if ann.location:
        chips.append(ann.location)
    if ann.remote:
        chips.append(ann.remote)
    if ann.employment_type:
        chips.append(ann.employment_type)
    if ann.salary:
        chips.append(ann.salary)

    preview_title = html.escape(ann.title or "Untitled job")
    meta_line = html.escape(" · ".join(chips) if chips else "No location / company metadata")

    body = (ann.raw_text or ann.description or "").strip()
    if not body:
        body = "No job body was captured for this posting."

    preview_body = body if len(body) <= 6000 else body[:6000] + "\n\n…(truncated)"
    preview_body = html.escape(preview_body)

    st.markdown(
        f"""
<div style="
  border: 1px solid #d9dde3;
  border-radius: 12px;
  padding: 1.1rem 1.25rem;
  background: linear-gradient(180deg, #fbfcfe 0%, #f4f6f9 100%);
  max-height: 70vh;
  overflow-y: auto;
">
  <div style="font-size: 1.25rem; font-weight: 700; color: #152033; line-height: 1.3; margin-bottom: 0.35rem;">
    {preview_title}
  </div>
  <div style="color: #5b6575; font-size: 0.92rem; margin-bottom: 0.9rem;">
    {meta_line}
  </div>
  <div style="white-space: pre-wrap; color: #243044; font-size: 0.95rem; line-height: 1.55;">
{preview_body}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    if ann.source_url:
        st.link_button("Open original posting", ann.source_url, use_container_width=True)

    if ann.skills:
        st.caption(f"Detected skills: {ann.skills}")


def render_editor(job) -> None:
    ann = job.annotations
    st.markdown(f"### {ann.title or 'Untitled job'}")
    meta_cols = st.columns(4)
    meta_cols[0].metric("Company", ann.company or "—")
    meta_cols[1].metric("Location", ann.location or "—")
    meta_cols[2].metric("Remote", ann.remote or "—")
    meta_cols[3].metric("Reviewed", "Yes" if ann.reviewed else "No")

    st.caption(
        "Compare the job preview with the extracted fields. Edit anything wrong, then save and continue."
    )

    preview_col, edit_col = st.columns([1.15, 1], gap="large")

    with preview_col:
        render_job_preview(ann)

    with edit_col:
        st.markdown("#### Annotations")
        with st.form(f"edit-{job.id}", clear_on_submit=False):
            title = st.text_input("Title", value=ann.title)
            company = st.text_input("Company", value=ann.company)
            location = st.text_input("Location", value=ann.location)
            employment_type = st.text_input("Employment type", value=ann.employment_type)
            remote = st.selectbox(
                "Remote / on-site",
                options=["", "Remote", "Hybrid", "On-site"],
                index=["", "Remote", "Hybrid", "On-site"].index(ann.remote)
                if ann.remote in ("", "Remote", "Hybrid", "On-site")
                else 0,
            )
            salary = st.text_input("Salary", value=ann.salary)
            skills = st.text_input("Skills (comma-separated)", value=ann.skills)
            requirements = st.text_area("Requirements", value=ann.requirements, height=110)
            description = st.text_area("Description (editable summary)", value=ann.description, height=140)
            notes = st.text_area("Reviewer notes", value=ann.notes, height=70)
            reviewed = st.checkbox("Mark as reviewed / correct", value=True)
            source_url = st.text_input("Source URL", value=ann.source_url)
            save_next = st.form_submit_button(
                "Save & Next →",
                type="primary",
                use_container_width=True,
            )
            save_only = st.form_submit_button("Save only", use_container_width=True)

        if save_next or save_only:
            updated = JobAnnotation(
                title=title.strip(),
                company=company.strip(),
                location=location.strip(),
                employment_type=employment_type.strip(),
                remote=remote,
                salary=salary.strip(),
                skills=skills.strip(),
                requirements=requirements.strip(),
                description=description.strip(),
                source_url=source_url.strip(),
                scraped_at=ann.scraped_at or datetime.now(timezone.utc).isoformat(),
                raw_text=ann.raw_text,
                notes=notes.strip(),
                reviewed=reviewed,
            )
            get_store().update_annotations(job.id, updated)
            refresh_jobs()
            if save_next:
                go_to_index(st.session_state.job_index + 1)
            st.rerun()


def render_finished(jobs: list) -> None:
    st.success(f"You're done reviewing all {len(jobs)} job(s).")
    st.write("Download everything as JSON, or go back to keep editing.")

    blob, pretty = export_blob(reviewed_only=False)
    reviewed_blob, reviewed_pretty = export_blob(reviewed_only=True)

    st.download_button(
        label=f"Download all jobs as JSON ({blob['count']})",
        data=pretty,
        file_name=f"ai-jobs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json",
        mime="application/json",
        type="primary",
        use_container_width=True,
    )
    st.download_button(
        label=f"Download reviewed-only JSON ({reviewed_blob['count']})",
        data=reviewed_pretty,
        file_name=f"ai-jobs-reviewed-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json",
        mime="application/json",
        use_container_width=True,
    )

    cols = st.columns(2)
    with cols[0]:
        if st.button("← Back to last job", use_container_width=True):
            go_to_index(len(jobs) - 1)
            st.rerun()
    with cols[1]:
        if st.button("Restart from first job", use_container_width=True):
            reset_review_to_start()
            st.rerun()

    with st.expander("Preview full JSON", expanded=False):
        st.code(pretty, language="json")


def main() -> None:
    init_state()
    render_sidebar()

    st.title("AI training job annotations")
    st.write(
        "Set keywords in the sidebar, fetch matching jobs, then review with **Back** / **Next**. "
        "At the end you’ll get a JSON download of everything."
    )

    jobs = st.session_state.jobs
    if not jobs:
        st.info("No scraped jobs yet. Set keywords and click **Fetch live jobs** in the sidebar.")
        return

    if st.session_state.review_done:
        render_finished(jobs)
        return

    index = min(st.session_state.job_index, len(jobs) - 1)
    st.session_state.job_index = index
    job = jobs[index]

    render_nav(jobs, index, where="top")
    st.divider()
    render_editor(job)
    st.divider()
    render_nav(jobs, index, where="bottom")


if __name__ == "__main__":
    main()
