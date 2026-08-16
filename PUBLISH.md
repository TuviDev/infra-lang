# PUBLISH — Owner checklist (internal, delete before PyPI publish)

This file is for the repository owner only. It walks through making the repo
public, creating the GitHub Release, taking screenshots, publishing to PyPI
(next week), and posting to HN/Reddit. **Delete this file before the PyPI
release.**

---

## 1. Pre-flight (all done)

- [x] README rewritten (new structure, quick demo, badges)
- [x] pyproject metadata updated (description = tagline, keywords, classifiers, urls)
- [x] CONTRIBUTING, SECURITY, CODE_OF_CONDUCT present
- [x] GitHub issue/PR templates present
- [x] docs audited; promotion drafts in `docs/promotion/`
- [x] examples commented and compiling
- [x] release notes in `docs/release_notes_v0.1.0.md`
- [x] Full suite green, coverage ≥92%, ruff/mypy clean, build+twine PASS
- [x] CI runs on 3 OS × 3 Python versions (Linux/macOS/Windows × 3.11/3.12/3.13)
- [x] Codespaces devcontainer present
- [x] History audited for `.tools/`, large blobs, and secrets

## 1b. Clean git history before public (IMPORTANT — do this FIRST)

Audit found **no** `.tools/` binaries or large blobs in history and no real
secrets (only test literals like `supersecret`). If you ever need to purge a
file from history, this is the recipe:

```bash
# 1. Backup: clone the repo to a separate folder first
git clone <repo-url> /tmp/infra-lang-backup

# 2. Install git-filter-repo
pip install git-filter-repo

# 3. Purge a path (e.g. .tools/) from ALL history
git filter-repo --path .tools --invert-paths

# 4. Force push the rewritten history
git push --force

# 5. Verify the file is gone from history
git log --all --pretty=format: --name-only | sort -u | grep -i tools
```

**Expected outcome:** the path no longer appears in any commit. The repo size
(.git) drops and the history is clean.

**Warning:** this rewrites history. Every existing clone becomes incompatible
and must be re-cloned. This is **only** acceptable for a private repo before the
first public release — never after publishing.

**Rollback:** if something goes wrong, restore from the backup clone made in
step 1 (push it back with `--force`).

## 2. Make the repo public

1. GitHub → Settings → Danger Zone → "Change repository visibility" → Public.
2. Confirm.
3. Optional: add a short description and the tagline in the repo "About" box:
   "Write infrastructure once, compile it to Kubernetes, Compose, or GitHub Actions."
4. Add topics in the About box: `iac`, `kubernetes`, `docker-compose`,
   `devops`, `github-actions`, `terraform`, `helm`, `dsl`,
   `infrastructure-as-code`.
5. Enable **Discussions**: Settings → Features → check "Discussions". Set the
   announcement to point at `docs/promotion/` and the hosted docs.

## 3. Create the GitHub Release (v0.1.0)

1. GitHub → Releases → "Draft a new release".
2. Tag: `v0.1.0` (on `main`).
3. Title: **Infra Lang v0.1.0**.
4. Body: copy the contents of `docs/release_notes_v0.1.0.md`.
5. Mark "Set as the latest release".
6. Publish.

> If you already have a `v0.1.0-private` tag from earlier, either delete it or
> create `v0.1.0` as the public release. Prefer a clean `v0.1.0`.

## 3b. Enable GitHub Pages (deploy the hosted docs)

The docs site is built with MkDocs Material and deployed automatically from
`.github/workflows/docs.yml` (on every push touching `docs/`/`mkdocs.yml`).

1. GitHub → **Settings** → **Pages**.
2. Under **Build and deployment** → **Source**, select **GitHub Actions**.
3. **Save**.
4. Push to `main` (or run the `Deploy Docs` workflow manually via
   **Actions → Deploy Docs → Run workflow**). The first deployment triggers.
5. Verify the deployment: **Actions → Deploy Docs** shows a green run, and the
   site is live at **https://kakukpl.github.io/infra-lang/**.
6. The workflow sets the Pages environment URL automatically.

> The Pages environment must be allowed. If the repo is still private, GitHub
> Pages is not served publicly until the repo is made public — you can still
> build locally with `mkdocs build --strict`.

## 4. Screenshots (what to show)

Capture, then add to the README's quick-demo section or a `docs/images/` dir:

1. **The `.infra` file** side-by-side with the generated Kubernetes YAML —
   highlight how much smaller the source is.
2. **`infra compile` running** in a terminal (three targets: kubernetes,
   compose, github) showing the `infra-out/` output.
3. **The VS Code extension**: a `.infra` file open with an inline diagnostic
   (a hardcoded secret, say) and the completion menu.
4. **`infra validate`** showing an error with a source location and hint.
5. Optional: `infra diff` between two configs.

Save them as `docs/images/` and reference them from the README with relative
paths (so they work on GitHub).

## 5. Publish to PyPI (next week, after feedback)

1. Create a PyPI account and a project-scoped API token.
2. Build and check:
   ```bash
   rm -rf dist && python -m build
   twine check dist/*
   ```
3. Upload to TestPyPI first:
   ```bash
   twine upload --repository testpypi dist/*
   ```
4. Test the TestPyPI install in a fresh venv.
5. Upload to PyPI:
   ```bash
   twine upload dist/*
   ```
6. Update the README install command from `git+...` to `pip install infra-lang`.
7. Update `docs/quickstart.md` and `PUBLISH` references accordingly.

## 5b. Monitoring after publication

- **GitHub Actions**: watch `CI` and `Deploy Docs` runs for the first pushes.
- **Issues**: triage new issues within the first 48h; the issue templates
  capture environment + repro.
- **Docs traffic**: enable GitHub Pages traffic analytics (Pages → Traffic) if
  you want numbers.
- **Star growth**: watch the repo Insights → traffic / stars for the first week.

**Rollback:** if a critical bug ships, revert the commit or tag `v0.1.1` with
the fix. Publishing to PyPI is the only step that's hard to undo — test
TestPyPI first (§5).

## 6. Posting to HN / Reddit / Dev.to

Drafts live in `docs/promotion/`:

- `hn_post.md` — Show HN. Post on a **Tuesday or Thursday morning (US time)**.
- `reddit_devops.md` — r/devops and r/kubernetes. Post on a weekday, morning US.
- `devto_article.md` — full article. Cross-post the same week.

**Timing suggestion (stagger, don't dump all at once):**

- **Day 0:** Make repo public + GitHub Release + enable GitHub Pages (§3b).
- **Day 0–1:** HN Show HN post.
- **Day 1–2:** Reddit (r/devops, then r/kubernetes if well received).
- **Day 2–3:** Dev.to article.
- **Week later:** PyPI publish, then update install instructions.

## 7. Before the PyPI release (cleanup)

- [ ] Delete this `PUBLISH.md` file.
- [ ] Confirm no `SESSION_*`, `QA_*`, or internal artifacts remain in the repo.
- [ ] Re-run the full suite + build + twine one final time.

---

*This file is an internal owner artifact and must be removed before the public
PyPI release.*
