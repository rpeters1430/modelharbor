# Remove offline devices

Approved behavior: offline agent cards offer Remove device with confirmation. Removal clears the agent, all its runtime inventory, and device-specific jobs and matches from SQLite. No computer files are touched. An agent can register again by reconnecting. Online agents cannot be removed; check again in the deletion transaction using the existing 3900-second heartbeat/worker threshold.

Use an authenticated DELETE endpoint and exact device/runtime names, rather than wildcard prefixes. Keep shared hash-based source metadata for other devices.
