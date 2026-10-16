# Release secrets: the `production` environment (owner steps)

The release workflow's secrets are stored as secrets of a GitHub **environment** named `production` that only the `main` branch can use. They are not repository secrets: a repository secret can be read by a workflow file on any branch, which would let someone with write access call the deploy hooks and skip the gate and your 2FA approval.

Do this once, after you have deployed Render and Vercel (you need their URLs and hooks). Nothing here is created by the agents.

## 1. Create the environment

1. Open the repository on github.com, then **Settings** (top tab row).
2. In the left sidebar choose **Environments**.
3. Click **New environment**, type `production` (exactly, lower case), click **Configure environment**.

## 2. Limit it to the main branch

1. On the `production` page find **Deployment branches and tags**.
2. Open the dropdown (it says "No restriction") and choose **Selected branches and tags**.
3. Click **Add deployment branch or tag rule**, leave **Branch** selected, type `main`, click **Add rule**.
4. Optional extra layer (free on public repos): tick **Required reviewers**, add yourself, click **Save protection rules**. Every release then also asks you to approve the run in GitHub, on top of the 2FA approval in the app.

## 3. Add the secrets to the environment

Still on the `production` page, scroll to **Environment secrets** and click **Add environment secret** for each row (name exactly as written, then paste the value, then **Add secret**):

| Name | Value |
|---|---|
| `API_BASE_URL` | Your Render service URL, `https://<name>.onrender.com`, no trailing slash |
| `APP_URL` | Your Vercel app URL, `https://<name>.vercel.app` |
| `DEPLOY_STATUS_KEY` | The same value you put in Render as `DEPLOY_STATUS_KEY` |
| `RELEASE_TOKEN` | The same value you put in Render as `RELEASE_TOKEN` |
| `RENDER_DEPLOY_HOOK` | Render: your service, **Settings**, **Deploy Hook**, copy the URL |
| `VERCEL_DEPLOY_HOOK` | Vercel: your project, **Settings**, **Git**, **Deploy Hooks**, create one named `release` for branch `main`, copy the URL |
| `NTFY_TOPIC` | The same topic you set in Render |

Do **not** add any of these under **Settings > Secrets and variables > Actions > Repository secrets**. If you already did, delete them there (the trash icon) after adding them to the environment.

## 4. Check

1. Settings > Environments > `production`: Deployment branches shows `main`; the secrets list shows the 7 names (values are never shown again).
2. Settings > Secrets and variables > Actions: **Repository secrets** has none of the 7.
3. Run the `release` workflow once from `main` (Actions > release > Run workflow). A run started from another branch is refused (the first job only runs on `main`) and cannot read the secrets.

## 5. Rules the release workflow enforces on itself

- Only the current tip of `main` can be released (until Render's deploy-hook `ref=` parameter has been verified on a real deploy).
- A re-run of a finished or failed release is refused: start a new run. The gate runs again right before the Render deploy hook is called.
- The tests in `scripts/tests/test_workflows.py` fail if a job that reads one of these secrets does not declare `environment: production`.
