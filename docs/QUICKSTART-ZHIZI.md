# Brain MCP — Adding to a New Repo on zhizi

zhizi already has the brain CLI, venv, wrappers, and SSH tunnel set up.
To give a new Claude Code project access to the brain, just symlink the
MCP config:

```bash
cd ~/w/my-project
ln -sf ~/w/zeresh-brain/.mcp.json .mcp.json
```

Then restart your Claude Code session (exit and re-enter, or `/mcp` to
check server status).

## Verify

Ask Claude Code to use the brain:

```
> Use brain_search to find entries about "openclaw"
> Use brain_recent with project "zhizi"
```

You should see it call the MCP tools and return results.

## That's it

The `.mcp.json` just calls `brain-mcp` (found via `~/.local/bin` in PATH),
which activates the venv and runs `brain-mcp.py`, which calls `brain-cli.py`
as a subprocess. The SSH tunnel forwarding port 5433 to bakkies is handled
by the `openclaw-tunnel-zeresh.service` systemd service — it's always running.

## Troubleshooting

**MCP tools not appearing:**
- Check the symlink: `ls -la .mcp.json` — should point to `~/w/zeresh-brain/.mcp.json`
- Restart the Claude Code session
- Check `brain-mcp` is in PATH: `which brain-mcp`

**"cannot connect to database":**
- Check the tunnel: `sudo systemctl status openclaw-tunnel-zeresh.service`
- Test DB directly: `PGPASSWORD=brain_local_only psql -h 127.0.0.1 -p 5433 -U brain -d zeresh_brain -c "SELECT 1;"`
