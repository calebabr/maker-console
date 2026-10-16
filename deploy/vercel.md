# Vercel (phone app) setup and the API origin in the CSP

The PWA in `control_app/` is a static Vite build on Vercel Hobby. Its config is `control_app/vercel.json`
(SPA rewrite, `no-cache` on `/sw.js`, security headers and a Content-Security-Policy).

## One-time setup
1. Vercel -> Add New -> Project -> import the GitHub repo.
2. **Root Directory:** `control_app`. Framework: Vite (auto-detected; `vercel.json` also sets it).
3. **Environment Variables** (Production): `VITE_API_BASE_URL` = your API origin, e.g.
   `https://<service>.onrender.com` (https, no trailing slash, no path). Never set `VITE_MOCK`.
4. **Edit the CSP API origin** (below), commit, push. Vercel deploys on every push to `main`.
5. Copy the Vercel URL into Render's `ALLOWED_ORIGIN` (exact, no trailing slash).

## The API origin in `connect-src` (security finding S-14)
`connect-src` lists the only hosts the app's JavaScript may call. It must be the **exact** API origin,
never a wildcard such as `https://*.onrender.com` (anyone can create a free Render service, so a
wildcard would let injected script send the session token to an attacker's server).

Vercel reads `vercel.json` headers as static text (environment variables are not substituted), so the
origin is a one-line edit. The repo ships a placeholder that matches nothing:

```
connect-src 'self' https://your-api-origin.invalid;
```

Until it is replaced, the deployed app cannot reach the API (login shows a network error). This fails
closed on purpose.

**Render:** replace `https://your-api-origin.invalid` with `https://<service>.onrender.com`
(the URL shown on the Render service page; it is public, not a secret).
**VM / custom domain:** replace it with `https://<your-domain>` (the `PUBLIC_URL` of the VM).
**Moving hosts later:** change it in the same commit as `VITE_API_BASE_URL`. During a migration you
may list both origins temporarily, but the check below then needs editing; remove the old one after.

Check before pushing (it must print `CSP check ok`):

```bash
node deploy/check_csp.mjs https://<service>.onrender.com     # same value as VITE_API_BASE_URL
```

CI runs `node deploy/check_csp.mjs` on every push and fails on a wildcard, a non-https source, a
path, or more than one API origin. It only warns about the placeholder.

## After deploy
- Open the Vercel URL on the phone; DevTools/remote console must show no `Refused to connect` CSP errors.
- Response headers on the Vercel URL include `Content-Security-Policy` with your origin.
