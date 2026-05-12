"""
Build "topic track" overview docs that synthesize related courses and faculty
into a single high-recall search hit.

Each track doc answers questions like:
  - "Tell me about the AI track at MSU CS"
  - "What's the path through quantum computing?"
  - "Which faculty work on cybersecurity, and what do they teach?"

This is content the live server's KB doesn't have — it's derived from cross-referencing
our new structured course + faculty docs.
"""

import json
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "backend" / "kb_structured" / "_generated_tracks"
OUT_DIR.mkdir(exist_ok=True)


# Each track defines: relevant course codes, faculty (by name keyword for matching),
# a short blurb, and a recommended progression path.
TRACKS = {
    "artificial_intelligence": {
        "title": "Artificial Intelligence Track",
        "description": "Computer Science track for students interested in AI, intelligent agents, machine learning, expert systems, and AI applications in cybersecurity.",
        "courses": [
            ("COSC 220", "Data Structures and Algorithms", "Required prerequisite for the AI track."),
            ("COSC 470", "Artificial Intelligence", "Foundation course: agents, search, logic, expert systems."),
            ("COSC 472", "Introduction to Machine Learning", "Statistical learning, decision trees, deep learning."),
            ("COSC 471", "Expert Systems", "Knowledge engineering follow-up to AI."),
            ("COSC 474", "Artificial Intelligence in Cybersecurity", "AI for threat detection, adversarial ML, xAI."),
            ("COSC 480", "Introduction to Image Processing and Analysis", "Computer vision foundations."),
            ("CLCO 341", "Machine Learning in the Cloud", "ML deployment on cloud platforms."),
            ("CLCO 312", "Data Science for Social Good", "Responsible ML for social problems."),
        ],
        "faculty": [
            ("Dr. Md Rahman", "Machine learning research."),
            ("Dr. Timothy Oladunni", "AI/NLP research."),
            ("Dr. Guobin Xu", "Machine learning and intelligent systems."),
            ("Dr. Radhouane Chouchane", "Explainable AI for malicious activity detection."),
        ],
        "recommended_path": [
            "COSC 111 → COSC 112 → COSC 220 (sophomore year)",
            "COSC 470 (Artificial Intelligence) — junior year",
            "COSC 472 (Machine Learning) — alongside or after COSC 470",
            "Pick electives: COSC 471 Expert Systems, COSC 474 AI in Cyber, COSC 480 Image Processing",
        ],
    },

    "cybersecurity": {
        "title": "Cybersecurity Track",
        "description": "Computer Science track for students interested in network security, cryptography, threat detection, secure systems, and AI in cybersecurity.",
        "courses": [
            ("COSC 351", "Cybersecurity", "Core CS requirement — threat analysis, encryption, IDS, firewalls."),
            ("COSC 350", "Foundations of Computer Security", "Information assurance, access control, security models."),
            ("COSC 358", "Network Security Fundamentals", "Network protocols, attacks, defensive responses."),
            ("COSC 323", "Introduction to Cryptography", "Digital signatures, PRNGs, modern crypto protocols."),
            ("COSC 462", "Introduction to Database Security", "DB protocols, SQL injection, ACID, DBMS security."),
            ("COSC 410", "Quantum Cryptography", "QKD protocols, post-quantum cryptography."),
            ("COSC 474", "Artificial Intelligence in Cybersecurity", "ML for threat detection."),
        ],
        "faculty": [
            ("Dr. Amjad Ali", "Cybersecurity, AI, critical infrastructure security, IoT, cloud security."),
            ("Dr. Monireh Dabaghchian", "Cybersecurity, IoT security, cyber-physical systems security, spectrum-sharing privacy."),
            ("Dr. Vahid Heydari", "Cybersecurity research."),
            ("Dr. Radhouane Chouchane", "Explainable AI to detect malicious cyber activities."),
            ("Dr. Eric Sakk", "Cryptography, applied to quantum systems."),
        ],
        "recommended_path": [
            "COSC 111 → COSC 112 → COSC 220 (foundation)",
            "COSC 351 Cybersecurity — required for major",
            "COSC 349 Computer Networks (often a cybersecurity prerequisite)",
            "COSC 323 Cryptography (Group B) + COSC 358 Network Security (Cloud Group B)",
            "COSC 462 Database Security or COSC 474 AI in Cybersecurity for advanced topics",
        ],
    },

    "quantum_computing": {
        "title": "Quantum Computing Track",
        "description": "A specialized track for students interested in quantum computation, quantum cryptography, and quantum algorithms. Several courses recently added (Quantum Computing I/II, Capstone in Quantum Information Science).",
        "courses": [
            ("COSC 210", "Quantum Mechanics for Computer Scientists", "Foundation: wave functions, superposition, qubits."),
            ("COSC 215", "Quantum Computing I", "Quantum circuits, gates, density matrices, no-cloning."),
            ("COSC 315", "Quantum Computing II", "Advanced QC: ML, search algorithms, complexity."),
            ("COSC 386", "Introduction to Quantum Computing", "Qiskit, Q#, Cirq, Forest SDKs."),
            ("COSC 410", "Quantum Cryptography", "QKD, post-quantum protocols."),
            ("COSC 415", "Capstone in Quantum Information Science", "Independent project under faculty mentorship."),
            ("COSC 486", "Applied Quantum Computing", "Shor's algorithm, optimization, quantum ML."),
        ],
        "faculty": [
            ("Dr. Eric Sakk", "Cryptology, quantum cryptography, quantum computing, reinforcement learning."),
            ("Dr. Shuangbao 'Paul' Wang", "Quantum cryptography, secure architecture, IoT, AI."),
        ],
        "recommended_path": [
            "COSC 111 → COSC 112 → COSC 241 (foundation through digital logic)",
            "COSC 210 Quantum Mechanics for CS (introduction)",
            "COSC 215 Quantum Computing I",
            "COSC 386 Introduction to Quantum Computing (programming SDKs)",
            "COSC 315 Quantum Computing II (advanced)",
            "COSC 323 Introduction to Cryptography (recommended bridge)",
            "COSC 410 Quantum Cryptography (combines QC + Crypto)",
            "COSC 486 Applied Quantum Computing (capstone-adjacent)",
            "COSC 415 Capstone in Quantum Information Science (senior research)",
        ],
    },

    "cloud_computing": {
        "title": "Cloud Computing Track",
        "description": "Full BS in Cloud Computing degree program (separate from BS in CS). Focuses on cloud infrastructure, cloud applications, big data on cloud, and ML in cloud environments.",
        "courses": [
            ("CLCO 261", "Introduction to Cloud Computing", "AWS, virtualization, software-defined networks/storage."),
            ("CLCO 312", "Data Science for Social Good", "ML deployment for social problems."),
            ("CLCO 341", "Machine Learning in the Cloud", "ML services for forecasting, vision, NLP."),
            ("CLCO 401", "Cloud Applications", "SaaS, IaaS, PaaS, cloud APIs, integration with Google Apps/Facebook/YouTube."),
            ("CLCO 471", "Data Analytics in Cloud", "OpenStack, Hadoop, MapReduce, Spark, big data analytics."),
            ("CLCO 490", "Senior Project in Cloud Computing", "Capstone: cloud projects with faculty mentorship."),
            ("COSC 351", "Cybersecurity", "Required Cloud Computing core."),
            ("COSC 354", "Operating Systems", "Required Cloud Computing core."),
            ("COSC 349", "Computer Networks", "Required Cloud Computing core."),
        ],
        "faculty": [
            ("Dr. Shuangbao 'Paul' Wang", "Chair; quantum, secure architecture, IoT — relevant to cloud."),
            ("Dr. Amjad Ali", "Cloud security."),
        ],
        "recommended_path": [
            "Year 1: COSC 111, 112, MATH 113, 114",
            "Year 2: COSC 220, 241, MATH 241, 312, CLCO 261",
            "Year 3: COSC 349, 351, 354, CLCO 401",
            "Year 4: CLCO 471 (data analytics), CLCO 490 (capstone)",
            "Note: BS Cloud is a SEPARATE degree from BS CS — pick one",
        ],
    },

    "data_science": {
        "title": "Data Science / Analytics Track",
        "description": "Data Science is offered through specific courses within the BS CS curriculum. Useful for students interested in data analytics, big data, and statistical computing.",
        "courses": [
            ("COSC 251", "Introduction to Data Science", "Data analysis, modeling, mining, visualization, search."),
            ("COSC 472", "Introduction to Machine Learning", "Statistical learning, decision trees, deep learning."),
            ("COSC 459", "Database Design", "Required CS core — data modeling, query languages, optimization."),
            ("CLCO 312", "Data Science for Social Good", "Responsible ML for social problems."),
            ("CLCO 471", "Data Analytics in Cloud", "Big data: Hadoop, MapReduce, Spark."),
            ("MATH 331", "Applied Probability and Statistics", "Required CS supporting course — statistical foundation."),
        ],
        "faculty": [
            ("Dr. Md Rahman", "Machine learning."),
            ("Dr. Guobin Xu", "Machine learning and intelligent systems."),
            ("Dr. Timothy Oladunni", "AI/data science."),
        ],
        "recommended_path": [
            "COSC 111 → COSC 112 (programming foundation)",
            "MATH 241 → MATH 312 → MATH 331 (math/stats foundation)",
            "COSC 251 Intro to Data Science",
            "COSC 459 Database Design",
            "COSC 472 Machine Learning",
            "Cap with CLCO 471 Data Analytics in Cloud or capstone project",
        ],
    },

    "software_engineering": {
        "title": "Software Engineering / Database Track",
        "description": "For students focused on software development, system design, programming languages, and databases.",
        "courses": [
            ("COSC 458", "Software Engineering", "SDLC, formal methods, version control, team project."),
            ("COSC 459", "Database Design", "Data modeling, query languages, optimization, concurrency."),
            ("COSC 352", "Organization of Programming Languages", "Language design and theory."),
            ("COSC 354", "Operating Systems", "OS principles, concurrency, virtual memory."),
            ("COSC 238", "Object Oriented Programming", "OOP foundations, UML."),
            ("COSC 239", "Java Programming", "Java-specific deep dive."),
            ("COSC 332", "Introduction to Game Design and Development", "Unity3D-based game dev."),
            ("COSC 338", "Mobile App Design & Development", "API client design, mobile dev."),
            ("COSC 456", "Compilers", "Compiler design and implementation."),
        ],
        "faculty": [
            ("Dr. Naja Mack", "Software engineering, education research."),
            ("Dr. Timothy Oladunni", "Software development."),
            ("Mr. Sam Tannouri", "Lecturer — software development courses."),
            ("Ms. Grace Steele", "Lecturer — software development courses."),
        ],
        "recommended_path": [
            "COSC 111 → COSC 112 → COSC 220 (foundation)",
            "COSC 238 OOP (Group A elective for both BS CS and BS Cloud)",
            "COSC 458 Software Engineering (CS core, Cloud Group C)",
            "COSC 459 Database Design (CS core, Cloud Group C)",
            "COSC 352 Programming Languages (CS core)",
            "Pick a domain elective: COSC 332 Games, COSC 338 Mobile, COSC 456 Compilers",
        ],
    },

    "bioinformatics": {
        "title": "Bioinformatics Track",
        "description": "Bioinformatics courses bridge computer science and biology. Most are part of the MS in Bioinformatics program but undergrads can take introductory courses.",
        "courses": [
            ("COSC 373", "Foundations of Bioinformatics", "Computational methods, sequence analysis, protein structure."),
            ("COSC 374", "Introduction to Bioprogramming", "Programming projects in computational biology."),
        ],
        "faculty": [
            ("Dr. Roshan Paudel", "Coordinator of MS in Bioinformatics. Teaches BIOI courses across all semesters."),
        ],
        "recommended_path": [
            "Take COSC 111 → COSC 112 first",
            "COSC 373 Foundations of Bioinformatics (no biology background assumed)",
            "COSC 374 Introduction to Bioprogramming (after COSC 373)",
            "If interested in graduate study: explore the MS in Bioinformatics program with Dr. Paudel",
        ],
    },

    "computer_systems": {
        "title": "Systems / Architecture Track",
        "description": "For students interested in low-level systems, computer architecture, networks, and high-performance computing.",
        "courses": [
            ("COSC 241", "Computer Systems and Digital Logic", "Required CS core — Boolean algebra, gates, digital circuits."),
            ("COSC 243", "Computer Architecture", "CPU, memory, I/O, ISA, parallel architectures."),
            ("COSC 247", "Digital Logic", "Advanced digital systems design."),
            ("COSC 343", "Microprocessor Systems and Applications", "Hands-on microcomputer hardware."),
            ("COSC 345", "Introduction to High Performance Computing", "Parallel/distributed computing, HPC platforms."),
            ("COSC 354", "Operating Systems", "Required CS core — concurrency, scheduling, memory management."),
            ("COSC 349", "Computer Networks", "Required CS core — protocols, networking, distributed systems."),
            ("COSC 413", "Parallel Algorithms", "Concurrent programming, distributed algorithms."),
        ],
        "faculty": [
            ("Dr. Eric Sakk", "Computer organization, cryptology."),
            ("Dr. Shuangbao 'Paul' Wang", "Secure architecture, IoT."),
        ],
        "recommended_path": [
            "COSC 111 → COSC 112 → COSC 220 → COSC 241 (foundation)",
            "COSC 243 Computer Architecture (CS Group A)",
            "COSC 354 Operating Systems (CS core)",
            "COSC 349 Computer Networks (CS core)",
            "Advanced: COSC 343 Microprocessors, COSC 345 HPC, or COSC 413 Parallel Algorithms",
        ],
    },
}


def build_doc(track_id: str, info: dict) -> dict:
    course_codes = [c[0] for c in info["courses"]]
    faculty_names = [f[0] for f in info["faculty"]]

    content_lines = [
        info["title"],
        "",
        info["description"],
        "",
        "RELEVANT COURSES:",
    ]
    for code, name, note in info["courses"]:
        content_lines.append(f"  - {code} {name}: {note}")

    content_lines += ["", "RELEVANT FACULTY:"]
    for name, expertise in info["faculty"]:
        content_lines.append(f"  - {name}: {expertise}")

    content_lines += ["", "RECOMMENDED PROGRESSION:"]
    for step in info["recommended_path"]:
        content_lines.append(f"  - {step}")

    return {
        "doc_id": f"track_{track_id}",
        "title": f"Track Overview: {info['title']}",
        "category": "academic",
        "subcategory": "track_overview",
        "track_name": info["title"],
        "course_codes": course_codes,
        "faculty_names": faculty_names,
        "description": info["description"],
        "content": "\n".join(content_lines),
        "source_file": "synthesized 2026-05-06 from catalog + dept website",
    }


def main():
    written = 0
    for track_id, info in TRACKS.items():
        doc = build_doc(track_id, info)
        out = OUT_DIR / f"{doc['doc_id']}.json"
        out.write_text(json.dumps(doc, indent=2))
        print(f"  [OK] {doc['doc_id']:35s} | {len(info['courses'])} courses, {len(info['faculty'])} faculty")
        written += 1
    print(f"\n[RESULT] Wrote {written} track overview docs to {OUT_DIR}")


if __name__ == "__main__":
    main()
