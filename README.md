<!-- dato-prod-tree: do not edit on the appliance -->

# dato

On-premise multi-agent appliance powered by OpenClaw. Ships pre-installed
on dedicated hardware. All data stays local.

## Documentation

Appliance setup and recovery procedures live in `docs/runbooks/`.

## Updating

The system self-updates over the air. The OTA watcher checks for new
releases periodically and applies them automatically. Do not `git pull`
or otherwise modify this repo directly on the appliance — manual changes
will be overwritten by the next OTA cycle.

## Source version

The dev commit this appliance was built from is recorded in the
`VERSION` file (`cat VERSION`) and in the squashed commit's
`Source-Commit:` trailer (`git log -1`).

## Support

Contact your dato vendor for support.
