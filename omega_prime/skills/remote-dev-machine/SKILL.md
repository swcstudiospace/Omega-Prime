---
name: remote-dev-machine
description: Working safely on the shared remote development machine. Use before running any command on the dev box, especially anything destructive, and when setting up or debugging the environment there.
bots: [bot-00-omega-prime]
gates: [G-6]
---
<skill name="remote-dev-machine" version="1">
# Remote Development Machine

## L1 — Summary

**Host:** `{{REMOTE_DEV_USER}}@{{REMOTE_DEV_HOST}}`. Set both in the install configuration (`grokbot/SETUP.md`). Do not hardcode an address.

**Shared machine. If `{{REMOTE_DEV_USER}}` is root, there is no safety net:** no
permission boundary between a mistyped path and the operating system, no attribution of who ran
what, and no undo. A `rm -rf` with a variable that expanded to empty removes the system rather
than erroring.

**Treat every command here as production-grade**, because in terms of blast radius it is.

**Decision tree:**

```
About to run something on the dev box?
│
├─ Does it delete, overwrite, or modify outside your own workspace?
│   └─▶ DESTRUCTIVE. Human approval first (G-6). §3
│
├─ Does it affect other users — services, shared config, packages, ports?
│   └─▶ Coordinate first. §4
│
├─ Does it need credentials?
│   └─▶ §5. Never paste a secret into a shell — it lands in history
│
└─ Ordinary read, build, or test in your own workspace
    └─▶ Go ahead, inside your workspace directory. §2
```

**The root problem is worth fixing rather than working around.** See §6 — a non-root working user
with sudo costs an hour to set up and removes the entire category of accident this skill exists
to manage.

---

## L2 — Method

### §1 Connecting

```bash
ssh {{REMOTE_DEV_USER}}@{{REMOTE_DEV_HOST}}
```

Key-based authentication only. If password auth is enabled, that is a finding — raise it.

Keys live in the agent, never on the box. Do not copy a private key to the dev machine to reach a
third host; use agent forwarding, and only when you need it.

### §2 Workspace discipline

Every bot works in its own directory. Never in `/`, never in `/etc`, never directly in another
bot's workspace.

```
/workspace/
├── bot-01-systems-backend/
├── bot-02-web-edge/
├── bot-03-android/
├── bot-04-ios/
├── bot-05-infrastructure/
└── shared/            ← read-mostly; changes here are coordinated
```

**Before any destructive command, verify where you are:**

```bash
pwd && ls -la          # confirm the directory before acting on it
```

**Never use an unquoted variable in a path for a destructive command:**

```bash
rm -rf "$BUILD_DIR"/*          # $BUILD_DIR empty → rm -rf /*
rm -rf "${BUILD_DIR:?unset}"/* # refuses to run if unset or empty
```

The `:?` form is the habit worth building. It turns a catastrophe into an error message.

### §3 Destructive operations (G-6)

Require recorded human approval before execution:

| Category | Examples |
|---|---|
| Filesystem | `rm -rf` outside your workspace, overwriting shared config, truncating logs |
| Services | Stopping/restarting a shared service, killing another user's process |
| Packages | System-wide install/remove/upgrade, changing the default toolchain version |
| Users/access | Adding users, changing SSH config, modifying sudoers, rotating keys |
| Network | Firewall rules, opening or closing ports, changing DNS |
| Storage | Unmounting, reformatting, resizing, deleting volumes |
| Docker | `docker system prune`, removing volumes, removing another bot's containers |

`docker system prune -a --volumes` deserves naming specifically. It is a common "free up disk"
reflex and it deletes other bots' build caches and named volumes. Prune your own resources by
label instead.

Record in the receipt: what was approved, by whom, when, and the blast radius as understood at the
time.

### §4 Shared resources

**Ports.** Check before binding; a hardcoded 3000 collides with whoever got there first.

```bash
ss -tlnp | grep :3000
```

Use a per-bot port range and document it in `shared/PORTS.md`.

**Disk.** Shared and finite. Check before a large build, and clean up your own artefacts.

```bash
df -h /workspace && du -sh /workspace/*
```

A full disk breaks every bot at once and the failures look unrelated to the cause.

**System packages.** Prefer per-project toolchains — `rustup`, `uv`/`venv`, `nvm`, `sdkman` — over
system-wide installs. A system-wide version bump changes the toolchain under everyone else's
in-flight work, and they will experience it as their code mysteriously breaking.

**Long-running processes.** Run them in `tmux` or `screen` with a named session prefixed by your
bot id, so an orphaned process is attributable.

```bash
tmux new -s bot-01-build
```

### §5 Secrets on the box

**Never paste a secret into a shell.** It goes into `~/.bash_history`, into the process table
where `ps` shows it to every user, and often into logs.

```bash
export API_KEY="sk-live-..."              # in history, in `ps`, shared box
set -a; source /workspace/shared/.env.local; set +a   # file with 0600, outside git
```

Files holding secrets: mode `0600`, outside any git working tree, never in `/tmp`.

If a secret does reach the shell or a commit, it is compromised. **Rotate it.** Deleting the
history line is not remediation — on a shared root box you cannot establish who saw it.

### §6 The root access problem

Running shared development as root means:

- No permission boundary. A typo in a path acts on the system rather than being denied.
- No attribution. Every action is "root"; after an incident you cannot tell who did what.
- No least privilege. A compromised session owns the machine.
- Build tooling running as root produces root-owned artefacts that then break other users.

**The fix is cheap:**

```bash
# as root, once
adduser --disabled-password --gecos "" botdev
usermod -aG docker,sudo botdev
mkdir -p /home/botdev/.ssh && cp ~/.ssh/authorized_keys /home/botdev/.ssh/
chown -R botdev:botdev /home/botdev/.ssh && chmod 700 /home/botdev/.ssh
chmod 600 /home/botdev/.ssh/authorized_keys
mkdir -p /workspace && chown -R botdev:botdev /workspace

# then disable direct root SSH in /etc/ssh/sshd_config:
#   PermitRootLogin no
# and reload:  systemctl reload sshd
```

Per-bot users are better still — real attribution, and one bot's mistake cannot reach another's
workspace.

**Until that happens:** every command on this box is a production command. Read it twice before
pressing enter, and prefer the `${VAR:?}` form everywhere.

> **This section is a recommendation, not a completed change.** Changing SSH access affects
> everyone using the machine and is itself a G-6 operation. Raise it, get approval, and keep a
> second session open while editing `sshd_config` so a mistake does not lock everyone out.

### §7 Debugging on the box

```bash
journalctl -u <service> -f --since "10 min ago"    # service logs
ss -tlnp                                            # what is listening
htop                                                # what is consuming
df -h && du -sh /workspace/*                        # disk
docker ps -a && docker logs --tail 100 <container>  # containers
```

Reproduce a failure on the box before changing anything there. Environment-specific failures are
common and the difference from local is usually the answer — a missing env var, a different
toolchain version, a permission.

### §8 Environment drift

The dev box drifting from CI is the source of "works on the dev box, fails in CI" and its more
annoying inverse.

- Pin toolchain versions in project config, not in the shell profile
- Use the same container images the CI pipeline uses, where practical
- When you find a drift, fix the config rather than the box — a manual fix on the box is lost on
  the next rebuild and invisible to everyone else

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| Unquoted path variable | `rm -rf /*` from an empty variable | `"${VAR:?}"` always |
| Wrong directory | Deleted the wrong workspace | `pwd && ls -la` before destructive commands |
| System-wide install | Another bot's build breaks unrelatedly | Per-project toolchains |
| Port collision | Service will not start, no clear reason | Check `ss -tlnp`, document your range |
| Disk full | Every bot fails simultaneously | Monitor, clean your own artefacts |
| Secret in history | Credential on a shared root box | Rotate it; assume disclosed |
| Root-owned artefacts | Other users cannot clean their own builds | Non-root working user (§6) |
| Blanket docker prune | Everyone's caches and volumes gone | Prune by label, your own only |
| Orphaned process | Mystery CPU or port usage | Named tmux sessions, bot-prefixed |

---

## Setup checklist

- [ ] Key-based SSH confirmed; password auth disabled
- [ ] Per-bot workspace directories created under `/workspace`
- [ ] `shared/PORTS.md` documents each bot's port range
- [ ] Toolchains per-project, not system-wide
- [ ] Disk monitoring with an alert threshold
- [ ] `shared/.env.local` at 0600, outside any git tree
- [ ] **Non-root working user created and `PermitRootLogin no` set** (§6)
- [ ] Backup for anything on the box that is not reproducible from git
</skill>
