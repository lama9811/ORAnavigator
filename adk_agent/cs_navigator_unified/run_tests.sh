#!/bin/bash
set -e
echo "=== CS Navigator Regression Tests ==="
echo "Running promptfoo eval..."
npx promptfoo eval -c promptfooconfig.yaml --no-cache -o results.json 2>&1
PASS_RATE=$(python3 -c "
import json
r=json.load(open('results.json'))
results=r.get('results',{}).get('results',[])
total=len(results)
passed=sum(1 for t in results if t.get('success'))
print(f'{passed}/{total} ({passed/total*100:.0f}%)')
if passed/total < 0.9: exit(1)
")
echo "Pass rate: $PASS_RATE"
echo "Results saved to results.json"
