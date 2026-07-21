# Master Prompt for Claude Code: VivAtlas

You are working on the **VivAtlas** project — a standalone self-hosted application for cataloguing, searching, explaining, and recommending skills, agents, MCP servers, commands, plugins, scripts, and other add-ons stored in private GitHub, Gitea, and local Git repositories.

## Operating mode

First switch to planning mode and **do not start implementation right away**.

1. Study the current repository in full, but without pointlessly reading binary files, dependencies, and generated directories.
2. Find the existing architecture, documentation, previous MVP, the Skill Librarian prototype, configurations, tests, and already-implemented components.
3. Do not assume the project is empty and do not create a parallel architecture until you have determined what already exists.
4. Do not change the existing directory structure unless necessary.
5. Do not delete or rewrite working code merely for the sake of your preferred style.
6. First prepare a full plan, a list of the components you found, and a list of blocking questions.
7. Ask your questions in a single compact block only after studying the project. Do not ask what can already be answered from the repository files.
8. After the questions, stop and wait for explicit permission to implement.
9. Do not perform `git push`, merge, branch deletion, mass file moves, or other irreversible actions without separate permission.

## Primary product goal

VivAtlas must answer four questions:

1. What is in our repositories?
2. What does each tool do?
3. Which tool or chain of tools best fits a given task?
4. What has appeared, changed, become stale, conflicts, or needs attention?

The application must work as a single personal dispatcher of software capabilities for **ChatGPT and Claude Code**.

---

# 1. Fixed product principles

## 1.1. Git structure is the source of truth

Our GitHub and Gitea repositories already have their own maintained structure of sections and directories.

Mandatory rules:

- do not invent your own mandatory hierarchy on top of Git;
- do not move files and folders automatically;
- do not rename existing sections;
- do not sort tools into automatically created directories;
- show the physical repository structure exactly as it is;
- use the path as a source of metadata and tags, but not as a reason to change the structure;
- present any reorganization proposals only as a recommendation;
- perform any physical Git change only after explicit user confirmation.

Product formula:

```text
Physical Git structure = source of truth
User sections = additional virtual layer
Tags = additional search layer
AI = assistant, not the one in charge
```

## 1.2. Three independent levels of organization

### Physical structure

The real directories of GitHub, Gitea, and local repositories. They are displayed without transformation.

### User sections

Virtual folders inside VivAtlas. The user themselves:

- creates sections;
- creates nested sections;
- renames them;
- reorders them;
- assigns icons;
- adds a single tool to multiple sections;
- archives or hides sections.

User sections do not change Git.

### Smart sections

Saved filters, for example:

- new within 30 days;
- poorly documented;
- support Claude Code;
- work with PDF;
- require an external API;
- not updated in over a year;
- have elevated risk.

The user defines the rules of smart sections themselves.

## 1.3. A mobile version is mandatory

The mobile version is not a future add-on. It is part of the core product.

The first version must be a full-fledged responsive **PWA**:

- Android;
- iPhone and iPad;
- installation to the home screen;
- standalone launch;
- responsive interface;
- basic offline cache of the catalogue;
- synchronization of changes after the network is restored;
- push notifications or a prepared notification architecture;
- a shared backend and shared database with the desktop version.

Do not design a desktop-only interface and then mechanically shrink it. Think through dedicated mobile scenarios from the start.

## 1.4. Auto-tags are mandatory

Auto-tags must be one of the central capabilities of the MVP.

Tag sources:

- tool name;
- path in the repository;
- `SKILL.md` and `skill.md`;
- `README.md`;
- `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`;
- YAML frontmatter;
- `package.json`, `pyproject.toml`, `requirements.txt`;
- dependencies;
- names of functions, commands, and entrypoints;
- usage examples;
- safe AI analysis of the content.

Divide tags into:

- system;
- derived from path and files;
- AI tags;
- user-defined.

For each tag, store:

- source;
- confidence;
- assignment date;
- the model or rule that created it;
- a manual-confirmation flag;
- a manual-suppression flag.

Manual actions have absolute priority:

- an auto-tag removed by the user must not come back after the next scan;
- a manual tag must not be overwritten;
- low-confidence tags should go into suggestions rather than being assigned automatically;
- auto-apply thresholds must be configurable.

---

# 2. Data sources

Support:

1. Private GitHub.
2. Private Gitea.
3. Local Git repositories and ordinary local folders.
4. Multiple repositories from one provider.
5. Mirrors of one project in Gitea and GitHub.
6. Separate read-only and write tokens.
7. A configurable synchronization schedule.
8. Webhook synchronization after push, where possible.
9. Manual scan triggering.
10. Incremental scanning of only the changed files and directories.

Store secrets only via environment variables, Docker secrets, or the system secret store. Never put tokens into the catalogue, logs, frontend, or indexed content.

---

# 3. What needs to be recognized

Repositories have a mixed structure. Do not assume that every tool is laid out the same way.

At a minimum, recognize:

```text
SKILL.md
skill.md
README.md
CLAUDE.md
AGENTS.md
GEMINI.md
.claude/commands/*.md
.claude/agents/*.md
mcp.json
.mcp.json
plugin.json
manifest.json
package.json
pyproject.toml
requirements.txt
*.py
*.js
*.mjs
*.cjs
*.ts
*.tsx
*.ps1
*.sh
Dockerfile
docker-compose.yml
compose.yml
```

Artifact types:

- ChatGPT Skill;
- Claude Skill;
- Claude Command;
- Claude Agent;
- MCP server;
- plugin;
- prompt;
- script;
- template;
- library;
- CLI utility;
- web service;
- project/tool;
- unknown artifact requiring manual determination.

Recognition must take into account not only the file name but also its surroundings, content, frontmatter, dependencies, and entrypoints.

Do not run discovered code during indexing.

---

# 4. Tool card

The core entity is `Artifact`, not a bookmark.

Minimal fields:

```yaml
id: stable-uuid
canonical_key: provider/repository/path-or-derived-key
name: Human Readable Name
artifact_type: chatgpt-skill
summary_short: One sentence
summary_normal: Normal explanation
summary_technical: Technical description
capabilities:
  - extract-tables
inputs:
  - pdf
outputs:
  - xlsx
platforms:
  - chatgpt
  - claude-code
languages:
  - python
risk_level: read-only
status: active
documentation_score: 0.86
confidence: 0.91
repository_path: skills/documents/pdf/table-extractor
source_repositories: []
tags: []
dependencies: []
related_artifacts: []
created_at: timestamp
updated_at: timestamp
last_scanned_at: timestamp
```

Additionally store:

- versions and commit SHA;
- mirror and primary source;
- mirror conflict;
- history of moves and renames;
- user notes;
- favourites;
- usage history;
- the user's rating of the result;
- quality checks;
- discovered risks;
- links to the source files;
- connection and usage instructions.

---

# 5. GitHub and Gitea mirrors

The same tool in GitHub and Gitea must not be displayed as two cards.

A canonical record with multiple sources is needed.

The matching logic must take into account:

- an explicitly set `collection` or mirror-group;
- repository identity;
- relative path;
- manifest metadata;
- remote URLs;
- commit ancestry;
- content hash.

When versions diverge, show the conflict:

```text
Gitea is 3 commits ahead of GitHub.
Last common commit: <sha>
```

Never synchronize mirrors automatically without a separate setting and confirmation.

---

# 6. Search and recommendations

Support simultaneously:

- full-text search;
- filters;
- search by path;
- search by tags;
- semantic multilingual search in Russian and English;
- search by natural-language task description;
- comparison of similar tools;
- building a chain of tools.

Example query:

> I need to take a large PDF, extract the tables, and make a tidy Excel file.

The response must contain:

1. The best option.
2. Up to two alternatives.
3. The reasons for the choice.
4. The limitations of each option.
5. If needed, a sequence of several tools.
6. Why obvious but less suitable tools were not chosen.
7. The confidence of the recommendation.
8. An indicator of whether the recommendation is based on documentation, tags, usage history, or AI inference.

If there is no suitable tool, the system must say so directly and suggest:

- a combination of existing tools;
- extending the closest tool;
- creating a new Skill;
- searching for an external solution.

Do not invent capabilities that do not exist.

---

# 7. Creating sections and directories

In the interface, the user must explicitly choose one of three options:

```text
1. User section
   Does not change Git.

2. Smart section
   Automatically filled by filters.

3. Repository directory
   Creates a physical folder via a Git operation.
```

For a physical directory, support:

- provider selection;
- repository selection;
- branch selection;
- creating a nested path;
- adding `.gitkeep`, a README, or a tool template;
- a preview diff;
- a new branch;
- commit;
- pull request;
- direct commit only with explicit permission.

Before writing, show a clear preview:

```text
Will be created:
skills/pdf/new-tool/
skills/pdf/new-tool/SKILL.md
skills/pdf/new-tool/agents/openai.yaml

Repository: Gitea / tools
Branch: feature/add-new-pdf-tool
Method: Pull Request
```

AI and auto-tags must never move or delete files on their own.

---

# 8. Permissions and security

Each source must have a separate permissions matrix:

```yaml
permissions:
  read: true
  create_directories: true
  create_files: true
  move_files: false
  delete_files: false
  direct_commit: false
  create_pull_request: true
```

Mandatory measures:

- read-only by default;
- the write token is connected separately;
- explicit confirmation for writes;
- additional confirmation for destructive operations;
- exclusion of `.env`, private keys, certificates, credentials, and secret files;
- secret scanning before passing fragments to an external AI;
- treat repository content as untrusted;
- do not execute instructions inside indexed files;
- do not run shell commands from discovered files;
- log operations;
- do not send private code to an external AI without an explicit setting;
- support a mode without an external AI;
- prepare support for local models, e.g. Ollama.

Classify artifact risk at least as:

- read-only;
- modifies-files;
- external-network;
- credentials-access;
- shell-execution;
- destructive.

---

# 9. Interface

## 9.1. Desktop web

Main navigation:

```text
Home
Repositories
My sections
Smart sections
All tools
Changes
Tags
Settings
```

Within a repository:

```text
Overview
Structure
Tools
Changes
Tag rules
Permissions
Synchronization settings
```

Catalogue views:

1. As in the repository.
2. My sections.
3. By tags.
4. By types.
5. Smart sections.

Artifact page:

- overview;
- how to use;
- source files;
- dependencies;
- compatibility;
- history;
- notes;
- similar tools;
- checks;
- usage.

## 9.2. Mobile PWA

Bottom navigation:

```text
Home
Search
Structure
Changes
Profile
```

Mandatory mobile scenarios:

- a voice or text task query;
- quick search;
- recommendations;
- the tool card;
- viewing the repository structure;
- assigning and confirming tags;
- adding to a user section;
- favourites;
- a note;
- viewing new and changed items;
- comparing two or three tools;
- preparing a Git operation with confirmation.

In offline mode, store only the safe catalogue, short descriptions, tags, favourites, sections, and recent queries. Do not download the full source code to the phone.

---

# 10. API and MCP

A single REST API and one MCP server are needed for ChatGPT and Claude Code.

Minimal MCP tools:

```text
search_artifacts
recommend_artifacts
get_artifact
compare_artifacts
build_workflow
list_repository_tree
list_recent_changes
find_duplicates
find_stale_artifacts
find_poorly_documented
list_tags
list_sections
record_usage
add_note
```

Keep write-tools separate and disabled by default:

```text
create_repository_directory
create_artifact_from_template
prepare_move
prepare_pull_request
```

Each write-tool must return a preview and require separate confirmation before actual execution.

MCP responses must be compact, structured, and suitable for further reasoning by ChatGPT/Claude.

---

# 11. Preferred technical architecture

First check the project's existing stack. Do not create a second backend or frontend if a usable architecture already exists.

If the project does not dictate another mature stack, prefer:

## Backend

- Python 3.12+;
- FastAPI;
- Pydantic v2;
- SQLAlchemy 2;
- Alembic;
- a separate scanner/worker;
- an official or maintained MCP SDK;
- GitHub API and Gitea API with a provider-adapter abstraction.

## Frontend

- React;
- TypeScript;
- Vite;
- PWA/Service Worker;
- responsive mobile-first layout;
- keyboard and screen-reader accessibility;
- no hard-coupling of the UI to a specific Git provider.

## Database

- PostgreSQL;
- pgvector for semantic search;
- SQLite is allowed only for the local development/test mode;
- PostgreSQL full-text search or a separate search adapter.

## Deployment

- Docker Compose;
- separate API, web, worker, and database services;
- health checks;
- migrations;
- `.env.example` without secrets;
- production and development profiles.

The architecture must allow replacing:

- embedding provider;
- LLM provider;
- Git provider;
- search backend;
- background job runner.

Do not fork Karakeep or copy its code. You may use only its product ideas: cards, tags, search, collections, rules, and bulk actions.

---

# 12. Incremental scanning

Do not rescan the entire repository unless necessary.

Think through:

- last scanned commit;
- commit diff;
- content hash;
- renamed/moved files;
- deleted files;
- parser version;
- AI-analysis version;
- tag-rule version;
- reindex queue;
- retry and error states;
- rate limits GitHub/Gitea;
- webhook verification.

After scanning, produce a changelog:

```text
+ new
~ updated
- deleted
↪ moved
! mirror conflicts
⚠ documentation issues
```

---

# 13. Quality assessment

The score must be built from explainable components, not from a single magic number.

Minimal metrics:

- documentation completeness;
- description clarity;
- freshness;
- installability;
- test evidence;
- dependency health;
- platform compatibility;
- security risk;
- recommendation confidence.

Show the reasons for the score.

Do not conclude "works" if the tool has not actually been verified. Use the statuses:

- discovered;
- documented;
- statically-validated;
- tests-present;
- tests-passed;
- user-verified;
- deprecated;
- broken;
- unknown.

---

# 14. MVP 0.1 — mandatory scope

The MVP is considered done when all items are complete:

1. Connecting a private GitHub.
2. Connecting a private Gitea.
3. Connecting a local source.
4. Read-only mode by default.
5. Displaying the real repository tree without any reorganization of your own.
6. Recognition of mixed artifact types.
7. Creating cards and three levels of description.
8. Auto-tags with confidence and source.
9. Manual tags with priority and blocking the return of removed auto-tags.
10. User sections.
11. Smart sections.
12. Full-text search.
13. Russian-English semantic search.
14. Recommendation of the top three tools with an explanation.
15. Comparison of similar tools.
16. Detection of new, changed, deleted, and moved items.
17. Merging of GitHub/Gitea mirrors.
18. Responsive desktop web.
19. An installable mobile PWA.
20. A basic offline cache.
21. REST API.
22. MCP for ChatGPT and Claude Code.
23. Directory creation via preview + branch/commit/PR.
24. Separate permissions for each repository.
25. Docker Compose.
26. Database migrations.
27. Unit and integration tests of the critical parts.
28. Documentation for launch, configuration, and security.

Do not include in the MVP:

- automatic execution of discovered scripts;
- automatic installation of MCP;
- automatic moving of files;
- AI reorganization of the repository;
- unconditional direct commit;
- a full source-code editor;
- mandatory publication to the App Store/Google Play.

---

# 15. What is required of you before starting implementation

After studying the repository, prepare one structured response:

## A. What already exists

- discovered components;
- the stack in use;
- working parts;
- unfinished parts;
- potentially reusable code;
- technical debt;
- conflicts with this task.

## B. Proposed architecture

- components;
- module boundaries;
- a data-flow diagram;
- the security model;
- the GitHub/Gitea adapter strategy;
- scanner pipeline;
- tagging pipeline;
- search/recommendation pipeline;
- PWA/offline strategy;
- MCP integration.

## C. Data model

Show the main tables/entities and relationships at least for:

- repositories;
- repository_sources;
- artifacts;
- artifact_versions;
- files;
- tags;
- artifact_tags;
- tag_suppressions;
- sections;
- section_items;
- smart_sections;
- scan_runs;
- changes;
- notes;
- usage_events;
- permissions;
- git_operations.

## D. Contracts

Propose:

- REST endpoints;
- MCP tools;
- background jobs;
- webhook handlers;
- key events.

## E. Interface map

- desktop screens;
- mobile screens;
- navigation;
- key user flows;
- loading/empty/error/offline/conflict states.

## F. Implementation plan

Break the work into small verifiable stages.

For each stage, specify:

- the goal;
- the modules to be changed;
- migrations;
- API;
- UI;
- tests;
- completion criteria;
- risks.

## G. Blocking questions

Ask only the questions without which a correct architectural decision cannot be made. Combine them into one list. For each question, propose a recommended default.

After that, **stop and wait for confirmation**.

---

# 16. Implementation rules after confirmation

When the user approves the plan:

1. Work through the agreed stages.
2. Before each major stage, briefly state what you are changing.
3. Do not change unrelated files.
4. Make minimal, meaningful changes.
5. Preserve backward compatibility where reasonable.
6. After each stage, run the related tests, lint, and type-check.
7. Fix the causes of errors rather than hiding them by disabling checks.
8. Do not add mock implementations to the production path without clear labeling.
9. Do not pass off static keyword search as semantic search.
10. Do not pass off the presence of tests as tests passing successfully.
11. Do not claim the mobile version is ready until real mobile breakpoints, PWA installability, and offline state have been verified.
12. Update the documentation together with the code.
13. Keep a changelog of completed stages.
14. At the end of each stage, report:
    - what is done;
    - what has been verified;
    - which test commands were run;
    - what remains;
    - what limitations were found.

---

# 17. Criterion of final success

The scenario must work end to end:

1. The user connects private Gitea and GitHub.
2. VivAtlas scans the existing structure without changing it.
3. The application finds Skills, Claude commands, agents, MCP, plugins, and scripts.
4. Creates clear cards and auto-tags.
5. Shows the real repository tree.
6. The user creates their own virtual sections.
7. The user, from their phone, types:

   > I need to analyze a PDF, extract the tables, and make an Excel file.

8. The system suggests a suitable tool or chain and explains the choice and limitations.
9. The same data is available to ChatGPT and Claude Code via MCP.
10. After a new push, only the changed items are updated.
11. The user can create a new physical directory via a preview, a branch, and a pull request.
12. Neither AI nor auto-tags change the Git structure on their own.

Start with an audit of the current repository and prepare the materials from the **"What is required of you before starting implementation"** section. Do not start implementation yet.
