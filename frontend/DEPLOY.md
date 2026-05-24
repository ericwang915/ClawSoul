# Deploy

The site is on **Cloudflare Pages**, project `clawsoul`.

- Production: https://clawsoul.pages.dev
- Account: `Wangchen2007915@gmail.com's Account` (`aadde33ec5f286c97831236c9a6dcfd7`)

## One-shot deploy (from your machine)

```bash
npm run deploy
```

Runs `astro build` and uploads `dist/` via Wrangler. You're already authed
on this machine; if you ever need to re-auth:

```bash
npm run cf:login
```

## What's deployed

- `dist/` — static HTML/CSS/JS built by Astro
- `public/_headers` — security + cache headers (HSTS, X-Frame-Options, etc.)

## Custom domain

Once the domain is on Cloudflare:

1. Cloudflare dashboard → **Workers & Pages** → `clawsoul` → **Custom domains**
2. Click **Set up a custom domain**, enter `clawsoul.ai` (or whatever you picked)
3. Cloudflare auto-creates the CNAME on the same account — done

(If the domain isn't on Cloudflare yet, do that first — see the DNSSEC notes
from earlier; once nameservers point at Cloudflare, custom-domain attachment
is one click.)

## Auto-deploy on git push (recommended for the long run)

Once you push `frontend/` to GitHub:

1. CF dashboard → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**
2. Pick the repo, set:
   - Build command: `npm run build`
   - Output directory: `dist`
   - Root directory: `frontend`
3. Every push to `main` rebuilds and deploys. PRs get preview URLs.

After connecting Git, you can delete the manually-deployed `clawsoul`
project (or rename one of them) so you don't have two pointing at the
same source.

## Rollback

```bash
npx wrangler pages deployment list --project-name=clawsoul
npx wrangler pages deployment tail --project-name=clawsoul  # live logs
```

Or in CF dashboard → Pages → `clawsoul` → Deployments → **Rollback**
button on any older deploy.
