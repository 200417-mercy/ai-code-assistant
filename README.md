# AI Code Assistant

AI-powered coding workspace for developers who want faster project understanding, repository analysis, GitHub-aware workflows, and smarter code assistance.

> Turn GitHub repos, project context, code review, and AI chat into a single developer workflow built for real engineering teams.

## Why this project matters

- Most AI coding tools are isolated from project context.
- Real developer work happens across files, repos, PRs, issues, and team workflows.
- This project brings AI into the full software lifecycle: repo intelligence, project chat, code review, collaboration, and plugin-based extensibility.

## What it does

- Chat with AI over project and repository context
- Analyze GitHub repositories, issues, and pull requests
- Work with imported project files and searchable workspace content
- Run AI code review and quality analysis over a project or PR
- Collaborate with team members and manage workspace permissions
- Extend the platform through plugin and capability-based architecture
- Support Stellar / Soroban-aware analysis for blockchain projects

## Try it in 5 minutes

```bash
# 1. Clone the project
git clone https://github.com/AyinkxLab/ai-code-assistant.git
cd ai-code-assistant

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements-dev.txt

# 4. Configure environment
cp .env.example .env

# 5. Run the app
python run.py
```

Open http://localhost:5000 and start from the app.

## Who it's for

- Developers who want AI help grounded in their codebase
- Teams reviewing pull requests and project quality
- Engineers working with GitHub repositories and project-wide context
- Builders exploring Stellar / Soroban tooling with AI assistance

## Why it stands out

- Project-aware AI instead of generic chat-only tools
- GitHub repo and PR intelligence built into the workflow
- Structured AI findings with clear labels and review history
- Workspaces and collaboration features for team use
- Plugin architecture for extensibility and future integrations

## Project status

A mature, feature-rich developer platform with active GitHub integration, AI project analysis, review tooling, collaboration, and Phase 8 plugin / Stellar tooling under active development.

## Documentation

- [Chat feature guide](CONTRIBUTING.md#chat-feature-developer-guide)
- [GitHub integration](docs/reviews.md)
- [Stellar / Soroban tooling](docs/stellar.md)
- [Plugin system](docs/plugins.md)
- [Architecture](docs/architecture.md)
- [Security model](docs/security.md)
- [Roadmap](docs/roadmap.md)

## License

[MIT](./LICENSE)

---

