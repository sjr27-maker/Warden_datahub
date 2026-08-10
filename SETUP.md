# Warden — Local Development Setup

Getting a working Warden dev environment on Windows. Written after doing it once, so
the gotchas below are real ones that cost time, not hypothetical.

**Estimated time:** 60–90 minutes, most of it waiting on Docker image pulls.

---

## What you're setting up

Warden is a CI-time code generation agent for DataHub. It needs four things running
locally before any agent code can be written:

1. **WSL2 + Ubuntu** — everything runs in Linux, not native Windows
2. **DataHub OSS** — the metadata platform, via Docker
3. **mcp-server-datahub** — how Warden reads and writes the graph
4. **A Python 3.11 venv** — with dbt, DuckDB, and the DataHub SDK

The critical dependency is #3. If MCP can't reach DataHub, nothing else matters —
validate that before building anything on top of it.

---

## Phase 1 — WSL2 and Docker

### Install WSL2

In **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-22.04
wsl --set-default-version 2
```

**Restart Windows.** Not optional — WSL enables Windows features that only activate
after a reboot. Skipping this produces confusing hangs later.

After reboot, Ubuntu launches and prompts for a UNIX username and password. This is
separate from your Windows login; you'll use it for `sudo`.

### Allocate memory

DataHub runs OpenSearch, MySQL, and Kafka simultaneously. Starved of RAM it will
thrash or fail silently.

**Docker Desktop hides the memory slider when using the WSL2 backend.** You configure
it in `.wslconfig` instead. In PowerShell:

```powershell
notepad "$env:USERPROFILE\.wslconfig"
```

Create the file if prompted, and paste:

```ini
[wsl2]
memory=10GB
processors=4
swap=4GB
```

Adjust `memory` to your total RAM minus a few GB for Windows. 10GB works on a 16GB
machine.

Then:

```powershell
wsl --shutdown
```

Wait ~10 seconds, start Docker Desktop.

### Enable Docker's WSL integration

Docker Desktop → **Settings → Resources → WSL Integration** → enable `Ubuntu-22.04`.

### Verify

Inside Ubuntu:

```bash
free -h     # total should match your .wslconfig setting, not full system RAM
docker ps   # should run without error
```

If `docker ps` errors, the WSL integration toggle didn't take effect.

---

## Phase 2 — Python 3.11

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y software-properties-common curl git build-essential
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
python3.11 --version
```

Python 3.11 specifically — some DataHub dependencies lag on 3.12+.

---

## Phase 3 — Project and VS Code

### Clone

```bash
cd ~
git clone https://github.com/<org>/Warden_datahub.git warden
cd warden
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

**Keep the project in the WSL filesystem (`~/`), never in `/mnt/c/`.** Cross-boundary
file I/O is roughly 10x slower and breaks file watching.

### VS Code

Install the **WSL extension** (Microsoft) in VS Code on Windows. Then from the Ubuntu
terminal:

```bash
cd ~/warden
code .
```

**Verify you're actually connected to WSL** — the bottom-left status bar must show a
green `WSL: Ubuntu-22.04` badge. If it doesn't, `Ctrl+Shift+P` →
`WSL: Reopen Folder in WSL`.

Quick check in the integrated terminal:

```bash
pwd        # /home/<you>/warden
uname -a   # mentions Linux and microsoft
```

If `uname` isn't found, you're in PowerShell browsing WSL files over a network share,
not in a Linux shell. See gotchas below.

### Select the interpreter

`Ctrl+Shift+P` → **Python: Select Interpreter** → `./.venv/bin/python`

---

## Phase 4 — DataHub with authentication enabled

### Install the CLI

```bash
source .venv/bin/activate
pip install acryl-datahub
datahub version
```

### Create the auth-enabled compose file

Metadata Service Authentication is **off by default** in OSS, and you need it on to
generate the API token that MCP requires. The quickstart compose file hardcodes it to
`false`, and re-downloads itself on every run — so an env var won't override it and
editing the original won't survive.

First bring it up once to get the compose file, then stop it:

```bash
datahub docker quickstart
datahub docker quickstart --stop
cp ~/.datahub/quickstart/docker-compose.yml ~/datahub-auth-compose.yml
```

Edit your copy:

```bash
code ~/datahub-auth-compose.yml
```

- Under the **GMS** service (`datahub-gms-quickstart`): change
  `METADATA_SERVICE_AUTH_ENABLED: 'false'` → `'true'`
- Under the **frontend** service (`frontend-quickstart`): add
  `METADATA_SERVICE_AUTH_ENABLED: 'true'` to its `environment:` block

### Launch with your file

```bash
datahub docker quickstart -f ~/datahub-auth-compose.yml
```

First run pulls a lot of images — 10–20 minutes. Don't interrupt it even if it looks
stalled.

**Set up an alias so you never forget the `-f` flag:**

```bash
echo "alias datahub-up='datahub docker quickstart -f ~/datahub-auth-compose.yml'" >> ~/.bashrc
source ~/.bashrc
```

Running plain `datahub docker quickstart` re-downloads the default compose and silently
loses your auth setting.

### Verify

```bash
docker ps
```

Expect six containers:

| Container | Image | Port |
|---|---|---|
| `datahub-gms` | `acryldata/datahub-gms` | 8080, 4319 |
| `datahub-frontend-react` | `acryldata/datahub-frontend-react` | 9002 |
| `datahub-actions` | `acryldata/datahub-actions` | — |
| `broker` | `confluentinc/cp-kafka` | 9092 |
| `mysql` | `mysql:8.2` | 3306 |
| `opensearch` | `opensearchproject/opensearch` | 9200 |

Open `http://localhost:9002` in your Windows browser (WSL forwards ports
automatically). Login `datahub` / `datahub`.

### Generate an access token

**Settings → Access Tokens → Generate new token.** The button is enabled only because
of the auth change above; if it's greyed out, that step didn't take.

Copy the token immediately — it's shown once.

---

## Phase 5 — MCP server

### Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv --version
```

### Configure environment

```bash
cd ~/warden
cat > .env << 'EOF'
DATAHUB_GMS_URL=http://localhost:8080
DATAHUB_GMS_TOKEN=
TOOLS_IS_MUTATION_ENABLED=true
EOF
```

Paste your token into `DATAHUB_GMS_TOKEN`. Confirm `.env` is gitignored.

`TOOLS_IS_MUTATION_ENABLED` is **required** — it defaults to false, and without it the
server registers read tools only. Warden's Scribe needs writes.

### Run it

```bash
cd ~/warden
set -a && source .env && set +a
uvx mcp-server-datahub@latest
```

`set -a` exports sourced variables so they reach the subprocess.

### What a healthy startup looks like

```
register_all_tools:425      - Registering MCP tools (is_oss=True)
register_mutation_tools:230 - Mutation Tools ENABLED MCP Server.
register_mutation_tools:268 - Save Document ENABLED - registering save_document tool
register_user_tools:288     - User Tools DISABLED MCP Server.
register_data_quality_tools:395 - Data Quality Tools DISABLED MCP Server.
Starting MCP server 'datahub' with transport 'stdio'
```

**Mutation Tools ENABLED** and **Save Document ENABLED** are the lines that matter.
User tools and data quality tools stay disabled deliberately — Warden doesn't need them,
and a smaller tool surface means less for the LLM to misuse.

The process then sits waiting on stdio. That's correct, not a hang.

An `ExperimentalWarning` about `datahub.sdk` import paths is expected and harmless.

---

## Phase 6 — Remaining dependencies

```bash
source .venv/bin/activate
pip install dbt-core dbt-duckdb duckdb pandas faker
pip install mcp anthropic google-generativeai
sudo apt install -y gh
gh auth login
```

For `gh auth login`: GitHub.com → HTTPS → login with browser. **Make sure the browser
is logged into the correct GitHub account** — see gotchas.

### LLM backend

Local Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull llama3.2
```

Or Gemini free tier — set `GEMINI_API_KEY` in `.env`.

Either way, Warden has a deterministic fallback path so a missing key never breaks a run.

---

## Daily workflow

**Start of session:**

```bash
datahub-up                    # uses the auth compose file
# wait for containers to be healthy
docker ps
```

**End of session:**

```bash
datahub docker quickstart --stop
```

This preserves your data — token, ingested metadata, everything. Do **not** run
`datahub docker nuke` or `docker system prune` unless you intend to wipe the catalog and
regenerate the token.

DataHub uses meaningful RAM even when idle. If your machine gets sluggish during coding
sessions where you aren't hitting the catalog, stopping it is reasonable.

---

## Known gotchas

| Symptom | Cause | Fix |
|---|---|---|
| No memory slider in Docker Desktop | WSL2 backend manages resources via Windows | Edit `%USERPROFILE%\.wslconfig`, then `wsl --shutdown` |
| `uname: command not found` in VS Code terminal | Window opened as Windows-local, browsing WSL over `\\wsl.localhost\` | `Ctrl+Shift+P` → `WSL: Reopen Folder in WSL` |
| "Generate new token" greyed out | `METADATA_SERVICE_AUTH_ENABLED` is false | Edit the compose copy on **both** gms and frontend, relaunch with `-f` |
| Auth setting reverts after restart | Plain `quickstart` re-downloads the default compose | Always launch via the `datahub-up` alias |
| `Mutation Tools DISABLED` | `TOOLS_IS_MUTATION_ENABLED` not set or not exported | `set -a && source .env && set +a` before starting the server |
| `Permission denied to <wrong-user>` on git push | Cached credentials for a different GitHub account | `git config --global --unset credential.helper`, then `gh auth login` |
| Repo bloated with thousands of files | `.venv` committed before `.gitignore` existed | `git rm -r --cached .venv`, confirm `.venv/` is in `.gitignore`, commit |
| Port 8080 or 9092 already in use | Another service holds it | `DATAHUB_MAPPED_GMS_PORT=8082 datahub-up`, carry the new port everywhere |

### Known MCP limitations

Documented by another hackathon entrant in
[mcp-server-datahub#141](https://github.com/acryldata/mcp-server-datahub/pull/141).
Read these before writing agent code:

1. **`search` is eventually consistent after a write.** A freshly written entity may not
   come back immediately. Retry before assuming your code is broken.
2. **`get_lineage` doesn't return relationships modeled as entity aspects.** An
   MLModel's deployments and features fall in this category — use `get_entities` for
   those.
3. **Column-level lineage is stored correctly but not traversable via any MCP tool.**
   `get_lineage_paths_between` falls back to dataset-level. This constrains how precise
   blast radius analysis can be.

---

## Verification checklist

Before writing any agent code, all of these should pass:

```bash
free -h                                  # matches .wslconfig
docker ps                                # 6 healthy containers
curl http://localhost:8080/health        # GMS responding
python3.11 -c "import dbt.version"       # dbt importable
uv --version                             # uv installed
gh auth status                           # correct GitHub account
```

Plus: `localhost:9002` loads, a token exists under Settings → Access Tokens, and the MCP
server logs `Mutation Tools ENABLED`.

---

## Working with Claude on this project

Claude Code or the desktop app can help with setup and debugging. To give it useful
context, paste this:

> I'm working on Warden, a CI-time code generation agent for DataHub. Environment:
> Windows with WSL2 (Ubuntu 22.04), project at `~/warden`, Python 3.11 in `.venv`.
> DataHub OSS runs via `datahub docker quickstart -f ~/datahub-auth-compose.yml` (a
> modified compose with `METADATA_SERVICE_AUTH_ENABLED: 'true'` on both gms and
> frontend). MCP access is through `uvx mcp-server-datahub@latest` with
> `DATAHUB_GMS_URL`, `DATAHUB_GMS_TOKEN`, and `TOOLS_IS_MUTATION_ENABLED=true` from
> `.env`. Read `SETUP.md` in the repo for the full setup and known gotchas.

Two things worth telling it explicitly when asking for help:

- **Commands must run in the WSL terminal**, not PowerShell. If it suggests Windows
  paths or PowerShell syntax, correct it.
- **Don't trust remembered MCP commands.** `mcp-server-datahub`'s invocation has
  changed between versions. Check the live README at
  `github.com/acryldata/mcp-server-datahub` when something doesn't work.