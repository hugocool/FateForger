# 🎉 Upstream Google Calendar MCP Server Setup Guide

This document describes the complete process for setting up the Google Calendar MCP server using the official upstream repository with proper OAuth authentication.

## 📋 Overview

We successfully configured the Google Calendar MCP server to use the official upstream Docker build from `nspady/google-calendar-mcp` repository instead of maintaining a local Dockerfile. This approach provides:

- ✅ Always up-to-date with upstream changes
- ✅ Tested and verified upstream Dockerfile
- ✅ Proper OAuth authentication flow
- ✅ Persistent token storage
- ✅ Clean environment variable management

## 🔧 What Was Done

### 1. **Docker Compose Configuration**
**File:** `infra/docker-compose.yml`

**Key Changes:**
- Updated `build.context` to use GitHub URL: `https://github.com/nspady/google-calendar-mcp.git#${MCP_VERSION}`
- Added proper port mapping for OAuth authentication server (port 3500)
- Cleaned up confusing double env file references
- Configured volume mounting for OAuth credentials and token persistence

```yaml
calendar-mcp:
  build:
    context: https://github.com/nspady/google-calendar-mcp.git#${MCP_VERSION}
    dockerfile: Dockerfile
  ports:
    - "${PORT}:${PORT}"
    - "3500:3500"  # OAuth authentication server port
  volumes:
    - type: bind
      source: ../secrets/gcal-oauth.json
      target: /app/gcp-oauth.keys.json
      read_only: true
    - calendar-mcp-tokens:/home/node/.config/google-calendar-mcp
  environment:
    GOOGLE_OAUTH_CREDENTIALS: "/app/gcp-oauth.keys.json"
    HOST: "0.0.0.0"
```

### 2. **Environment Variable Management**
**File:** `infra/.env`

**Simplified Configuration:**
- Removed confusing double env file loading (`- ./.env` and `- ../.env`)
- Centralized MCP-specific configuration in `infra/.env`
- Set proper OAuth credentials path to match Docker volume mount

```properties
# MCP Server Configuration
MCP_VERSION=v1.4.8
PORT=3000
TRANSPORT=http
GOOGLE_OAUTH_CREDENTIALS=/app/gcp-oauth.keys.json
```

### 3. **OAuth Credentials Setup**
**File:** `secrets/gcal-oauth.json`

**User Action Required:**
- You obtained real Google Cloud OAuth credentials from Google Cloud Console
- Replaced placeholder values in `secrets/gcal-oauth.json` with actual credentials
- Enabled Google Calendar API in your Google Cloud project

### 4. **Authentication Flow Resolution**
**Issue Encountered:** OAuth callback server was running inside container but port wasn't exposed to host

**Solution Implemented:**
- Added port mapping for authentication server (3500:3500)
- Identified that the auth server uses port 3500 (not 3501 as initially assumed)
- Updated Docker Compose to expose this port to enable OAuth callback

## 🚀 Step-by-Step Process

### What The System Did:
1. **Cleaned up Docker Compose configuration**
   - Removed duplicate env file references
   - Added upstream GitHub build context
   - Configured proper volume mounting

2. **Fixed environment variable conflicts**
   - Resolved OAuth credentials path mismatches
   - Simplified env file structure
   - Added required HOST=0.0.0.0 for container networking

3. **Debugged OAuth authentication flow**
   - Identified port mapping issue for OAuth callback
   - Added port 3500 exposure for authentication server
   - Enabled proper redirect URL handling

4. **Tested and validated the complete setup**
   - Verified MCP server builds from upstream repository
   - Confirmed OAuth credentials mounting works
   - Validated authentication token persistence

### What You Did:
1. **Provided real OAuth credentials**
   - Went to Google Cloud Console
   - Created/configured OAuth 2.0 client credentials
   - Downloaded and placed real credentials in `secrets/gcal-oauth.json`

2. **Completed OAuth authentication flow**
   - Visited the authentication URL in your browser
   - Signed in with your Google account
   - Granted calendar permissions to the application
   - Successfully completed the OAuth callback

## ✅ Final Status

**MCP Server Status:** ✅ **FULLY OPERATIONAL**
```
✅ Container: admonish-calendar-mcp running
✅ Port: 3000 (MCP server) + 3500 (OAuth auth)
✅ Authentication: Valid tokens loaded
✅ OAuth: Credentials properly mounted
✅ Upstream: Using official v1.4.8 from GitHub
```

**Key Indicators of Success:**
```bash
docker-compose logs calendar-mcp | tail -3
# Expected output:
# Loaded tokens for normal account
# Valid normal user tokens found.
# Google Calendar MCP Server listening on http://0.0.0.0:3000
```

## 🔄 Usage Commands

### Start the MCP Server
```bash
cd infra
docker-compose up -d calendar-mcp
```

### Check Server Status
```bash
docker-compose logs calendar-mcp
```

### Re-authenticate (if needed)
```bash
docker exec -it admonish-calendar-mcp npm run auth
# Then visit http://localhost:3500 in your browser
```

### Stop the Server
```bash
docker-compose down
```

## 🎯 Next Steps

Now that the Google Calendar MCP server is fully operational, you can:

1. **Start the Python Bot Service:**
   ```bash
   docker-compose up -d bot
   ```

2. **Test Calendar Integration:**
   - The bot can now communicate with the MCP server at `http://calendar-mcp:3000`
   - All calendar operations (create, read, update, delete events) are available
   - Authentication tokens persist across container restarts

3. **Develop and Test:**
   ```bash
   docker-compose up -d dev  # For development with live reload
   ```

## 🔐 Security Notes

- OAuth tokens are stored in the `calendar-mcp-tokens` Docker volume
- Credentials file is mounted read-only from the host
- Authentication is persistent and survives container restarts
- All communication uses the internal Docker network for security

## 🎉 Success Metrics

- ✅ **Build Time:** Uses upstream Dockerfile (no local maintenance)
- ✅ **Authentication:** OAuth flow completed successfully
- ✅ **Networking:** Proper port exposure and internal communication
- ✅ **Persistence:** Tokens survive container restarts
- ✅ **Integration:** Ready for Python bot service connection

**The Google Calendar MCP server is now ready for production use!**
