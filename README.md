# N184 
N184 uses multi-model AI consensus (Claude, DeepSeek, GPT-4) to discover security vulnerabilities in codebases. Multiple agents independently analyze code and vote on findings, reducing false positives with actionable PRs. Named after element 184's island of stability.

**AI-powered security and bug vulnerability analysis**

---

## What is N184?

N184 is an AI-powered vulnerability discovery platform that deploys multiple AI agents to analyze codebases for security issues. 

It's power comes in a few unique features that allow it to find bugs and security vulnerabilities that are often missed.  

Specifically:
- An entire codebase is mapped out and referenced to documentation.  This allows agents to flag behavior that does not match documentation, allowing the user to either update code or documentation.
- Agents analyze git history to flag repeated errors.  If a contributor makes the same mistake over and over, we catch it.
- Multiple models are used to flag bugs based on the analysis above.
- Bug reports are analyzed for consensus, and a "devil's advocate" approach is used to push for clear PRs with steps to reproduce.
- Once the cycle of analysis is completed, the database is updated, and the agents get better at finding bugs.

N184 is not theoretical.  It has been used to find bugs and generate fixes in multiple mature codebases across the internet.

### Key Features

- **🤝 Consensus Validation**: Polling multiple models allows confirmation of high quality bugs, while offloading analysis work to cheaper models.
- **📊 Structured Output**: JSON findings with CVSS scores, CWE classifications, PoC code
- **🔒 Security First**: Podman containers, rootless architecture, isolated execution.  
- **⚖️ Professional Methodology**: Multi-phase analysis designed to help make code more stable and secure.

---

## Quick Start

### 1. Run Setup Script

```bash
# Clone repository
git clone https://github.com/MillaFleurs/N184.git
cd n184/

# Run setup (installs deps, creates ~/.n184/)
./setup.sh
```

### 2. Configure API Keys

TODO: We need to update this to allow people to choose how many agents.

Edit `~/.n184/config.yaml` with your API keys:

```yaml
api_keys:
  anthropic: "sk-ant-your-actual-key"
  deepseek: "sk-your-actual-key"
  openai: "sk-your-actual-key"
```

**Get API keys from:**
- [Anthropic Console](https://console.anthropic.com/)
- [DeepSeek Platform](https://platform.deepseek.com/)
- [OpenAI API Keys](https://platform.openai.com/api-keys)

See `config.example.yaml` for full configuration options.

### 3. Deploy Single Vautrin Agent

```bash
python3 n184-deploy-vautrin.py \
  --repo https://github.com/apache/httpd \
  --model claude
```

### 4. Deploy Full Swarm (Goriot Consensus Mode)

```bash
python3 n184-deploy-vautrin.py \
  --repo https://github.com/apache/httpd \
  --model all \
  --output httpd-findings.json
```

---

## Configuration

N184 uses `~/.n184/config.yaml` for all settings:

| Setting | Description | Default |
|---------|-------------|---------|
| `api_keys` | API keys for Claude, DeepSeek, GPT-4 | *Required* |
| `agents.vautrin.models` | Enable/disable specific models | All enabled |
| `agents.goriot.consensus_threshold` | Validation threshold (0.67 = 2/3) | 0.67 |
| `container.runtime` | Container runtime (podman/docker) | podman |
| `output.default_output_dir` | Where to save results | `~/.n184/results/` |

Full config options: `config.example.yaml`

---

## Container Deployment

### Build Podman Container

```bash
# Build n184-vautrin:latest image
./build.sh

# Or specify version tag
./build.sh v0.1.0
```

### Run Single Container

```bash
podman run --rm \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -v $(pwd)/output:/workspace/output \
  n184-vautrin:latest \
  --repo https://github.com/apache/httpd \
  --model claude
```

### Deploy Full Swarm (Docker Compose)

```bash
# Set API keys
export ANTHROPIC_API_KEY="sk-ant-..."
export DEEPSEEK_API_KEY="sk-..."
export OPENAI_API_KEY="sk-..."

# Clone repo to analyze
git clone https://github.com/apache/httpd repos/httpd

# Run all Vautrin agents in parallel
docker-compose up

# Results saved to output/
```

---
## Design Philosophy

### Agent Naming Convention

When managing a swarm of AI agents, "Scout#145" doesn't roll off the tongue. I needed a way to refer to each agent with personality and keep them distinct. To solve this, I borrowed characters from Honoré de Balzac's *La Comédie Humaine*:

- **Vautrin**: The primary analyst - sees through facades, finds what others miss
- **Goriot**: The consensus validator - patient, methodical, brings agents together
- **Rastignac**: Pattern detection - ambitious, strategic, learns from history
- **Bianchon**: Deep diagnostics - medical precision applied to code
- **Nucingen**: Risk assessment - banker's eye for CVSS scoring and impact

Each character's traits map to their function in the analysis pipeline. It's more memorable than numerical IDs and makes debugging conversations clearer: "Vautrin found it, but Goriot rejected it in consensus" is easier to parse than "Agent-001 found it, but Agent-004 rejected it."
