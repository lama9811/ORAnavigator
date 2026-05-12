"""
Build comprehensive degree-program docs from the official Morgan State catalog
(catalog.morgan.edu, 2024-2026 catalog, accessed 2026-05-06).

Creates:
  - program_bs_computer_science.json
  - program_bs_cloud_computing.json
  - program_cs_minor.json
  - department_cs.json
"""

import json
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "backend" / "kb_structured" / "_generated_programs"
OUT_DIR.mkdir(exist_ok=True)


BS_CS_CONTENT = """Bachelor of Science in Computer Science - Degree Requirements
Source: Morgan State University Undergraduate Catalog 2024-2026 (catalog.morgan.edu)

TOTAL CREDITS REQUIRED: 120

CREDIT DISTRIBUTION:
  - General Education and University Requirements: 44 credits
  - Supporting Courses: 11 credits (some overlap with Gen Ed)
  - Required Courses for the Major: 65 credits

GENERAL EDUCATION (44 credits):
  - ENGL 101 or 111 - Composition I (3)
  - ENGL 102 or 112 - Composition II (3)
  - MATH 241 - Calculus I (4)            [also Supporting]
  - COSC 111 - Intro to Computer Science I (4)  [also Supporting]
  - HH General Education (3)
  - Two AH General Education courses (6)
  - BP General Education with lab (4)
  - BP General Education without lab (3)
  - Two SB General Education courses (6)
  - CI General Education (3)
  - CT General Education (3)
  - ORNS 106 - Freshman Orientation (1)
  - Physical Activity OR FIN 101 OR MIND 101 (1)

SUPPORTING COURSES (11 credits, after Gen Ed overlap):
  - MATH 242 - Calculus II (4)
  - MATH 312 - Linear Algebra I (3)
  - MATH 331 - Applied Probability and Statistics (3)
  - COSC 201 - Computer Ethics (1)

REQUIRED MAJOR COURSES (43 credits):
  - COSC 111 - Intro to Computer Science I (4)
  - COSC 112 - Intro to Computer Science II (4)
  - COSC 220 - Data Structures and Algorithms (4)
  - COSC 241 - Computer Systems and Digital Logic (3)
  - COSC 281 - Discrete Structure (3)
  - COSC 349 - Computer Networks (3)
  - COSC 351 - Cybersecurity (3)
  - COSC 352 - Organization of Programming Languages (3)
  - COSC 354 - Operating Systems (3)
  - COSC 458 - Software Engineering (3)
  - COSC 459 - Database Design (3)
  - COSC 490 - Senior Project (3)

ELECTIVES (22 credits across four groups):

  Group A - select 3 courses (9-12 credits):
    - COSC 238 Object Oriented Programming (4)
    - COSC 239 Java Programming (3)
    - COSC 243 Computer Architecture (3)
    - COSC 251 Intro to Data Science (3)
    - CLCO 261 Intro to Cloud Computing (3)

  Group B - select 2 courses (6 credits):
    - COSC 320 Algorithm Design and Analysis (3)
    - COSC 323 Intro to Cryptography (3)
    - COSC 332 Intro to Game Design and Development (3)
    - COSC 338 Mobile App Design & Development (3)
    - COSC 383 Numerical Methods and Programming (3)
    - COSC 385 Theory of Languages and Automata (3)
    - COSC 386 Intro to Quantum Computing (3)
    - MATH 313 Linear Algebra II (3)
    - EEGR 317 Electronic Circuits (4)

  Group C - select 4 courses (12 credits):
    - COSC 470 Artificial Intelligence OR COSC 472 Intro to Machine Learning (3)
    - COSC 460 Computer Graphics (3)
    - COSC 480 Intro to Image Processing and Analysis (3)
    - COSC 486 Applied Quantum Computing (3)
    - COSC 491 Conference Course (3)
    - COSC 498 Senior Internship (3)
    - COSC 499 Senior Research or Teaching/Tutorial Assistantship (3)
    - CLCO 471 Data Analytics in Cloud (3)

  Group D - select 1 course (3 credits):
    - INSS 391 IT Infrastructure and Security (3)
    - INSS 494 Information Security and Risk Management (3)
    - EEGR 481 Intro to Network Security (3)
    - EEGR 483 Intro to Security Management (3)
    - Any 300-400 level COSC course not previously taken (3)

GRADUATION REQUIREMENTS:
  - Cumulative GPA >= 2.0
  - Major GPA >= 2.0
  - No grade below "C" in major courses
  - Pass Senior Departmental Comprehensive Examination
  - Earn 6 credits in the Complementary Studies Program
  - Complete junior+senior major requirements at MSU (unless prior written Dean approval)

Catalog page: https://catalog.morgan.edu/preview_program.php?catoid=26&poid=5968"""


BS_CLOUD_CONTENT = """Bachelor of Science in Cloud Computing - Degree Requirements
Source: Morgan State University Undergraduate Catalog 2024-2026 (catalog.morgan.edu)

TOTAL CREDITS REQUIRED: 120

CREDIT DISTRIBUTION:
  - General Education and University Requirements: 44 credits
  - Supporting Courses: 14 credits
  - Required Courses for the Major: 62 credits

GENERAL EDUCATION (44 credits):
  - ENGL 101 - Composition I (3)
  - ENGL 102 - Composition II (3)
  - MATH 113 - Intro to Mathematical Analysis I (4)*
  - COSC 111 - Intro to Computer Science I (4)*
  - One HH General Education course (3)
  - One BP course with lab (4)
  - One BP course without lab (3)
  - Two SB courses (6)
  - Two AH courses (6)
  - One CT course (3)
  - One CI course (3)
  - ORNS 106 - Freshman Orientation (1)
  - Physical Activity OR FIN 101 OR MIND 101 (1)

  * grade C or higher required

SUPPORTING COURSES (14 credits):
  - MATH 113 - Intro to Mathematical Analysis I (4)*
  - MATH 114 - Intro to Mathematical Analysis II (4)
  - MATH 241 - Calculus I (4)
  - MATH 312 - Linear Algebra I (3)
  - MGBU 200 - Intro to Business for Non-Business Majors (3)

REQUIRED MAJOR CORE (40 credits):
  - COSC 111 - Intro to Computer Science I (4)*
  - COSC 112 - Intro to Computer Science II (4)
  - COSC 220 - Data Structures and Algorithms (4)
  - COSC 241 - Computer Systems and Digital Logic (3)
  - COSC 349 - Computer Networks (3)
  - COSC 351 - Cybersecurity (3)
  - COSC 354 - Operating Systems (3)
  - CLCO 261 - Intro to Cloud Computing (3)
  - CLCO 401 - Cloud Applications (3)
  - CLCO 490 - Senior Project in Cloud Computing (3)

ELECTIVES (22 credits across four groups):

  Group A - select 2 courses:
    - COSC 238 Object Oriented Programming (4)
    - COSC 239 Java Programming (3)
    - COSC 243 Computer Architecture (3)
    - COSC 251 Intro to Data Science (3)
    - COSC 281 Discrete Structure (3)
    - MATH 242 Calculus II (4)

  Group B - select 3 courses:
    - COSC 320 Algorithm Design and Analysis (3)
    - COSC 323 Intro to Cryptography (3)
    - COSC 332 Intro to Game Design and Development (3)
    - COSC 358 Network Security Fundamentals (3)
    - COSC 383 Numerical Methods and Programming (3)
    - COSC 385 Theory of Languages and Automata (3)
    - COSC 386 Intro to Quantum Computing (3)
    - MATH 313 Linear Algebra II (3)
    - MATH 331 Applied Probability and Statistics (3)
    - EEGR 317 Electronic Circuits (4)

  Group C - select 5 courses (3 credits each):
    - COSC 458 Software Engineering
    - COSC 459 Database Design
    - COSC 470 Artificial Intelligence
    - COSC 472 Intro to Machine Learning
    - COSC 486 Applied Quantum Computing
    - COSC 491 Conference Course
    - COSC 498 Senior Internship
    - COSC 499 Senior Research or Teaching/Tutorial Assistantship
    - CLCO 471 Data Analytics in Cloud

  Group D - select 1 course:
    - MATH 345 Mathematics for Insurance and Investment (3)
    - MATH 346 Financial Mathematics (4)
    - MATH 361 Intro to Mathematical Modeling (3)
    - MGBU 326 Business, Ethics and Society (3)
    - INSS 391 IT Infrastructure and Security (3)
    - INSS 494 Information Security and Risk Management (3)
    - EEGR 481 Intro to Network Security (3)
    - EEGR 483 Intro to Security Management (3)
    - 300-400 level COSC course not previously taken (3)

GRADUATION REQUIREMENTS:
  Same as BS Computer Science (cum GPA >= 2.0, major GPA >= 2.0, no grade below "C" in major,
  pass Senior Comprehensive Exam, 6 credits Complementary Studies, junior+senior coursework at MSU).

Catalog page: https://catalog.morgan.edu/preview_program.php?catoid=26&poid=6172"""


CS_MINOR_CONTENT = """Computer Science Minor - Requirements
Source: Morgan State University Undergraduate Catalog 2024-2026 (catalog.morgan.edu)

TOTAL CREDITS REQUIRED: 18

REQUIRED COURSES (5 courses):
  - COSC 111 - Intro to Computer Science I (4)
  - COSC 112 - Intro to Computer Science II (4)
  - COSC 220 - Data Structures and Algorithms (4)
  - COSC 241 - Computer Systems and Digital Logic (3)
  - COSC 243 - Computer Architecture (3)

NOTES:
  - No GPA minimum specified for the minor itself
  - All prerequisites still apply (e.g., COSC 112 requires COSC 111 with C or higher)
  - Open to students of any major; declare via your advisor

Catalog page: https://catalog.morgan.edu/preview_program.php?catoid=26&poid=6078"""


DEPT_CS_CONTENT = """Department of Computer Science - Overview
Source: Morgan State University Undergraduate Catalog 2024-2026 (catalog.morgan.edu)
Accessed: 2026-05-06

CHAIRPERSON: Shuangbao "Paul" Wang

CONTACT:
  - Address: 1700 East Cold Spring Lane, Baltimore, Maryland 21251
  - Department phone: (443) 885-3962 (admin office, Wendy Smith)
  - Chair phone: (443) 885-4503 (Dr. Wang)

DEGREE PROGRAMS OFFERED:
  - B.S. in Computer Science (120 credits) - see program_bs_computer_science doc
  - B.S. in Cloud Computing (120 credits) - see program_bs_cloud_computing doc
  - Computer Science Minor (18 credits) - see program_cs_minor doc

DEPARTMENT MISSION:
The program prepares students for entry into the computing profession and for graduate study in
computer, computational, and data sciences. Students develop problem-solving capabilities across
the full software development lifecycle from definition through documentation, with emphasis on
teamwork and interdisciplinary communication.

DEPARTMENT-WIDE GRADUATION REQUIREMENTS (all CS programs):
  - Cumulative GPA of 2.0 or higher
  - Major GPA of 2.0 or higher
  - No grade below "C" in major courses
  - Complete General Education Requirements
  - Earn 6 credits in the Complementary Studies Program
  - Pass the Senior Departmental Comprehensive Examination
  - Complete junior and senior coursework at Morgan State (unless prior written Dean approval)

LEADERSHIP ROLES:
  - Chair: Shuangbao "Paul" Wang
  - Associate Chair / Director PhD Advanced Computing: Md Rahman
  - Director of Undergraduate Studies: Radhouane Chouchane
  - Director of MS Advanced Computing: Guobin Xu
  - Coordinator of MS Bioinformatics Program: Roshan Paudel
  - Administrative Assistant: Wendy Smith (McMechen 507A)
  - Engineer in Residence (CodePath): Rahmel Bailey

Catalog page: https://catalog.morgan.edu/preview_entity.php?catoid=26&ent_oid=1618"""


DOCS = [
    {
        "doc_id": "program_bs_computer_science",
        "title": "Program: B.S. Computer Science (Catalog 2024-2026)",
        "category": "academic",
        "subcategory": "degree_program",
        "program_name": "Bachelor of Science in Computer Science",
        "program_code": "BS_CS",
        "total_credits": 120,
        "core_courses": ["COSC 111","COSC 112","COSC 220","COSC 241","COSC 281","COSC 349",
                         "COSC 351","COSC 352","COSC 354","COSC 458","COSC 459","COSC 490"],
        "supporting_courses": ["MATH 241","MATH 242","MATH 312","MATH 331","COSC 201"],
        "min_gpa_cumulative": 2.0,
        "min_gpa_major": 2.0,
        "min_grade_in_major": "C",
        "comprehensive_exam_required": True,
        "complementary_studies_credits": 6,
        "content": BS_CS_CONTENT,
        "source_file": "catalog.morgan.edu/preview_program.php?poid=5968",
    },
    {
        "doc_id": "program_bs_cloud_computing",
        "title": "Program: B.S. Cloud Computing (Catalog 2024-2026)",
        "category": "academic",
        "subcategory": "degree_program",
        "program_name": "Bachelor of Science in Cloud Computing",
        "program_code": "BS_CLOUD",
        "total_credits": 120,
        "core_courses": ["COSC 111","COSC 112","COSC 220","COSC 241","COSC 349","COSC 351",
                         "COSC 354","CLCO 261","CLCO 401","CLCO 490"],
        "supporting_courses": ["MATH 113","MATH 114","MATH 241","MATH 312","MGBU 200"],
        "min_gpa_cumulative": 2.0,
        "min_gpa_major": 2.0,
        "min_grade_in_major": "C",
        "comprehensive_exam_required": True,
        "complementary_studies_credits": 6,
        "content": BS_CLOUD_CONTENT,
        "source_file": "catalog.morgan.edu/preview_program.php?poid=6172",
    },
    {
        "doc_id": "program_cs_minor",
        "title": "Program: Computer Science Minor (Catalog 2024-2026)",
        "category": "academic",
        "subcategory": "degree_program",
        "program_name": "Computer Science Minor",
        "program_code": "MINOR_CS",
        "total_credits": 18,
        "required_courses": ["COSC 111","COSC 112","COSC 220","COSC 241","COSC 243"],
        "content": CS_MINOR_CONTENT,
        "source_file": "catalog.morgan.edu/preview_program.php?poid=6078",
    },
    {
        "doc_id": "department_cs_overview",
        "title": "Department of Computer Science - Official Overview",
        "category": "academic",
        "subcategory": "department_overview",
        "department_name": "Computer Science",
        "chair": "Shuangbao 'Paul' Wang",
        "phone": "(443) 885-3962",
        "address": "1700 East Cold Spring Lane, Baltimore, Maryland 21251",
        "programs_offered": ["BS Computer Science", "BS Cloud Computing", "CS Minor"],
        "content": DEPT_CS_CONTENT,
        "source_file": "catalog.morgan.edu/preview_entity.php?ent_oid=1618",
    },
]


def main():
    for d in DOCS:
        out = OUT_DIR / f"{d['doc_id']}.json"
        out.write_text(json.dumps(d, indent=2))
        print(f"  [OK] {d['doc_id']}")
    print(f"\n[RESULT] Wrote {len(DOCS)} program/department docs to {OUT_DIR}")


if __name__ == "__main__":
    main()
