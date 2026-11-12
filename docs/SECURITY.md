# Security

DevFlow AI runs code it did not write (test suites) and consults tools whose output it
cannot trust (AI agents). This document lists what is treated as untrusted, the controls
in place, how they are tested, and the risks that remain.

> DevFlow is a local developer tool. Its controls have been unit-, integration-, and
> live-container-tested on the development Mac (Docker Desktop 29.9), but it has
> **not** had an external security review. Do not describe it as production-safe.

## Trust boundaries

| Untrusted input | Examples | Primary controls |
|---|---|---|
| Repository contents | source, tests, `conftest.py`, `.git/config`, `.gitattributes`, hooks, symlinks | sandboxed execution; hardened Git access; path validation |
| Test output | stdout/stderr, JUnit XML | size caps, defusedxml, redaction, never executed or interpreted |
| AI provider output | diagnosis text, diffs | treated as data; patch validation; human approval; sandboxed retest |
| API input | paths, revisions, settings | token auth, Pydantic validation, allowlists, bounds |
| The network | other processes, web pages | loopback-only bind, bearer token, Host allowlist, CORS allowlist |

Text inside the repository (comments, test names, logs) never changes DevFlow's
behavior. DevFlow does not act on natural-language content. The only consumer of such
text is the AI provider, and the provider's output is only ever data (see below).

## Controls

### Service exposure
- Binds `127.0.0.1` by default. `config.py` refuses a non-loopback `DEVFLOW_HOST`
  unless `DEVFLOW_ALLOW_REMOTE_BIND=true` is set.
- Every `/api/*` route except `/api/health` requires `Authorization: Bearer <token>`.
  The token is 32 random bytes (`secrets.token_urlsafe`), stored in
  `<data_dir>/api_token` with mode `0600`, and compared in constant time.
- `TrustedHostMiddleware` only accepts `127.0.0.1`, `localhost`, and `[::1]`. This
  defeats DNS-rebinding attacks from a browser page.
- CORS allows only the Vite dev origin. Responses send `nosniff`, `X-Frame-Options:
  DENY`, and `no-referrer`.

### Command execution
- Every host process goes through `security/commands.py`. It accepts an allowlist of
  logical executables (`git`, `docker`, `python`, `claude`, `codex`), takes arguments
  as a list, never uses `shell=True`, and rejects NUL bytes.
- The environment is rebuilt from scratch (`PATH`, locale, `TMPDIR`; `HOME` only where
  a CLI needs its own login). Credentials in the parent environment are not inherited,
  and a test asserts this for `AWS_*` and `GITHUB_TOKEN`.
- No API field is ever turned into a command. Revisions are matched against
  `^(?!-)(?!.*\.\.)[A-Za-z0-9._/@^~-]{1,200}$` and passed after `--end-of-options`.

### Repository access
- Paths are resolved (following symlinks) and must sit inside an approved root: the
  managed examples directory, `DEVFLOW_ALLOWED_ROOTS`, or roots added in Settings.
  Approved roots cannot be `/`, the home directory itself, or anything that contains
  or sits inside `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config`, `~/.kube`, `~/.docker`,
  `~/.claude`, `~/.codex`, or `~/Library`.
- `.git` must be a real directory, not a gitfile, which can point anywhere. Paths are
  re-validated on every use, not only at registration.
- Git ignores system and global config and runs with `core.fsmonitor=false`,
  `core.hooksPath=/dev/null`, `protocol.allow=never`, an empty credential helper, and
  `--no-replace-objects`. DevFlow never runs `checkout`, `status`, or `worktree`.
  Snapshots come from `ls-tree` + `cat-file --batch`, so no smudge filter can run.
  Symlinks, submodules, unsafe paths, and files over 10 MB are skipped, and the
  snapshot is capped at 5,000 files / 100 MB. A test plants a hostile `fsmonitor`, a
  filter driver, and hooks, and asserts none of them executed.

### Sandbox (Docker runner)
Each run gets a fresh container:

| Control | Flag |
|---|---|
| No network | `--network none` |
| Read-only root filesystem | `--read-only`; writable `/tmp` is a 64 MB `noexec,nosuid,nodev` tmpfs |
| No capabilities, no privilege escalation | `--cap-drop ALL`, `--security-opt no-new-privileges` |
| Non-root | `--user 10001:10001` (also the image default) |
| Resource limits | `--memory`/`--memory-swap` (no swap), `--cpus`, `--pids-limit`, `--ulimit nofile` |
| Minimal mounts | snapshot at `/src` **read-only**; an empty per-run directory at `/out` |
| Never | `--privileged`, `--cap-add`, host PID/IPC/UTS namespaces, devices, the Docker socket, home-directory mounts, `-v` |
| Time limit | per-test `pytest-timeout` plus a wall-clock limit enforced by `docker kill` |
| Cleanup | `--rm` plus `docker rm --force` in a `finally` block |

`validate_docker_command()` re-checks the final argument list before every launch.
Forbidden flags cause a refusal, required flags must be present, and only the two
expected mounts are allowed. A refactor that weakens the sandbox therefore fails loudly
instead of running. Unit tests mutate the command in 10 different ways and assert each
is refused. `test_docker_sandbox.py` checks the properties from inside a real container:
UID, no network, read-only `/src` and root filesystem, zero effective capabilities,
`NoNewPrivs`, `noexec /tmp`, the memory limit, cancellation, and cleanup.

Settings can tune the timeout (5–1800 s), memory (128–4096 MB), CPUs (0.25–4), and
PIDs (32–2048). Networking is not a setting.

**The unsandboxed local runner** exists for environments without Docker. It runs
pytest on the host with a scrubbed environment in its own process group. It can only
be enabled from the environment, with *both* `DEVFLOW_RUNNER=local` and
`DEVFLOW_ALLOW_UNSANDBOXED_RUNNER=true`, never through the API. Every run records
`sandboxed=false`, and the dashboard shows a persistent warning.

### AI providers
- **Minimal context.** Only the failing tests' tracebacks and the files they reference
  (at most 8 files / 60 KB), copied into a throwaway `0700` workspace and redacted. The
  workspace is deleted afterwards.
- **Claude Code** runs with `--tools ""`, so no tool is available and the model can
  only return text. `--strict-mcp-config` loads no MCP servers.
- **Codex** runs with `--sandbox read-only --ignore-user-config --ignore-rules
  --ephemeral`.
- No bypass flag is ever used; a unit test checks for 7 known bypass flags.
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `CODEX_API_KEY` are stripped unless the
  user opts in.
- **Prompt injection.** Repository content sits inside `<untrusted_repository_content>`
  tags, and any embedded closing tag is neutralized. The instructions come before the
  data and say to ignore instructions inside it. Phrases such as "ignore previous
  instructions", `curl … | sh`, credential-file references, and "approve this patch"
  are detected and shown to the reviewer as a warning. Detection is advisory. The real
  boundary is that provider output is only text, it never executes, and patches need
  human approval.
- **Provider output** is parsed as JSON. Free text is stored and shown, but never
  executed or used to build a command.

### Patch validation
Every diff goes through the same checks, whatever its source:
- At most 200 KB and 20 files, and no NUL bytes.
- Must be a real unified diff with headers and hunks.
- Every path in `diff --git`, `---`, and `+++` must be relative, contain no `..`, `//`,
  or backslashes, and not touch `.git/`.
- Binary patches, mode changes, symlinks (`120000`), renames, and copies are rejected.
- **Risk score**: test-file changes (+25), skip/xfail or trivially true assertions
  (+30), build or config files such as `setup.py`, `conftest.py`, or `.github/`
  (+25), deleted files (+20), added process/network/deserialization/rmtree calls (+35),
  and size and spread. Each reason is shown to the reviewer.
- A dry-run `git apply --check` runs on a copy of the snapshot, with
  `GIT_CEILING_DIRECTORIES` so git cannot discover an enclosing repository.
- **Normalization** is applied before validation and fixes only formatting slips: CRLF,
  a missing final newline, and `diff --git` headers emitted with a stray leading space.
  The last case was observed in a real Codex response.

### Data handling
- Redaction covers private keys, `sk-ant-…`, `sk-…`, `ghp_`/`github_pat_`, AWS key
  IDs, Slack tokens, JWTs, bearer tokens, and `password=`/`token:`-style assignments.
  It applies to logs, stored stdout/stderr, failure messages, tracebacks, AI context,
  and AI output.
- The data directory and per-run directories are `0700`. The `/out` mount is
  temporarily `0777` so the container UID can write, inside a `0700` parent, and is
  reset afterwards.
- Logs are never served from outside the runs directory, and the stream name is an
  enum.

### Supply chain
- `pip-audit` and `npm audit` run locally (`make security`) and in CI.
- Runner image packages are pinned (`runner/requirements.txt`). Frontend versions are
  exact (`--save-exact`) with a lockfile.
- CI uses `permissions: contents: read`, `persist-credentials: false`, and the
  `pull_request` trigger, with no secrets and no publishing.

## Results of the security checks (development machine)

| Check | Result |
|---|---|
| Bandit (`backend/devflow`) | 0 issues. Two false positives (a container-internal `/tmp` path and a filename constant) are annotated with justifications |
| pip-audit | No known vulnerabilities |
| npm audit | 0 vulnerabilities |
| Workflow schema (`check-jsonschema`) | Valid |
| Security regression tests | Passing (path traversal, symlink escape, hostile git config, revision injection, malicious patches, redaction, command allowlist, env scrubbing, auth, Host header, settings bounds, sandbox flag validation) |
| Live sandbox properties (`make test-docker`) | 5/5 passing on Docker Desktop 29.9.3. Mount table observed from inside: rootfs `overlay ro`, `/src` `ro`, `/out` `rw`, nothing else from the host |

## Residual risks

1. **Container isolation is not a hard boundary.** On macOS, Docker Desktop runs
   containers in a Linux VM. That helps, but kernel or VM escapes exist in principle.
   gVisor (`--runtime=runsc`) or Kata would strengthen this on Linux.
2. **The `/out` bind mount is writable.** A malicious test can fill it until the
   10 MB log cap stops the run. JUnit is capped at 20 MB and parsed safely, but disk
   use between polling intervals is not strictly bounded.
3. **Codex can read local files.** Its read-only sandbox blocks writes and network for
   commands, not reads. A prompt-injected Codex could read, for example, `~/.ssh`
   contents into its *reply*. That reply goes to the reviewer, not to a third party,
   except that the model provider does see what the CLI sends it. Prefer the Claude
   Code provider (no tools) for untrusted repositories.
4. **AI providers are external services.** The selected source files are sent to
   Anthropic or OpenAI under the user's own account. Redaction is pattern-based and
   will miss secrets in unusual formats.
5. **The local runner provides no isolation** and must only be used with trusted
   repositories.
6. **The compose deployment mounts the Docker socket** into the API container, which
   is root-equivalent on the Docker host. Sandbox containers never receive it. The
   native backend (`make serve`) avoids this.
7. **Single shared token.** It doesn't expire or identify individual users. That is
   fine for one local user and not suitable for shared deployment.
8. **Prompt-injection detection is heuristic.** It reduces surprise, not risk. The
   human review step is the control.
9. **Dependencies in the runner image** are fixed. Repositories cannot install
   packages, which is a functional limitation that also prevents fetching code at
   test time.

## Reporting

This is a portfolio project. Report security issues privately to the author rather
than in public issues.
