# NVIDIA AI Hackathon

## Folder structure

- `apps/`: Frontend owner adds UI project files.
- `services/`: Agent and backend owner adds service files.
- `packages/`: Retrieval and contract owner adds shared schemas.
- `fixtures/`: Demo-data owner adds source files.
- `docs/`: Shared planning documents.

## Run the demo

Start the dependency-free mock API in one terminal:

```bash
cd services/api
npm run dev
```

Start the frontend in another terminal:

```bash
cd apps/web
npm install
npm run dev
```

The frontend reads `apps/web/openapi.yaml` as its contract and calls the mock API at `http://localhost:8787` by default. Set `VITE_API_URL` to point it at another API host.
