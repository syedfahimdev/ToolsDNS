"""CI check: warn if CORS allows all origins. Warn-only (exit 0)."""
content = open('main.py').read()
issues = []
if 'allow_origins=["*"]' in content or "allow_origins=['*']" in content:
    issues.append('CORS allows ALL origins — restrict in production')
if issues:
    print('SECURITY WARNINGS:')
    for i in issues:
        print(' -', i)
else:
    print('CORS/auth check passed')
