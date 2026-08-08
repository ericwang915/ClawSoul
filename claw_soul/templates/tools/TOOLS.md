# TOOLS.md — Local Notes

Skills define *how* tools work. This file is for *your* specifics — the stuff
that's unique to your setup.

## What Goes Here

Anything environment-specific that the agent should know about:

- Device nicknames and locations
- SSH hosts and connection details
- API endpoints and service URLs
- Preferred defaults (voices, languages, formats)
- Project paths and workspace conventions
- Frequently used accounts, repos, or resources
- Anything you'd put on a cheat sheet

## Examples

```markdown
### SSH Hosts
- home-server → 192.168.1.100, user: admin
- gpu-box → 10.0.0.50, user: ml, key: ~/.ssh/gpu

### Projects
- main repo → ~/code/myproject (Python 3.12, uses poetry)
- docs site → ~/code/docs (Next.js, deployed on Vercel)

### Preferences
- Code style: use type hints, prefer pathlib over os.path
- Default language: English
- Timezone: Asia/Shanghai
```

## Why Separate from Skills?

Skills are **shared and reusable**. Your local setup is **yours alone**.
Keeping them apart means you can update skills without losing your notes,
and share skills without leaking your infrastructure details.

---

*Add whatever helps the agent do its job. This is your cheat sheet.*
