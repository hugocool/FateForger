# 🎨 TRMNL Dashboard - Quick Launch Guide

## How to Start the Dev Server (Easiest Way)

### ⭐ Method 1: Run and Debug Sidebar (RECOMMENDED)
1. Click the **Run and Debug** icon in VS Code sidebar (play button with bug) OR press `Cmd+Shift+D`
2. At the top of the sidebar, select **🎨 TRMNL Dashboard (Dev Server)** from the dropdown
3. Click the green **▶ Play** button (or press `F5`)
4. **Browser opens automatically** to http://localhost:4567 when server is ready! 🎉

### Method 2: Command Palette
1. Press `Cmd+Shift+P` (macOS) or `Ctrl+Shift+P` (Windows/Linux)
2. Type: `Tasks: Run Task`
3. Select: **FateForger: TRMNL Dev Server**

### Method 3: Terminal Menu
1. Click `Terminal` in the top menu
2. Click `Run Task...`
3. Select: **FateForger: TRMNL Dev Server**

---

## What Happens When You Run It

✅ Docker container starts with TRMNL preview server  
✅ Port 4567 is exposed (http://localhost:4567)  
✅ Hot reload enabled (file watcher active)  
✅ Terminal opens showing live logs  

---

## What to Do Next

1. **Open Browser**: http://localhost:4567
2. **Edit Files**:
   - [src/trmnl_frontend/src/full.liquid](src/trmnl_frontend/src/full.liquid) → Template/layout
   - [src/trmnl_frontend/src/data.json](src/trmnl_frontend/src/data.json) → Mock data
3. **Save** → Browser refreshes automatically!
4. **Toggle "E-ink" mode** in browser to see 1-bit rendering

---

## Stopping the Server

- Press `Ctrl+C` in the terminal
- Or run: `cd src/trmnl_frontend && docker compose down`

---

## Troubleshooting

### "Task not found"
- Make sure you're in the workspace root
- Reload VS Code: `Cmd+Shift+P` → `Reload Window`

### "Port already in use"
- Stop existing TRMNL container: `docker compose down` in `src/trmnl_frontend/`
- Check port 4567: `lsof -i :4567`

### "Hot reload not working"
- Verify `.trmnlp.yml` has `watch: [src]`
- Check container logs: `docker compose logs -f`

---

## File Structure

```
src/trmnl_frontend/
├── .trmnlp.yml          # TRMNL config (watch settings)
├── docker-compose.yml   # Container definition
├── schema.json          # Data contract
├── src/
│   ├── full.liquid      # Main template (EDIT THIS)
│   ├── data.json        # Mock data (EDIT THIS)
│   └── settings.yml     # Plugin metadata
└── README.md            # Full documentation
```

---

## Hot Reload Details

The TRMNL server watches:
- `src/full.liquid` → Template changes
- `src/data.json` → Data changes
- `.trmnlp.yml` → Config changes

When you **save** any of these files:
1. TRMNL detects the change (< 1 second)
2. Re-renders the template
3. Browser auto-refreshes (via WebSocket)

**No manual refresh needed!** 🎉
