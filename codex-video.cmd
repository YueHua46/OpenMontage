@echo off
rem Store-installed pwsh cannot start inside the Codex Windows sandbox, so hide that folder for this session only.
set "PATH=%PATH:\Microsoft\WindowsApps=\Microsoft\WindowsApps_hidden%"
cd /d "%~dp0"
codex --sandbox workspace-write --ask-for-approval on-request -c sandbox_workspace_write.network_access=true %*
