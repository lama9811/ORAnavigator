#!/usr/bin/env python3
"""Generate the ORIGINAL sample-proposal PDFs hosted by the Sample Proposals
Library (the /sample-proposals page).

These are written from scratch by us -- they are NOT real third-party proposals
-- so they carry zero copyright exposure and can be downloaded and read offline.
They are realistic enough to show a first-time PI how each required section reads.

Outputs (committed to the repo so they ship in the backend Docker image):
    backend/sample_proposals/nsf-ej-idss-planning-proposal.pdf
    backend/sample_proposals/nih-specific-aims-research-strategy.pdf
    backend/sample_proposals/budget-justification-example.pdf

Run:  python3 scripts/make_sample_proposals.py
Requires: reportlab (dev-only; the backend serves the pre-generated files).
"""
import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

# backend/sample_proposals/ -- the directory the backend serves from.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_ROOT, "backend", "sample_proposals")
os.makedirs(OUT_DIR, exist_ok=True)

_ss = getSampleStyleSheet()
TITLE = ParagraphStyle("title", parent=_ss["Title"], fontSize=15, leading=19, spaceAfter=6)
SUB = ParagraphStyle("sub", parent=_ss["Normal"], fontSize=10, alignment=TA_CENTER,
                     textColor="#333333", spaceAfter=2)
H1 = ParagraphStyle("h1", parent=_ss["Heading1"], fontSize=13, spaceBefore=14, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=_ss["Heading2"], fontSize=11, spaceBefore=8, spaceAfter=3)
BODY = ParagraphStyle("body", parent=_ss["Normal"], fontSize=10.5, leading=14,
                      alignment=TA_JUSTIFY, spaceAfter=6)
NOTE = ParagraphStyle("note", parent=_ss["Normal"], fontSize=8.5, leading=11,
                      textColor="#666666", alignment=TA_CENTER, spaceAfter=8)


def _build(filename, flow, title):
    path = os.path.join(OUT_DIR, filename)
    doc = SimpleDocTemplate(path, pagesize=letter,
                            leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.85 * inch, bottomMargin=0.85 * inch,
                            title=title)
    doc.build(flow)
    print("WROTE", path)


def _disclaimer():
    return Paragraph(
        "Illustrative sample authored by ORA Navigator for reference only — not a "
        "real submitted proposal. Adapt freely; verify all rules against the live "
        "solicitation and Morgan State ORA (443-885-4044).", NOTE)


# ===========================================================================
# 1. NSF full planning proposal (EJ-IDSS)
# ===========================================================================
def nsf_proposal():
    S = []
    P = lambda t, st=BODY: S.append(Paragraph(t, st))
    P("Planning a National Integrated Data Service for Environmental-Justice Research (EJ-IDSS)", TITLE)
    P("Submitted to the U.S. National Science Foundation", SUB)
    P("Program Solicitation NSF 26-509 — Integrated Data Systems &amp; Services (IDSS)", SUB)
    P("Category III: Planning Grant &nbsp;|&nbsp; Requested: $499,500 &nbsp;|&nbsp; Duration: 24 months", SUB)
    P("Lead Institution: Morgan State University &nbsp;|&nbsp; PI: Dr. A. Researcher, Department of Computer Science", SUB)
    S.append(Spacer(1, 6))
    S.append(_disclaimer())

    P("Project Summary", H1)
    P("<b>Overview.</b> This two-year planning project will define the science requirements, "
      "federated technical architecture, and governance model for a national-scale Integrated "
      "Data Service for Environmental-Justice research (EJ-IDSS). Environmental-justice science "
      "requires linking environmental-exposure, public-health, and socioeconomic data that today "
      "live in separate, incompatible repositories maintained by federal agencies, states, "
      "universities, and community organizations. No national-scale, operations-quality "
      "cyberinfrastructure currently federates these data for the environmental-justice research "
      "community. Through structured stakeholder engagement, a landscape analysis of existing "
      "repositories, and a focused technical prototyping effort, the project will produce a "
      "reference architecture, a cross-domain metadata schema, a data-governance and "
      "community-consent framework, and an operations and sustainability plan that together "
      "prepare a future full-scale IDSS deployment. The effort will federate&mdash;not "
      "duplicate&mdash;existing repositories and will align with NSF's prior cyberinfrastructure "
      "investments (CSSI, CC*).")
    P("<b>Intellectual Merit.</b> The project advances data cyberinfrastructure by developing "
      "shared, reusable standards for linking heterogeneous environmental-exposure and "
      "community-health data at national scale&mdash;a transdisciplinary problem that no single "
      "existing repository solves. The planning effort produces three transferable artifacts: a "
      "reference architecture for federating geographically and administratively distributed "
      "datasets; a cross-domain metadata schema that maps exposure, health, and socioeconomic "
      "vocabularies to community standards (DataCite, schema.org/Dataset, OGC); and a governance "
      "model for sensitive, community-contributed data.")
    P("<b>Broader Impacts.</b> Led by Morgan State University, a historically Black university, "
      "the project trains graduate and undergraduate students from groups underrepresented in "
      "computing and data science in modern data cyberinfrastructure and community-engaged "
      "research. It broadens participation by partnering with community organizations in "
      "overburdened neighborhoods so residents help shape the data service rather than serving as "
      "passive subjects, and by opening its planning workshops to regional minority-serving "
      "institutions. A formal evaluation plan, led by an external evaluator, will measure training "
      "outcomes, partner engagement, and the usability of the proposed architecture against "
      "pre-defined targets.")

    P("Project Description", H1)
    P("1. Vision, Goals, and Driving Requirements", H2)
    P("Our vision is a national, operations-quality data service that lets any researcher discover, "
      "access, and responsibly combine environmental-exposure, health, and socioeconomic data to "
      "study environmental injustice and its remedies. This planning project does not build that "
      "operational system; it produces the validated requirements, architecture, and plan needed to "
      "propose one. We pursue three concrete goals: (G1) define the driving science requirements "
      "with researchers and affected communities; (G2) specify a federated technical architecture "
      "and concept of operations; and (G3) produce an operations, governance, and sustainability "
      "plan suitable for a future Category I or II IDSS proposal. The driving requirements are "
      "explicit and measurable: the future service must federate at least five major external "
      "repositories without duplicating them; return cross-domain queries over exposure and health "
      "data in under five seconds for common requests; and enforce community-consent and "
      "data-sovereignty rules at the dataset level.")
    P("2. Background and Significance", H2)
    P("Environmental-justice research is constrained today because the data it depends on are "
      "fragmented. Air- and water-quality monitoring data sit in federal and state environmental "
      "systems; health outcomes sit in public-health repositories with strict access controls; and "
      "the socioeconomic context lives in yet other census and administrative datasets. Each uses "
      "different identifiers, geographies, time scales, and metadata, and linking them today is "
      "slow, manual, and rarely reproducible. Prior NSF investments (CSSI, CC*) built valuable "
      "capacity at campus and regional scales, but no national-scale, operations-quality service "
      "federates these data for the environmental-justice community. Closing this gap is "
      "significant: credible findings depend on linking exposure to outcomes at the neighborhood "
      "scale, and a shared national service would make that analysis reproducible and auditable.")
    P("3. Approach and Planning Activities", H2)
    P("We will execute four interlocking activities. <b>(A) Stakeholder requirements.</b> Three "
      "structured workshops&mdash;researchers, data-provider agencies, and community "
      "organizations&mdash;using a documented elicitation protocol to produce a prioritized "
      "requirements register. <b>(B) Landscape analysis.</b> Inventory the major candidate "
      "repositories, documenting access models, metadata, identifiers, licensing, and governance "
      "constraints. <b>(C) Technical prototyping.</b> A narrow proof-of-concept that maps and links "
      "two representative datasets (an exposure dataset and a health-outcomes dataset) through a "
      "candidate metadata schema, to test feasibility&mdash;not to deploy a service. <b>(D) "
      "Governance design.</b> A data-governance and community-consent framework for sensitive and "
      "community-contributed data.")
    P("4. Evaluation and Expected Outcomes", H2)
    P("Success is defined by measurable deliverables and independent evaluation: a prioritized "
      "requirements register validated by at least 20 stakeholders; a landscape report covering at "
      "least eight repositories; a working prototype that links the two sample datasets and answers "
      "three benchmark cross-domain queries; a reference architecture and concept of operations; and "
      "a governance framework reviewed by community partners. An external evaluator assesses each "
      "deliverable against these targets and measures student-training and partner-engagement "
      "outcomes, reporting formatively at the midpoint and summatively at the end.")
    P("5. Project Management", H2)
    P("The PI (Computer Science) leads coordination and the technical prototype; a co-PI in Public "
      "Health leads health-data requirements and governance; and a community-partnerships "
      "coordinator leads the workshops. The 24-month timeline has four milestones: <b>M1 (1&ndash;6)</b> "
      "workshops complete and requirements register drafted; <b>M2 (7&ndash;12)</b> landscape analysis "
      "and metadata schema drafted; <b>M3 (13&ndash;18)</b> prototype linking two datasets "
      "demonstrated; <b>M4 (19&ndash;24)</b> reference architecture, governance framework, and "
      "sustainability plan delivered.")

    P("References Cited", H1)
    P("[1] National Science Foundation. <i>Proposal &amp; Award Policies &amp; Procedures Guide "
      "(PAPPG)</i>. Current edition.")
    P("[2] National Academies of Sciences, Engineering, and Medicine. <i>Reproducibility and "
      "Replicability in Science</i>. The National Academies Press, 2019.")
    P("[3] U.S. EPA. <i>EJScreen: Environmental Justice Screening and Mapping Tool</i>. Technical "
      "documentation.")
    P("[4] Wilkinson, M. et al. “The FAIR Guiding Principles for scientific data management and "
      "stewardship.” <i>Scientific Data</i> 3, 160018 (2016).")

    P("Data Management and Sharing Plan", H1)
    P("<b>Types of data.</b> Requirements registers, a repository landscape inventory, a candidate "
      "metadata schema, de-identified workshop notes, prototype code, and design documents. The "
      "prototype links two small, already-public sample datasets; the project collects no new "
      "human-subjects data.")
    P("<b>Standards and formats.</b> Open formats (PDF/A, CSV, JSON); metadata follows DataCite and "
      "schema.org/Dataset, with geospatial metadata following OGC conventions.")
    P("<b>Storage and security.</b> Artifacts are stored on Morgan State's managed research storage "
      "with automated backup; sensitive material is de-identified and access-controlled.")
    P("<b>Sharing and access.</b> Reports, the metadata schema, and prototype code are deposited in "
      "a public repository (Zenodo) under open licenses (CC-BY for documents, Apache-2.0 for code) "
      "with DOIs at each milestone, not merely “available on request.”")
    P("<b>Retention.</b> Shared artifacts are retained for at least five years after the award, "
      "consistent with NSF policy and Morgan State's records schedule.")

    P("Budget Justification (Summary)", H1)
    P("<b>Senior personnel.</b> PI (Computer Science) and co-PI (Public Health) at partial "
      "academic-year and summer effort. <b>Other personnel.</b> Two graduate research assistants "
      "support the landscape analysis, prototype, and workshop logistics. <b>Fringe</b> is applied "
      "at Morgan State's negotiated rates. <b>Travel</b> supports three stakeholder workshops and one "
      "NSF PI meeting. <b>Materials and other direct costs</b> cover modest cloud prototyping "
      "resources, participant-support and workshop costs, and external evaluation. <b>Indirect costs "
      "(F&amp;A)</b> are applied to modified total direct costs at Morgan State's federally "
      "negotiated rate. The total request remains within the $500,000 Category III ceiling; "
      "voluntary committed cost sharing is not included, as required by the solicitation.")

    _build("nsf-ej-idss-planning-proposal.pdf", S, "NSF Sample Proposal — EJ-IDSS")


# ===========================================================================
# 2. NIH Specific Aims + Research Strategy excerpt
# ===========================================================================
def nih_aims():
    S = []
    P = lambda t, st=BODY: S.append(Paragraph(t, st))
    P("NIH Sample — Specific Aims &amp; Research Strategy", TITLE)
    P("Illustrative R01-style application excerpt", SUB)
    P("PI: Dr. B. Investigator, Morgan State University &nbsp;|&nbsp; Mechanism: NIH R01 (sample)", SUB)
    S.append(Spacer(1, 6))
    S.append(_disclaimer())

    P("Specific Aims", H1)
    P("Hypertension is markedly more prevalent and less well controlled in historically "
      "underserved urban communities, yet most digital self-management tools are neither designed "
      "with these communities nor validated in them. <b>The long-term goal</b> of this research is "
      "to reduce disparities in blood-pressure control through community-designed digital health "
      "tools. <b>The overall objective</b> of this application is to develop and test a culturally "
      "tailored, mobile self-management intervention for adults with uncontrolled hypertension in "
      "Baltimore. <b>Our central hypothesis</b> is that a community-co-designed intervention will "
      "improve blood-pressure control and medication adherence relative to usual care. Guided by "
      "strong preliminary data from our community advisory board, we will test this hypothesis "
      "through three aims:")
    P("<b>Aim 1. Co-design and refine</b> the intervention with patients and community health "
      "workers. <i>Working hypothesis:</i> structured co-design yields features that map to known "
      "adherence barriers. We will use iterative design sessions and usability testing to finalize "
      "the intervention.")
    P("<b>Aim 2. Determine efficacy</b> on blood-pressure control in a randomized pilot trial "
      "(N = 200). <i>Working hypothesis:</i> the intervention reduces systolic blood pressure by "
      "≥ 6 mmHg at 6 months versus usual care. Primary outcome: change in systolic BP at 6 months.")
    P("<b>Aim 3. Identify mediators and implementation determinants</b> using mixed methods. "
      "<i>Working hypothesis:</i> medication adherence and self-efficacy mediate the BP effect, and "
      "specific clinic-level factors predict adoption.")
    P("<b>Expected outcomes and impact.</b> This work will produce a validated, community-designed "
      "intervention and the implementation knowledge needed to scale it&mdash;an expected positive "
      "impact on a condition that drives substantial, preventable cardiovascular morbidity in "
      "underserved communities.")

    P("Research Strategy", H1)
    P("Significance", H2)
    P("Uncontrolled hypertension is a leading, modifiable contributor to cardiovascular disease, and "
      "the burden falls disproportionately on the communities this project serves. Existing digital "
      "tools show promise but are rarely co-designed with or validated in these populations, "
      "limiting both efficacy and adoption. By grounding design in the community and rigorously "
      "testing efficacy, this project addresses an important problem that, if solved, would shift "
      "how self-management tools for chronic disease are developed and evaluated.")
    P("Innovation", H2)
    P("The project is innovative in three ways: it applies a structured community co-design method "
      "to a chronic-disease self-management tool rather than retrofitting an existing app; it pairs "
      "an efficacy trial with an embedded implementation-science evaluation so that adoption is "
      "studied from the start; and it centers community health workers as designers and deliverers, "
      "not just recruiters.")
    P("Approach", H2)
    P("<b>Aim 1 (co-design).</b> We will conduct six iterative design sessions with patients and "
      "community health workers, followed by think-aloud usability testing with 15 participants, "
      "refining the intervention to a locked design. <b>Aim 2 (pilot RCT).</b> We will randomize "
      "200 adults with uncontrolled hypertension 1:1 to the intervention or usual care, measuring "
      "blood pressure with validated automated devices at baseline, 3, and 6 months; the primary "
      "analysis is an intention-to-treat comparison of 6-month systolic BP change. <b>Aim 3 "
      "(mediators and implementation).</b> Pre-specified mediation analyses will test adherence and "
      "self-efficacy as mediators, and qualitative interviews with clinic staff, analyzed with an "
      "implementation framework, will identify adoption determinants. <b>Potential pitfalls and "
      "alternatives.</b> If recruitment lags, we will add a second clinic site already engaged with "
      "our team; if adherence measurement by self-report proves unreliable, we will incorporate "
      "pharmacy-refill data.")

    _build("nih-specific-aims-research-strategy.pdf", S, "NIH Sample — Specific Aims & Research Strategy")


# ===========================================================================
# 3. Budget Justification (annotated)
# ===========================================================================
def budget_justification():
    S = []
    P = lambda t, st=BODY: S.append(Paragraph(t, st))
    P("Budget Justification — Annotated Sample", TITLE)
    P("Illustrative federal proposal budget justification", SUB)
    P("Morgan State University &nbsp;|&nbsp; 3-year project &nbsp;|&nbsp; rates shown are illustrative", SUB)
    S.append(Spacer(1, 6))
    S.append(_disclaimer())

    P("A. Senior Personnel", H1)
    P("<b>Dr. A. Researcher, Principal Investigator (1.0 summer month/yr).</b> The PI conceived the "
      "project and is responsible for overall scientific direction, supervision of personnel, and "
      "reporting. One summer month is requested in each of the three years, charged at the PI's "
      "institutional base salary with annual escalation of 3%. Effort is committed and will be "
      "tracked through Morgan State's effort-certification system.")
    P("<b>Dr. C. Collaborator, Co-Investigator (0.5 summer month/yr).</b> The co-I leads the "
      "domain-specific analyses described in Aim 2 and contributes to manuscript preparation.")

    P("B. Other Personnel", H1)
    P("<b>Graduate Research Assistant (1 GRA, 12 months/yr at 50% effort).</b> The GRA conducts data "
      "collection, runs the analysis pipeline, and assists with dissemination. The stipend follows "
      "the institution's standard GRA rate; effort is 50% during the academic year consistent with "
      "graduate-assistantship policy.")
    P("<b>Undergraduate Research Assistants (2, academic year).</b> Two undergraduates support data "
      "preparation and literature review, advancing the project's broader-impacts training goals.")

    P("C. Fringe Benefits", H1)
    P("Fringe benefits are applied to all salaries and wages at Morgan State's federally negotiated "
      "rates (faculty and staff at the negotiated full-time rate; student rates as applicable). "
      "Fringe is computed on committed salary, not on the total budget, and follows the rates in "
      "effect during each budget period.")

    P("D. Travel", H1)
    P("<b>Domestic conference travel ($2,500/yr).</b> Supports one trip per year for the PI or GRA "
      "to present results at a national meeting (airfare, lodging, registration, and per diem at "
      "institutional rates). <b>Project travel ($1,000/yr)</b> supports local travel to the field "
      "site for data collection.")

    P("E. Participant Support Costs", H1)
    P("<b>Stipends and incentives ($6,000/yr).</b> Supports participant incentives for the study "
      "activities described in the project plan. Per sponsor policy, participant-support costs are "
      "budgeted as a separate category and are <b>excluded from the modified total direct cost "
      "(MTDC) base</b>, so no indirect costs are charged on them.")

    P("F. Materials, Supplies, and Other Direct Costs", H1)
    P("<b>Computing and software ($3,000/yr)</b> covers cloud compute for the analysis pipeline and "
      "required software licenses. <b>Publication costs ($1,500 in Years 2–3)</b> cover open-access "
      "article fees. <b>Materials and supplies ($1,200/yr)</b> cover consumables directly used by "
      "the project.")

    P("G. Indirect Costs (Facilities &amp; Administrative)", H1)
    P("Indirect costs are charged at Morgan State University's federally negotiated F&amp;A rate, "
      "applied to the <b>modified total direct cost (MTDC)</b> base. MTDC excludes equipment, "
      "participant-support costs, and the portion of each subaward above $25,000, consistent with "
      "2 CFR 200. The applicable rate and rate-agreement date are listed in the budget form; no "
      "indirect costs are requested on the excluded categories above.")
    P("<b>Total.</b> The total requested budget is the sum of direct and indirect costs across the "
      "three budget periods; all figures reconcile to the line items on the sponsor budget form.")

    _build("budget-justification-example.pdf", S, "Budget Justification — Annotated Sample")


if __name__ == "__main__":
    nsf_proposal()
    nih_aims()
    budget_justification()
    print("\nAll sample PDFs written to", OUT_DIR)
