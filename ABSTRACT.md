# Evidence Horizon — MedTech AI & Data Science Showcase Abstract

**Working title:** Evidence Horizon: An Excel-Governed, Multi-Domain Literature Intelligence Platform for Strategic Medical Affairs  
**Team / owner:** Strategic Medical Affairs · J&J MedTech China (JJMC)  
**Structure:** Background · Aim · Method · Results · Discussion and Future Work  
**Word limit:** &lt;500

---

## Abstract (submission text)

**Background.** Strategic Medical Affairs (SMA) at J&J MedTech China must convert emerging clinical literature into decisions that support new product registration, product lifecycle management, commercial prioritization, and competitive landscape assessment across franchises such as Electrophysiology (EP), Neurovascular (NV), and Surgery. Manual PubMed searching with ad-hoc keywords is slow, incomplete across therapy areas, and hard to keep aligned as portfolios and competitors change. SMA therefore needs an automated, Medical Affairs–owned evidence pipeline rather than one-off searches maintained only by engineers.

**Aim.** We aimed to build Evidence Horizon—a multi-domain literature intelligence platform that (1) lets Medical Affairs govern surveillance scope through an editable Excel playbook, (2) retrieves and attributes PubMed evidence for both J&J and competitor companies, and (3) delivers a clear, branded digest and durable article library that SMA can use for registration, lifecycle, commercial, and landscape workflows.

**Method.** Evidence Horizon centers on an Excel playbook (technologies, diseases, competitor company names, optional products, and J&J portfolio terms) that Medical Affairs can download, edit, and upload via a Streamlit App Manual console (YAML/form editing retained as backup). A modular Python pipeline constructs Boolean PubMed queries (NCBI Entrez), parses full records, deduplicates in SQLite, matches companies/products including J&J, and emails a J&J News–styled digest with domain coverage, total / J&J / other-company counts, and Paper i/N cards (full abstract; 3–5 authors + et al. with first-author institute; company–product text lines). Articles are stored in a library with Excel export for later analysis and optional LLM scoring. EP, NV, and Surgery are seeded first; additional domains can be added by Medical Affairs without code changes.

**Results.** End-to-end runs confirm multi-domain query composition, PubMed retrieval, company attribution, library persistence, Excel export, and digest generation under weekly/daily schedules. The operational output is a repeatable Evidence Horizon digest and shared evidence backbone that reduces manual scanning and makes surveillance scope transparent and editable by non-engineers.

**Discussion and Future Work.** Novelty comes from treating the Medical Affairs playbook—not hardcoded keyword lists—as the source of truth, and from expanding a single-EP prototype into a reusable MedTech SMA capability that includes J&J’s own evidence alongside competitors. Technical rigor is reflected in modular search–parse–match–persist–report architecture, clear attribution rules, and separation of secrets from MA-editable content. Key learnings: company-level competitor lists are more maintainable than exhaustive product taxonomies; simpler digests (counts + Paper i/N) improve trust; and cross-functional collaboration among SMA, franchise Medical Affairs, and digital partners keeps the playbook clinically valid. Future work includes broader domain coverage, richer landscape analytics, selective LLM prioritization, additional evidence sources beyond PubMed, and tighter linkage of digest insights to registration, lifecycle, and commercial decision forums—advancing MedTech strategic priorities through an AI/data-enabled lighthouse pattern for Medical Affairs.
