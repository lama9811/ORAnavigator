#!/usr/bin/env python3
"""Generate a realistic NSF 26-509 (IDSS) Category III planning-grant proposal
draft as a PDF, for testing ORA Navigator's Draft Critic / Drafting Coach.
Output: ~/Desktop/NSF Real Draft.pdf
"""
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak)

OUT = os.path.expanduser("~/Desktop/NSF Real Draft.pdf")

styles = getSampleStyleSheet()
title = ParagraphStyle("title", parent=styles["Title"], fontSize=15, leading=19, spaceAfter=6)
sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER,
                     textColor="#333333", spaceAfter=2)
h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=13, spaceBefore=14, spaceAfter=4)
h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, spaceBefore=8, spaceAfter=3)
body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10.5, leading=14,
                      alignment=TA_JUSTIFY, spaceAfter=6)

S = []
def P(t, st=body): S.append(Paragraph(t, st))
def gap(h=6): S.append(Spacer(1, h))

# ---- Header ---------------------------------------------------------------
P("Planning a National Integrated Data Service for Environmental-Justice Research (EJ-IDSS)", title)
P("Submitted to the U.S. National Science Foundation", sub)
P("Program Solicitation NSF 26-509 — Integrated Data Systems &amp; Services (IDSS)", sub)
P("Category III: Planning Grant &nbsp;|&nbsp; Requested: $499,500 &nbsp;|&nbsp; Duration: 24 months", sub)
P("Lead Institution: Morgan State University &nbsp;|&nbsp; PI: Dr. A. Researcher, Department of Computer Science", sub)
gap(10)

# ---- Project Summary ------------------------------------------------------
P("Project Summary", h1)
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
  "model for sensitive, community-contributed data. These outcomes are multi-disciplinary and "
  "national in scope, and are designed to be reused well beyond environmental justice.")
P("<b>Broader Impacts.</b> Led by Morgan State University, a historically Black university, "
  "the project trains graduate and undergraduate students from groups underrepresented in "
  "computing and data science in modern data cyberinfrastructure and community-engaged "
  "research. It broadens participation by partnering with community organizations in "
  "overburdened neighborhoods so residents help shape the data service rather than serving as "
  "passive subjects, and by opening its planning workshops to regional minority-serving "
  "institutions. A formal evaluation plan, led by an external evaluator, will measure training "
  "outcomes, partner engagement, and the usability of the proposed architecture against "
  "pre-defined targets.")

# ---- Project Description --------------------------------------------------
P("Project Description", h1)

P("1. Vision, Goals, and Driving Requirements", h2)
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

P("2. Project Definition and Specification &mdash; Background and Significance", h2)
P("Environmental-justice research is constrained today because the data it depends on are "
  "fragmented. Air- and water-quality monitoring data sit in federal and state environmental "
  "systems; health outcomes sit in public-health and clinical repositories with strict access "
  "controls; and the socioeconomic and demographic context lives in yet other census and "
  "administrative datasets. Each uses different identifiers, geographies, time scales, and "
  "metadata, and linking them today is slow, manual, and rarely reproducible. Prior NSF "
  "investments&mdash;Cyberinfrastructure for Sustained Scientific Innovation (CSSI) and Campus "
  "Cyberinfrastructure (CC*)&mdash;built valuable capacity at campus and regional scales, but no "
  "national-scale, operations-quality service federates these data for the environmental-justice "
  "community. Closing this gap is significant: credible environmental-justice findings depend on "
  "linking exposure to outcomes at the neighborhood scale, and a shared national service would "
  "make that analysis reproducible, auditable, and broadly accessible.")

P("3. Concept of Operations and Planning Activities &mdash; Approach", h2)
P("We will execute four interlocking activities. <b>(A) Stakeholder requirements.</b> We will "
  "convene three structured workshops&mdash;one with environmental-justice researchers, one with "
  "data-provider agencies and repositories, and one with community organizations&mdash;using a "
  "documented requirements-elicitation protocol to produce a prioritized requirements register. "
  "<b>(B) Landscape analysis.</b> We will inventory the major candidate repositories, "
  "documenting their access models, metadata, identifiers, licensing, and governance "
  "constraints. <b>(C) Technical prototyping.</b> We will build a narrow proof-of-concept that "
  "maps and links two representative datasets (an exposure dataset and a health-outcomes "
  "dataset) through a candidate metadata schema, to test feasibility&mdash;not to deploy a "
  "service. <b>(D) Governance design.</b> We will draft a data-governance and community-consent "
  "framework addressing sensitive and community-contributed data. The outputs feed a reference "
  "architecture and a concept-of-operations document.")

P("4. Performance Objectives and Measures &mdash; Evaluation and Expected Outcomes", h2)
P("Success is defined by measurable deliverables and independent evaluation. Expected outcomes "
  "are: a prioritized requirements register validated by at least 20 stakeholders across the "
  "three communities; a landscape report covering at least eight repositories; a working "
  "prototype that links the two sample datasets and answers three benchmark cross-domain "
  "queries; a reference architecture and concept of operations; and a governance framework "
  "reviewed by community partners. An external evaluator will assess each deliverable against "
  "these targets and will measure student-training outcomes and partner-engagement quality "
  "through surveys and structured interviews, reporting formatively at the midpoint and "
  "summatively at the end of the project.")

P("5. Project Management", h2)
P("The PI (Computer Science, Morgan State) leads overall coordination and the technical "
  "prototype; a co-PI in Public Health leads health-data requirements and governance; and a "
  "community-partnerships coordinator leads the community workshops. The 24-month timeline has "
  "four milestones: <b>M1 (months 1&ndash;6)</b> stakeholder workshops complete and requirements "
  "register drafted; <b>M2 (months 7&ndash;12)</b> landscape analysis complete and metadata "
  "schema drafted; <b>M3 (months 13&ndash;18)</b> prototype linking two datasets demonstrated; "
  "<b>M4 (months 19&ndash;24)</b> reference architecture, governance framework, and "
  "sustainability plan delivered. A project-level advisory committee meets semi-annually to "
  "review progress against these milestones.")

P("6. Budget Estimation", h2)
P("The requested budget is within the Category III planning-grant ceiling of $500,000 over up "
  "to two years. It supports partial PI and co-PI effort, graduate research assistants, three "
  "stakeholder workshops, modest prototyping infrastructure, external evaluation, and required "
  "travel. Cost sharing is not included, consistent with the solicitation. A full budget and "
  "budget justification accompany this proposal.")

P("Intellectual Merit (Summary)", h2)
P("The project creates reusable, national-scale standards&mdash;a reference architecture, a "
  "cross-domain metadata schema, and a governance model&mdash;for federating heterogeneous "
  "environmental, health, and socioeconomic data. The work is transdisciplinary and "
  "demonstrably multi-disciplinary, and it is designed to benefit many communities rather than a "
  "single discipline or application, consistent with the IDSS program's primary goal.")

P("Broader Impacts (Summary)", h2)
P("The project broadens participation by training students from underrepresented groups at an "
  "HBCU, by embedding affected communities as co-designers of the data service, and by opening "
  "its workshops to regional minority-serving institutions. Societal benefit is direct: the "
  "future service would make neighborhood-scale environmental-justice evidence reproducible and "
  "accessible. A formal, externally led evaluation measures whether these impacts are achieved "
  "against pre-defined targets.")

# ---- References -----------------------------------------------------------
P("References Cited", h1)
P("[1] National Science Foundation. <i>Proposal &amp; Award Policies &amp; Procedures Guide "
  "(PAPPG)</i>. Current edition.", body)
P("[2] National Academies of Sciences, Engineering, and Medicine. <i>Reproducibility and "
  "Replicability in Science</i>. The National Academies Press, 2019.", body)
P("[3] U.S. EPA. <i>EJScreen: Environmental Justice Screening and Mapping Tool</i>. Technical "
  "documentation.", body)
P("[4] Wilkinson, M. et al. “The FAIR Guiding Principles for scientific data management and "
  "stewardship.” <i>Scientific Data</i> 3, 160018 (2016).", body)

# ---- Data Management Plan -------------------------------------------------
P("Data Management and Sharing Plan", h1)
P("<b>Types of data.</b> This planning project generates requirements registers, a repository "
  "landscape inventory, a candidate metadata schema, workshop notes (de-identified), prototype "
  "code, and design documents. The prototype links two small, already-public sample datasets; "
  "the project does not itself collect new human-subjects data.")
P("<b>Standards and formats.</b> Documents will use open formats (PDF/A, CSV, JSON); metadata "
  "will follow DataCite and schema.org/Dataset, with geospatial metadata following OGC "
  "conventions. Code will follow standard open-source repository practices.")
P("<b>Storage, backup, and security.</b> Artifacts are stored on Morgan State's managed research "
  "storage with automated backup. Any sensitive material is de-identified and access-controlled; "
  "the governance framework deliverable specifies handling rules for the future operational "
  "system.")
P("<b>Sharing and access.</b> Reports, the metadata schema, and prototype code will be "
  "deposited in a public repository (Zenodo) under open licenses (CC-BY for documents, "
  "Apache-2.0 for code) and assigned DOIs at each milestone, not merely “available on "
  "request.”")
P("<b>Retention.</b> All shared artifacts will be retained for a minimum of five years after the "
  "end of the award, consistent with NSF policy and Morgan State's records schedule.")

# ---- Budget Justification (brief) ----------------------------------------
P("Budget Justification (Summary)", h1)
P("<b>Senior personnel.</b> PI (Computer Science) and co-PI (Public Health) are requested at "
  "partial academic-year and summer effort to lead technical and health-data activities. "
  "<b>Other personnel.</b> Two graduate research assistants support the landscape analysis, "
  "prototype, and workshop logistics. <b>Fringe</b> is applied at Morgan State's negotiated "
  "rates. <b>Travel</b> supports the three stakeholder workshops and one NSF PI meeting. "
  "<b>Materials and other direct costs</b> cover modest cloud prototyping resources, "
  "participant-support and workshop costs, and external evaluation. <b>Indirect costs (F&amp;A)</b> "
  "are applied to modified total direct costs at Morgan State's federally negotiated rate. The "
  "total request remains within the $500,000 Category III ceiling; voluntary committed cost "
  "sharing is not included, as required by the solicitation.")

doc = SimpleDocTemplate(OUT, pagesize=letter,
                        leftMargin=0.9*inch, rightMargin=0.9*inch,
                        topMargin=0.85*inch, bottomMargin=0.85*inch,
                        title="NSF Real Draft - EJ-IDSS")
doc.build(S)
print("WROTE", OUT)
