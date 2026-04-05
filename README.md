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
- A "librarian" checks documentation to confirm where a program differs from documented behavior.
- Once the cycle of analysis is completed, the database is updated, and the agents get better at finding bugs.

N184 is not theoretical.  It has been used to find bugs and generate fixes in multiple mature codebases across the internet.

### Key Features

- **🤝 Consensus Validation**: Polling multiple models allows confirmation of high quality bugs, while offloading analysis work to cheaper models.
- **📊 Structured Output**: JSON findings with CVSS scores, CWE classifications, PoC code
- **🔒 Security First**: Podman containers, rootless architecture, isolated execution.  
- **⚖️ Professional Methodology**: Multi-phase analysis designed to help make code more stable and secure.

---
## Requirements

### System Requirements

- **Container Runtime**: Podman 4.0+ or Docker 20.10+
  - Podman recommended for rootless execution
  - Install: [podman.io/get-started](https://podman.io/get-started)
  
- **Python**: 3.11 or higher
  - Check version: `python3 --version`

- **Git**: For cloning repositories to analyze
  - Install: `git --version` to check

- **NanoClaw**: Nanoclaw is the agentic solution used on the backend.  If you do not have it downloaded it will be downloaded for you and installed for you.

- **Communications Platform**: We use Telegram in this example but you need a way to communicate with N184.  This could be any platform supported by NanoClaw.

- **Other**: N184 can check a variety of codebases, which may require codebase specific software.  (e.g. to test Clojure based software, you'll need to install Clojure).

- **HIL**: The most imporant requirement for N184 is the Human In the Loop.  The HIL is responsible for providing feedback to the N184 Agent, which it will learn from.

### Python Dependencies

Install via `requirements.txt`:

```bash
pip install -r requirements.txt
```

**Core dependencies:**
- `pydantic-ai>=1.0.0` - AI agent framework
- `pydantic>=2.0.0` - Data validation
- `pyyaml>=6.0` - Configuration parsing
- `anthropic>=0.86.0` - Claude API client
- `openai>=1.0.0` - GPT-4 and DeepSeek API client

### API Keys (Required)

N184 requires at least one API key to function:

- **Anthropic** (Claude): [console.anthropic.com](https://console.anthropic.com/)
- **DeepSeek**: [platform.deepseek.com](https://platform.deepseek.com/)
- **OpenAI** (GPT-5): [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- **Ollama** (Any): You can install your own models using Ollama (https://ollama.com/)

**Cost estimate:** Costs vary based upon model used and API used.

### Optional

- **Docker Compose**: For multi-agent parallel execution
  - Install: `docker-compose --version` to check
  
- **Storage**: ~2-5GB per repository clone (cached in `~/.n184/cache/`)

---

## Quick Start

### 0. Download

```bash
$ git clone https://github.com/MillaFleurs/N184.git
```

### 1. Configure Environment
```bash
$ cp ./.env.example ./.env
```

Open ```.env.``` in your favorite text editor and provide any necessary API keys.

**Get API keys from:**
- [Anthropic Console](https://console.anthropic.com/)
- [DeepSeek Platform](https://platform.deepseek.com/)
- [OpenAI API Keys](https://platform.openai.com/api-keys)

See `.env.example` for full configuration options.

### 2. Run Setup Script

```bash

$ ./init.sh

```

### 3. Optional: Troubleshoot or make updates to the default configuration

Thanks to the flexibility of NanoClaw, from the root directory of your nanoclaw install you can invoke a Claude code session with ```claude``` and make any changes you desire (teaching new skills for instance).  You can also use this to troubleshoot if you are unable to communicate with N184.

### 4. Start N184

By default, N184 uses a Telegram connection to your podman container.  This requires asking the blessing of @BotFather on Telegram to get an API token, and filling in the .env file correctly.

If you have done all steps correctly, you should be able to message your bot on Telegram and say "Hello" or get down to working.

If this is not working, please invoke ```claude``` and troubleshoot.  You can ask Honoré to kick off a swarm, or just chat with him like any normal agent.  (Hint: he likes Turkish Coffee)  Once you're able to chat with Honoré, you can move forward with any 

### 5. Analysis

If you provide Honoré a git hub link or other location, he will be able to clone the repository and kick off swarm consensus analysis. 

By design Honoré works in a Podman rootless container, which means he may be limited in what he can do within his container without explicitly updating it.

Podman provides a number of methods to collaborate with Honoré with minimal system risk.  (Another reason why we chose NanoClaw as our base).

As an example, I asked Honoré to review a git repository with a full swarm review.  He confirmed all environmental variables and API keys were working and he was able to spin up DeepSeek, Claude, ChatGPT, and other agents.

Once done, I would normally do something like below:

```
# podman provies a list of all containers running.  Which gets me **my** container d940146efaf6
$ podman ps

ONTAINER ID  IMAGE                                 COMMAND          CREATED        STATUS               PORTS                                 NAMES
d940146efaf6  localhost/nanoclaw-agent:latest                        8 seconds ago  Up 8 seconds                                               nanoclaw-main-1775418882653

# let's go look around the container
$ podman exec -it d940146efaf6  /bin/bash
$ ls
# I see results here and decide what I want, I'm going to copy it now...
$ tar cvf ./myresults.tar.gz ./some-directory
$ exit

# back outside of the pod, I can now copy the file I created
$ podman cp d940146efaf6:/workspace/group/myrseults.tar.gz ./

```

There are a variety of other ways one might collaborate with their agent as well outside the scope of this document.  For instance, you can set up a shared drive that Honoré has access to, provide Honoré access to ftp or email, or any number of things.

We hope you enjoy working with Honoré.  Please reach out with any questions at our github site.

We also welcome any collaboration or attributions.

---
## Design Philosophy

### Agent Naming Convention

When managing a swarm of AI agents, "Scout#145" doesn't roll off the tongue. I needed a way to refer to each agent with personality and keep them distinct. To solve this, I borrowed characters from Honoré de Balzac's *La Comédie Humaine*:

- **Vautrin**: The primary analyst - sees through facades, finds what others miss
- **Goriot**: The consensus validator - patient, methodical, brings agents together
- **Rastignac**: Pattern detection - ambitious, strategic, learns from history
- **Bianchon**: Deep diagnostics - medical precision applied to code.  Our "librarian" responsible for checking documentation vs findings.
- **Nucingen**: Risk assessment - banker's eye for CVSS scoring and impact (TODO)


Each character's traits map to their function in the analysis pipeline. It's more memorable than numerical IDs and makes debugging conversations clearer: "Vautrin found it, but Goriot rejected it in consensus" is easier to parse than "Agent-001 found it, but Agent-004 rejected it."



---
# Authors
N184 was created through the cowork of A.L. Figaro and Dan Anderson (https://github.com/MillaFleurs)

# License
See LICENSE.  This software is distributed under the terms of the GNU Affero General Public License v. 3.0.
