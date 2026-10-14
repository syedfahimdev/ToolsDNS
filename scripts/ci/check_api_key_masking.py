"""CI check: never expose raw API keys in responses. Exits 1 on findings."""
import glob
import re
import sys

issues = []
# Scan Python source for patterns that might return raw API keys in responses
for f in glob.glob('tooldns/**/*.py', recursive=True):
    content = open(f).read()
    # Flag if full key is returned in a JSON response dict (not just logged)
    if re.search(r'[\'"]\s*key\s*[\'"].*credentials\.credentials', content):
        issues.append(f'{f}: possible raw key exposure in response')
if issues:
    print('API KEY EXPOSURE ISSUES:')
    for i in issues:
        print(' ', i)
    sys.exit(1)
print('API key masking check passed')
