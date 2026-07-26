## Long-command lifecycle

When a command returns a live session or cell ID, preserve that exact ID and continue polling the same session until it reports an explicit exit code. Intermediate or empty output is not completion. Do not retry, resume, or start a second writer for the same artifact while the original session is unresolved. If the live handle is lost, stop and report uncertainty instead of guessing that the command failed. This child-process polling rule does not authorize Supervisor to poll delegated workflow tasks.
