"""CI check: flag f-string SQL queries. Warn-only (exit 0)."""
import glob
import re

issues = []
for f in glob.glob('tooldns/**/*.py', recursive=True):
    content = open(f).read()
    # Flag f-string SQL queries (risk of injection)
    matches = re.findall(r'f["\'](.*?SELECT.*?WHERE.*?)["\']\s*[,)]', content, re.IGNORECASE)
    for m in matches:
        if '{' in m:
            issues.append(f'{f}: possible SQL injection: {m[:80]}')
if issues:
    print('SQL INJECTION RISKS:')
    for i in issues:
        print(' ', i)
    # Warn only, don't fail (could be false positives)
else:
    print('SQL injection check passed')
