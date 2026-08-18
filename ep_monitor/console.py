"""Streamlit App Manual console for Evidence Horizon.

Edit the surveillance playbook (domains, keywords, companies, recipients,
schedule), fetch PubMed articles, save to SQLite/Excel, and send digests.

Run
---
    python -m ep_monitor.console
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path when launched via `streamlit run`
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def main() -> None:
    """Launch the Streamlit console (or run page when invoked by Streamlit)."""
    # When executed as ``python -m ep_monitor.console``, spawn Streamlit.
    if Path(sys.argv[0]).name != "streamlit" and "streamlit" not in Path(sys.argv[0]).name:
        import subprocess

        script = Path(__file__).resolve()
        cmd = [sys.executable, "-m", "streamlit", "run", str(script), "--server.headless", "true"]
        raise SystemExit(subprocess.call(cmd))

    _render_app()


def _render_app() -> None:
    import streamlit as st

    from ep_monitor import config
    from ep_monitor import playbook as pb
    from ep_monitor.company_matcher import match_articles
    from ep_monitor.database import ArticleDatabase
    from ep_monitor.export_excel import articles_to_rows, export_articles_to_excel
    from ep_monitor.main_basic import _BASIC_SOURCE, _LIBRARY_DB, run_basic_pipeline
    from ep_monitor.pubmed_search import search_pubmed

    st.set_page_config(
        page_title="Evidence Horizon · App Manual",
        layout="wide",
    )

    st.markdown(
        """
        <style>
        .stApp { background: #f5f5f5; }
        h1, h2, h3 { color: #1a1a1a !important; }
        div[data-testid="stSidebar"] { background: #1a1a1a; }
        div[data-testid="stSidebar"] * { color: #f5f5f5 !important; }
        .jj-banner {
            background: #1a1a1a; color: #fff; padding: 18px 22px;
            border-top: 5px solid #c8102e; margin-bottom: 18px;
        }
        .jj-banner .brand { color: #c8102e; font-weight: 700; letter-spacing: 0.12em;
            text-transform: uppercase; font-size: 12px; }
        .jj-banner h1 { color: #fff !important; margin: 8px 0 4px 0; font-size: 28px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="jj-banner">
          <div class="brand">Evidence Horizon</div>
          <h1>App Manual Console</h1>
          <div>From Evidence to Strategic Insight · JJMC Medical Affairs</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    book = pb.load_playbook()
    tab_run, tab_playbook, tab_library = st.tabs(
        ["Run pipeline", "Playbook editor", "Article library"]
    )

    # ------------------------------------------------------------------ Run
    with tab_run:
        st.subheader("Fetch → Save → Email")
        col1, col2, col3 = st.columns(3)
        lookback = col1.number_input(
            "Lookback days",
            min_value=1,
            max_value=90,
            value=int(pb.lookback_days(book)),
        )
        force = col2.checkbox("Force (ignore email dedupe)", value=False)
        send_mail = col3.checkbox("Send email after fetch", value=True)

        st.caption(
            f"Enabled domains: "
            + ", ".join(d.get("name", d.get("id")) for d in pb.enabled_domains(book))
            or "(none)"
        )
        recipients = pb.recipient_emails(book)
        st.caption(
            "Recipients (BCC): " + (", ".join(recipients) if recipients else "(from .env EMAIL_TO)")
        )

        c1, c2, c3 = st.columns(3)
        do_preview = c1.button("1. Fetch & preview", type="primary")
        do_save = c2.button("2. Save preview to DB + Excel")
        do_full = c3.button("3. Full run (pipeline)")

        if "preview_articles" not in st.session_state:
            st.session_state.preview_articles = []

        if do_preview:
            with st.spinner("Searching PubMed…"):
                arts = search_pubmed(lookback_days=int(lookback))
                for a in arts:
                    a.source = _BASIC_SOURCE
                match_articles(
                    arts,
                    company_map=pb.company_product_map(book, include_own=True),
                    competitors_only=False,
                )
                st.session_state.preview_articles = arts
            st.success(f"Fetched {len(arts)} article(s).")

        arts = st.session_state.preview_articles
        if arts:
            rows = [
                {
                    "PMID": a.source_id,
                    "Title": a.title[:120],
                    "Companies": ", ".join(a.matched_companies) or "—",
                    "Products": ", ".join(a.matched_products) or "—",
                    "Journal": a.journal or "",
                    "Date": a.publication_date.isoformat() if a.publication_date else "",
                }
                for a in arts
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

        if do_save:
            if not arts:
                st.warning("Fetch a preview first.")
            else:
                domain_id = ",".join(d.get("id", "ep") for d in pb.enabled_domains(book)) or "ep"
                with ArticleDatabase(_LIBRARY_DB) as db:
                    db.upsert_articles(arts, domain_id=domain_id)
                path = export_articles_to_excel(
                    articles_to_rows(arts, domain_id=domain_id),
                )
                st.success(f"Saved {len(arts)} articles to library DB and {path}")

        if do_full:
            with st.spinner("Running pipeline…"):
                code = run_basic_pipeline(
                    lookback_days=int(lookback),
                    send_email_flag=bool(send_mail),
                    dry_run=False,
                    force=bool(force),
                    playbook=book,
                )
            if code == 0:
                st.success("Pipeline finished successfully.")
            else:
                st.error(f"Pipeline exited with code {code}. Check logs.")

    # ----------------------------------------------------------- Playbook
    with tab_playbook:
        from ep_monitor.excel_playbook import (
            ensure_template,
            playbook_from_excel,
            write_playbook_excel,
        )

        st.subheader("Playbook — Excel upload (recommended for MA)")
        st.caption(
            "Edit domains, keywords, and competitor company names in Excel, then upload. "
            "Product names are optional."
        )
        template_path = ensure_template()
        st.download_button(
            "Download starter Excel playbook",
            data=template_path.read_bytes(),
            file_name="evidence_horizon_playbook_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        uploaded = st.file_uploader("Upload edited playbook Excel", type=["xlsx"])
        if uploaded is not None and st.button("Apply uploaded Excel to playbook", type="primary"):
            try:
                new_book = playbook_from_excel(uploaded.getvalue())
                pb.save_playbook(new_book)
                write_playbook_excel(new_book, template_path)
                st.success("Playbook updated from Excel. Reloading…")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not import Excel: {exc}")

        st.divider()
        st.subheader("Playbook editor (advanced)")
        st.caption(f"File: `{pb.PLAYBOOK_PATH}`")

        meta = book.setdefault("meta", {})
        sched = book.setdefault("schedule", {})
        meta["product_name"] = st.text_input(
            "Product name", meta.get("product_name", "Evidence Horizon")
        )
        meta["owner"] = st.text_input("Owner", meta.get("owner", "Strategic Medical Affairs / JJMC"))
        meta["tagline"] = st.text_input(
            "Tagline", meta.get("tagline", "From Evidence to Strategic Insight")
        )

        st.markdown("#### Schedule")
        sc1, sc2, sc3, sc4 = st.columns(4)
        sched["mode"] = sc1.selectbox(
            "Mode",
            ["weekly", "daily"],
            index=0 if sched.get("mode") != "daily" else 1,
        )
        weekday_opts = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        wd = str(sched.get("weekday") or "monday").lower()
        sched["weekday"] = sc2.selectbox(
            "Weekday",
            weekday_opts,
            index=weekday_opts.index(wd) if wd in weekday_opts else 0,
        )
        sched["hour"] = int(sc3.number_input("Hour", 0, 23, int(sched.get("hour", 8))))
        sched["minute"] = int(sc4.number_input("Minute", 0, 59, int(sched.get("minute", 0))))
        sched["lookback_days"] = int(
            st.number_input("Default lookback days", 1, 90, int(sched.get("lookback_days", 7)))
        )

        st.markdown("#### Recipients (BCC)")
        recip_text = st.text_area(
            "One email per line (optional name <email> or just email)",
            value="\n".join(
                (
                    f"{r.get('name', '').strip()} <{r.get('email', '').strip()}>".strip()
                    if isinstance(r, dict) and r.get("name")
                    else (r.get("email") if isinstance(r, dict) else str(r))
                )
                for r in (book.get("recipients") or [])
                if (isinstance(r, dict) and r.get("email")) or (not isinstance(r, dict) and str(r).strip())
            ),
            height=100,
        )
        new_recipients = []
        for line in recip_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "<" in line and ">" in line:
                name = line.split("<", 1)[0].strip()
                email = line.split("<", 1)[1].split(">", 1)[0].strip()
            else:
                name, email = "", line
            if email:
                new_recipients.append({"name": name, "email": email})
        book["recipients"] = new_recipients

        st.markdown("#### Domains")
        domains = book.setdefault("domains", [])
        if not domains:
            st.info("No domains yet. Save default playbook first.")
        for i, domain in enumerate(domains):
            with st.expander(
                f"{domain.get('name', 'Domain')} ({domain.get('id', i)})",
                expanded=(i == 0),
            ):
                domain["enabled"] = st.checkbox(
                    "Enabled",
                    value=bool(domain.get("enabled", True)),
                    key=f"en_{i}",
                )
                domain["id"] = st.text_input("Domain id", domain.get("id", f"domain_{i}"), key=f"id_{i}")
                domain["name"] = st.text_input("Display name", domain.get("name", ""), key=f"name_{i}")
                domain["technologies"] = [
                    x.strip()
                    for x in st.text_area(
                        "Technologies (one per line)",
                        "\n".join(domain.get("technologies") or []),
                        key=f"tech_{i}",
                        height=120,
                    ).splitlines()
                    if x.strip()
                ]
                domain["diseases"] = [
                    x.strip()
                    for x in st.text_area(
                        "Diseases (one per line)",
                        "\n".join(domain.get("diseases") or []),
                        key=f"dis_{i}",
                        height=100,
                    ).splitlines()
                    if x.strip()
                ]
                domain["ep_topics"] = [
                    x.strip()
                    for x in st.text_area(
                        "EP topics (one per line)",
                        "\n".join(domain.get("ep_topics") or []),
                        key=f"ep_{i}",
                        height=100,
                    ).splitlines()
                    if x.strip()
                ]

                st.markdown("**Companies & products** (format: `Company | product1, product2`)")
                company_lines = []
                for row in domain.get("companies") or []:
                    if isinstance(row, dict):
                        company_lines.append(
                            f"{row.get('name', '')} | {', '.join(row.get('products') or [])}"
                        )
                edited = st.text_area(
                    "Competitor companies",
                    "\n".join(company_lines),
                    key=f"co_{i}",
                    height=140,
                )
                parsed_companies = []
                for line in edited.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if "|" in line:
                        name, prods = line.split("|", 1)
                    else:
                        name, prods = line, ""
                    parsed_companies.append(
                        {
                            "name": name.strip(),
                            "products": [p.strip() for p in prods.split(",") if p.strip()],
                        }
                    )
                domain["companies"] = parsed_companies

        if st.button("Save playbook", type="primary"):
            path = pb.save_playbook(book)
            st.success(f"Saved {path}")
            st.rerun()

        st.markdown("#### Add a new domain")
        nd1, nd2 = st.columns(2)
        new_id = nd1.text_input("New domain id", "new_domain")
        new_name = nd2.text_input("New domain name", "New Domain")
        if st.button("Add domain template"):
            domains.append(
                {
                    "id": new_id.strip() or "new_domain",
                    "name": new_name.strip() or "New Domain",
                    "enabled": False,
                    "technologies": [],
                    "diseases": [],
                    "ep_topics": [],
                    "companies": [],
                    "own_portfolio": [],
                }
            )
            pb.save_playbook(book)
            st.success("Domain template added (disabled). Edit keywords, then enable & save.")
            st.rerun()

    # ----------------------------------------------------------- Library
    with tab_library:
        st.subheader("Saved articles (SQLite library)")
        with ArticleDatabase(_LIBRARY_DB) as db:
            count = db.count_articles()
            st.metric("Articles in library", count)
            rows = db.list_articles(limit=500)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
            if st.button("Export current library to Excel"):
                path = export_articles_to_excel(rows, filename="articles_library_export.xlsx")
                st.success(f"Exported to {path}")
                st.download_button(
                    "Download Excel",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        else:
            st.info("Library is empty. Run a fetch/save or full pipeline first.")

        st.caption(f"DB: `{_LIBRARY_DB}` · Exports: `{config.EXPORTS_DIR}` · Reports: `{config.REPORTS_DIR}`")


if __name__ == "__main__":
    # Streamlit executes the script top-to-bottom; detect via runtime.
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        if get_script_run_ctx() is not None:
            _render_app()
        else:
            main()
    except Exception:
        main()
