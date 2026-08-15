# CLI compatibility

Version 0.4 intentionally removes the workflow-state compatibility surface from 0.3. New workflows support only `DIRECT`, `VERIFY`, and `REVIEW`.

Removed commands and configuration include workflow preflight/freeze/authority/semantic/candidate-freeze commands and the escalation controller. Old runtime files remain ordinary historical JSON but are not read by TaskStore v2.

Provider adapters receive one of the six ordinary role identities and the same four Worker or nine Supervisor MCP tools. Generic providers must enforce the generated role write and Git boundaries themselves.
