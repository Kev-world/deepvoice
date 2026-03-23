# DeepVoice - Voice AI Meeting Agent

## Project Overview
Voice AI meeting agent that joins WebRTC meetings via LiveKit/Vocal Bridge and answers questions about repositories, documentation, and work history using a RAG knowledge base.

## Architecture
```
Vocal Bridge Agent (voice) <-> LiveKit WebRTC Room <-> Participants
        |
        | (API tools - HTTP calls during conversation)
        v
   FastAPI Backend (src/main.py)
        |
        +-- RAG Query APIs (/api/rag/query, /code, /git, /docs)
        +-- Index APIs (/api/index/repository, /git-history)
        +-- Voice Token API (/api/voice-token)
        |
        v
   Knowledge Store (ChromaDB + sentence-transformers)
        |
        +-- code collection (indexed source files)
        +-- docs collection (indexed documentation)
        +-- git_history collection (indexed commits)
```

## Tech Stack
- Python 3.11+, FastAPI, uvicorn
- ChromaDB (vector store), sentence-transformers (embeddings)
- Vocal Bridge (voice AI), LiveKit (WebRTC)
- httpx (async HTTP)

## Key Commands
```bash
# Install dependencies
pip install -e .

# Start the API server
python -m src.main
# or: bash scripts/start.sh

# Index a repository
python scripts/index_repo.py /path/to/repo

# Configure Vocal Bridge agent
vb config set --api-tools-file tools/api_tools.json
vb prompt set --file tools/vb_prompt.md
```

## Project Structure
- `src/rag/` - RAG system (embeddings, store, indexer, retriever)
- `src/api/` - FastAPI routes and models
- `src/agents/` - Orchestrator and sub-agents
- `src/livekit/` - LiveKit room management
- `tools/` - Vocal Bridge config (api_tools.json, vb_prompt.md)
- `scripts/` - CLI utilities

## Code Style
- Python 3.11+, type hints required
- Ruff for linting (line-length=100)
- Logging via stdlib logging module
