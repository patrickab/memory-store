# gigachad-bot on memory-store — plan

Replace gigachad-bot's own memory system (Postgres documents, LLM extraction, review, regroup,
manual ranking, slash commands) with `memory-store`. gigachad keeps two
things: the memory tools in chat and a memory window to inspect and edit memories.

Precondition: memory-store stages 1-2 landed and installable as a Python dependency.

## Target

| Surface | How it gets memory |
|---|---|
| Chat (local completions) | Native `ToolDefinition`s in `lib/tools.py` wrapping `memory_store.store` directly, behind one **Memory** toggle in the composer's tool menu, for every login. Session start injects the memory index into the system prompt. |
| Pi tabs (pi-host `coding`) | Pi loads `~/.pi/agent/mcp.json` itself: nothing to build in gigachad. Uses the default disk root, so it shares the host login's memories. |
| Memory window (sidebar brain button) | Browses the login's root: memory_stores, memories, facts, documents; edit, retire, show retired. Reads/writes through a thin REST route over `memory_store.store`. |

### One root per login

| Login | Root |
|---|---|
| host | `~/.pi/memory_store`: the same one Pi CLI, pi-host and subagents use |
| every other login | `<repo>/.memory_store/<user-id>/`, relative to the running checkout |

- `memory_context.memory_root(login)` is the one place that decides; tools, injection and the
  window all call it.
- `.memory_store/` goes into `.gitignore`: family memories are never committed. `redeploy.sh`
  ignores untracked files, so deploys neither block on nor touch it.
- Dev (`~/git/gigachad-bot`) and production (`~/git/gigachad-bot-prod`) each have their own
  family roots; the host root is shared by both (one `~/.pi`).
- Factory task previews run from their worktree and see empty family roots.
- Tests: every root points into a tmp directory.

### Chat tools

The Memory toggle enables `list_memories`, `read_memories`, `apply_memory_ops`.
`create_memory_store` is not offered in chat: chats work in `user` and their project, created on
first use.

### Session start injection (replaces `memory_context.augment_system_prompt`)

Only while the Memory toggle is on:

- `user/profile` and `user/preferences` rendered in full, `ids=false`
- `list_memories("user")` and `list_memories("<project>")` as the index
- one line: the memory_stores to use (`user`, `<project>`) and "read before you answer from memory"

`<project>` = last segment of the project's linked folder when it has one, else its slug.

## Delete

Line counts as of 2026-11-24, about 2,700 lines plus test edits.

### Backend
| File | Why |
|---|---|
| `src/lib/memory_extract.py` (340) | LLM extraction and regroup |
| `src/lib/memory_store.py` (273) | document-based store, replaced by `memory_store.store` |
| `src/lib/memory_context.py` (35) | replaced by the session start injection |
| `src/backend/routes/memory.py` (134) | extract/commit/cancel/regroup/topics/memories routes; replaced by a small new route (below) |
| `tests/test_memory_store.py` (154) | tests the deleted store |

### Frontend
| File | Why |
|---|---|
| `features/memory/useMemorizeFlow.ts` (189) | `/memorize` flow |
| `features/memory/MemoryPanel.tsx` (185) | review screen |
| `features/memory/useMemoryCommandBar.ts` (69) | memorize command bar |
| `features/memory/RegroupPage.tsx` (52) | LLM regroup |
| `features/memory/RankingPage.tsx` (46) | manual ranking; order is now recency |
| `features/memory/scopeEdits.ts` (111) | topic/fact edit queue for the old scope model |
| `app/_components/MemoryCommandLayer.tsx` (38) | hosts command menu + review panel |
| `features/navigation/useSlashCommands.ts` (47) + its test (50) | all slash commands go (`/memorize`, `/files-load`) |
| `features/navigation/CommandMenu.tsx` (71) | only used by `MemoryCommandLayer` |

`/files-load` loses nothing: the attach menu's "Mounted file" entry opens the same picker
(`useTabChat.ts`, `attachMenuItems`).

### Rewrite (keep the shell, change the data model)
| File | Change |
|---|---|
| `features/memory/MemoryViewer.tsx`, `Overview.tsx`, `TopicPage.tsx` | memory_stores → memories → facts instead of scopes → topics → facts |
| `features/memory/useMemoryLibrary.ts` + test, `memoryUtils.ts` | load/edit via the new route |
| `lib/backend/api/memory.ts`, `lib/domain/memory.ts` | new route and types (memory_store, memory, fact, document) |
| `EditableText.tsx`, `MemoryChrome.tsx`, `MemoryViewerContext.tsx`, `styles.ts` | keep as they are |

### Edits in other files
| Where | Change |
|---|---|
| `src/backend/routes/chat.py` | drop `augment_system_prompt`/`MemoryStoreDep`, call the new injection |
| `src/backend/routes/deps.py` | drop `get_memory_store` |
| `src/backend/server.py` | swap the memory router for the new one |
| `src/lib/tools.py` + `lib/toolcalling` catalog | add the three memory `ToolDefinition`s |
| `src/frontend/features/chat/tools.ts`, `lib/domain/types.ts` (`ToolName`) | add the `memory` toggle |
| `src/lib/storage_namespace.py` | drop `memory_topics_key`, `memory_memories_key`, `_memory_project_dir` |
| `src/lib/project_rename.py` | drop `memory/project` from the moved namespaces; `move_memory_store(old, new)` in the login's root after the rename transaction commits |
| `src/config.py` `MODEL_DEFAULT_KEYS`, `routes/models.py`, `features/settings/ModelSettingsPanel.tsx`, `lib/domain/types.ts` | drop `memory_model`; a stored value is ignored |
| `app/_components/useTabChat.ts`, `TabContent.tsx` | drop memory command bar, slash commands, `shellCommands` |
| `features/pi/PiTab.tsx` | drop `shellCommands` (`/memorize` in the Pi composer) and the confirmed-messages registry kept for memorization |
| `app/globals.css` | drop the `MemoryPanel` z-index note |
| tests | `test_chat_route.py`, `test_project_rename.py`, `test_store_io_off_loop.py` (memory parts), frontend tests mocking memory/slash commands, `memory_model` in model tests |
| docs | `.docs/system-overview.md` "Memory system", `.docs/architecture.md` "Memory and deletion semantics" + providers row, `.docs/module-map.md` |

### Database
- Leave `db/schema/0010_memory_topics.sql` (applied history).
- After the migration below: a new schema step deletes the old `memory/*` documents.

## Add

| What | Where |
|---|---|
| Dependency `memory-store` | `pyproject.toml` |
| `.memory_store/` | `.gitignore` |
| Memory tools (3 `ToolDefinition`s) | `src/lib/tools.py` |
| Session start injection | `src/lib/memory_context.py` (rewritten, ~30 lines) |
| REST route for the window: list/read/apply in the login's root | `src/backend/routes/memory.py` (rewritten, thin) |
| Tests | all roots in a tmp directory |

## Migration (one-time script, every login)

- into the login's root
- global topic `<name>` → `user/<name>`, project topic → `<project>/<name>`
- topic summary → `description`; each fact → one active fact; stored order → `updated`
  (first = newest), so recency keeps today's relevance order
- dry run prints the result, a second run writes; the old documents stay until the cleanup step

## Stages

1. **memory-store in, injection swapped:** dependency, `.gitignore`, migration script, new
   `memory_context.py`, chat tools + toggle. Old UI still works on the old data (read-only in
   practice).
2. **Window rewritten:** new route, viewer on memory_stores/memories/facts.
3. **Delete:** everything in "Delete", the `memory_model` default, slash commands, docs updated,
   cleanup schema step.

Acceptance (each stage): `factory/gates.sh` green. End to end: a fact added from Pi CLI shows in
the host's memory window and in a new chat's injected index; updating it in the window retires the
old fact; a second login sees none of it and its own facts land in
`.memory_store/<user-id>/`.
