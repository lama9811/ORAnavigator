# -*- coding: utf-8 -*-
"""
Cache Warmup - Preload common questions at startup
===================================================
Run this after starting the backend to pre-populate the cache
with frequently asked questions.
"""

import requests
import time

BACKEND_URL = "http://127.0.0.1:8000"

# Most common questions students ask (preload these)
COMMON_QUESTIONS = [
    # Course prerequisites
    "What are the prerequisites for COSC 220?",
    "What are the prerequisites for COSC 320?",
    "What are the prerequisites for COSC 354?",
    "What are the prerequisites for COSC 458?",

    # Degree requirements
    "What courses are required for the CS degree?",
    "How many credits do I need to graduate?",
    "What are the core CS courses?",

    # Department info
    "Who is the department chair?",
    "Where is the CS department located?",
    "What is the CS department phone number?",
    "What is the CS department email?",

    # Advising
    "How do I find my academic advisor?",
    "When is the advising period?",
    "How do I get my registration PIN?",

    # General
    "What programming languages are taught?",
    "Does Morgan State offer a cloud computing degree?",
    "What is the 4+1 program?",
    "How do I apply for an internship?",
]


def warmup_cache():
    """Send common questions to preload the cache."""
    print(f"\n{'='*60}")
    print("CACHE WARMUP - Preloading {len(COMMON_QUESTIONS)} common questions")
    print(f"{'='*60}\n")

    success = 0
    failed = 0

    for i, question in enumerate(COMMON_QUESTIONS, 1):
        try:
            print(f"[{i}/{len(COMMON_QUESTIONS)}] {question[:50]}...", end=" ")
            start = time.time()

            response = requests.post(
                f"{BACKEND_URL}/chat/guest",
                json={"query": question},
                timeout=120
            )

            elapsed = time.time() - start

            if response.status_code == 200:
                data = response.json()
                if data.get("cached"):
                    print(f"ALREADY CACHED ({elapsed:.1f}s)")
                else:
                    print(f"CACHED ({elapsed:.1f}s)")
                success += 1
            else:
                print(f"FAILED - Status {response.status_code}")
                failed += 1

        except Exception as e:
            print(f"ERROR - {e}")
            failed += 1

        # Small delay to not overwhelm the AI
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"WARMUP COMPLETE: {success} cached, {failed} failed")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    warmup_cache()
