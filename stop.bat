@echo off
if not exist "__RUNTIME_ROOT__" mkdir "__RUNTIME_ROOT__"
type nul > "__RUNTIME_ROOT__\capture.stop"
echo Read-only capture will stop after its current operation. Manual login prompt: press Enter to finish.
