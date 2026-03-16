# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest (`master`) | ✅ |
| Older releases | ❌ — please update |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Please report security issues privately:

1. Go to [Security → Advisories](https://github.com/syedfahimdev/ToolsDNS/security/advisories/new) and click **"Report a vulnerability"**
2. Or email directly (check the GitHub profile for contact info)

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

You'll get a response within **48 hours**. Critical issues are patched within 7 days.

## Security Best Practices for Self-Hosters

- Always set `TOOLDNS_API_KEY` to a strong random value — never leave it as `td_dev_key`
- Put ToolsDNS behind a reverse proxy (Caddy/nginx) — never expose port 8787 directly
- Use HTTPS in production (`deploy.sh` sets this up automatically with Caddy)
- Rotate API keys regularly: `tooldns key-create`
- The `/dl/{token}` download endpoint is public by design — tokens expire in 15 minutes
- Keep dependencies updated: `pip install -e . --upgrade`
