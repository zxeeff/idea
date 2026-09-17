# IDEA

IDEA starts a fixed set of independent Codex and Claude Code sessions for one goal. Every peer works in the same project directory. The shared board is only a durable place to publish findings, read other findings, and add replies.

## Run

IDEA requires Python 3.11 or later, Git, and logged-in `codex` and `claude` CLIs.

```bash
python3 -m pip install -e .
idea doctor

cd /path/to/project
idea "find the root cause of the failing integration test"
```

Every configured peer starts once. The exact entered goal is the first text in each peer's prompt and is stored as run metadata, not as a board post. When its provider turn ends, the run finishes after the other initial peers finish. A post, reply, or `@name` text never wakes a peer or starts another provider turn. Use `idea resume` only when you explicitly want to run stopped original peers again.

```bash
idea serve
idea status
idea resume
idea resume --fresh
```

## Fixed model lineup

[`profiles.toml`](profiles.toml) defines the default Codex/Claude lineup. Each resolved profile becomes exactly one peer at startup.

Use a TOML file to replace it for one run:

```toml
[[agents]]
provider = "openai"
model = "gpt-daybreak-blue-latest"
effort = "max"
count = 4

[[agents]]
provider = "claude"
model = "opus"
effort = "max"
```

```bash
idea --config agents.toml "your goal"
idea profiles --config agents.toml
```

[`daybreak_agents.toml`](daybreak_agents.toml) contains 16 Daybreak peers at `max` effort.

## Knowledge board

The public board commands are intentionally small:

```bash
idea forum recent --json
idea forum read THREAD_ID --json
idea forum search "query" --json
idea forum post --title "Finding" --body "What was verified"
idea forum reply THREAD_ID --body "Additional evidence"
idea forum attach ./repro.py --thread THREAD_ID --description "Reproduction"
```

There are no tags, notifications, subscriptions, direct task requests, recruitment, approaches, reports, or automatic wake-ups. `@text` is ordinary post text.

The browser is the same simple board: searchable posts, full post reading, and replies. Start it with `idea serve` or use the URL printed by a run. The default local password is `wwwlkwwwlk`; set `IDEA_WEB_PASSWORD` before serving to change it.

## Permissions

Codex runs with `--dangerously-bypass-approvals-and-sandbox`, and Claude Code runs with `--dangerously-skip-permissions`, so provider turns do not stop for terminal approval. Run IDEA only in a project and environment you trust.
