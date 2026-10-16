# Analytics + Terminology interface contract

**Merged into [`API.md`](API.md) on 2026-10-13** (section "Analytics (read-only)"). `API.md` is now the single source for the endpoints, shapes, definitions, caching and rate limits. This file is kept only as a pointer.

Frontend glossary contract (still current): single source `control_app/src/utils/glossary.js` exporting `GLOSSARY` (array of `{id, term, short, body, group, related}`, kebab-case ids) and `getTerm(id)`; `<HelpIcon term="fill-rate" />` links to `/terminology#fill-rate`; a test fails if any `<HelpIcon term=...>` in `src/` is missing from `GLOSSARY`.
