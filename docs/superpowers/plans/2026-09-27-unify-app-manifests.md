# Unify application onboarding behind manifests

## Goal

The portal is an entry point for account grants and service start/stop. Each application is an independent local process with its own externally routed port. All application metadata and launch commands come from `applications/<id>/app.yaml`; no application IDs or commands are hard-coded in the platform registry.

## Migration decisions

- Ticket and Douyin keep their current source and data directories. Their YAML files retain internal ports 8767 and 8766 so current instances can be recognized during migration. New apps continue to receive internal ports automatically.
- Caddy keeps the separate Douyin verification origin and its restricted paths. Regular Ticket and Douyin pages move to generated per-app external ports.
- Existing applications expose the optional JSON readiness and activity endpoints so safe stop checks remain in the applications, not in platform-specific adapters.
- A trusted manifest may name a Python interpreter outside its application directory; the executable path is resolved relative to that directory. New applications can keep their executable and environment inside their own directory.

## Work

1. Extend manifest validation and port allocation for optional `internal_port` and `data_dir`; remove hard-coded application registry entries.
2. Add Ticket and Douyin YAML files and optional readiness/activity endpoints. Preserve original data directories and commands.
3. Use generated independent-port Caddy routes for all three apps; remove old prefix routes, retain the restricted verification site.
4. Make Douyin accept its actual external origin for write requests; keep standalone behavior. Update portal auth and card links for all manifest apps.
5. Rewrite README and INTEGRATION for the architecture and precise third-party contract, then run unit and local HTTP verification. Migrate running processes only after idle checks allow safe stop.
