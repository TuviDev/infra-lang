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

## 2. Make the repo public

1. GitHub → Settings → Danger Zone → "Change repository visibility" → Public.
2. Confirm.
3. Optional: add a short description and the tagline in the repo "About" box:
   "Write infrastructure once, compile it to Kubernetes, Compose, or GitHub Actions."
4. Add topics in the About box: `iac`, `kubernetes`, `docker-compose`,
   `devops`, `github-actions`, `terraform`, `dsl`, `infrastructure-as-code`.

## 3. Create the GitHub Release (v0.1.0)

1. GitHub → Releases → "Draft a new release".
2. Tag: `v0.1.0` (on `main`).
3. Title: **Infra Lang v0.1.0**.
4. Body: copy the contents of `docs/release_notes_v0.1.0.md`.
5. Mark "Set as the latest release".
6. Publish.

> If you already have a `v0.1.0-private` tag from earlier, either delete it or
> create `v0.1.0` as the public release. Prefer a clean `v0.1.0`.

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

## 6. Posting to HN / Reddit / Dev.to

Drafts live in `docs/promotion/`:

- `hn_post.md` — Show HN. Post on a **Tuesday or Thursday morning (US time)**.
- `reddit_devops.md` — r/devops and r/kubernetes. Post on a weekday, morning US.
- `devto_article.md` — full article. Cross-post the same week.

**Timing suggestion (stagger, don't dump all at once):**

- **Day 0:** Make repo public + GitHub Release.
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
