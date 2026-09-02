# State Locking (since 1.0.0)

Concurrent deploys corrupt state. `infra.workspace.lock` provides a tiny,
stdlib-only **atomic lock** for everything stored under
`.infra-state/<project>/` — no daemons, no psutil, no platform-specific
code paths.

## How it works

```python
from infra.workspace.lock import WorkspaceLock

with WorkspaceLock("my_project", operation="deploy"):
    deploy_things()      # only one process at a time can be here
# lock released automatically — even on exceptions
```

The lock is a JSON file `.infra-state/{project}/locks/{operation}.lock`:

```json
{"pid": 4242, "hostname": "build-01", "timestamp": "2026-09-02T12:00:00+00:00", "operation": "deploy"}
```

### Atomic creation

The file is created with `open(path, "x")` (`O_EXCL`) — either the file is
created exclusively or `FileExistsError` is raised. This is atomic on
**Windows, macOS and Linux** alike, with no symlinks, no `fcntl` and no
third-party packages.

### Stale-lock detection

A lock whose *owner process no longer exists* is **stale** and is reclaimed
automatically on the next acquire attempt:

- **POSIX**: `os.kill(pid, 0)` — `ProcessLookupError` means gone,
  `PermissionError` means alive-but-not-ours.
- **Windows**: the stdlib-invoked `tasklist` tool (filtered by PID).

Any probe failure *fails safe* — the lock is assumed alive, never stolen.

A lock file with an unreadable payload (crash while writing) is only
reclaimed once it is older than one second, so a concurrently *starting*
process — whose file is briefly visible before its JSON lands — is never
raced.

## `infra workspace unlock`

When a crashed process leaves a lock behind, the CLI helper removes it:

```bash
infra workspace unlock my_project
# -> [OK] Removed 'deploy' lock for project 'my_project'.

infra workspace unlock my_project --force   # override the liveness check
```

- Operates directly on `./.infra-state/` (no workspace manifest needed).
- **Refuses to remove a lock owned by a live process** unless `--force`
  is given — use that only when you are sure the process is gone.

## API summary

| Symbol | Purpose |
|---|---|
| `WorkspaceLock(project, operation="deploy", state_root=None)` | Context manager; `acquire()` raises `LockError` when held. |
| `lock_path(state_root, project, operation)` | `.infra-state/<project>/locks/<operation>.lock` |
| `read_lock(path)` / `lock_status(root, project, op)` | Parse the payload (`None` when missing/corrupt). |
| `is_stale(info)` | `True` when the owning process is gone. |
| `LockError` | Raised when the lock cannot be acquired. |

## Guarantees & limits

- Mutual exclusion across threads *and* processes on the same machine.
- Auto-release on exceptions (`with`-protocol).
- Not a distributed lock — shared filesystems (NFS) weaken `O_EXCL`
  semantics; keep `.infra-state/` local.
