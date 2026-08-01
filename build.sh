#!/bin/bash
# 规范智能问答助手系统 - Docker 构建脚本（后台运行）
set -e
LOG=/tmp/form_build.log

echo "=== START: $(date) ===" > "$LOG"

echo "=== STEP 1: docker compose pull ===" >> "$LOG"
docker compose pull >> "$LOG" 2>&1 || echo "[WARN] pull failed, will try build directly" >> "$LOG"

echo "=== STEP 2: docker compose up -d --build ===" >> "$LOG"
cd /mnt/f/Trae_projects/Form_research
docker compose up -d --build >> "$LOG" 2>&1

echo "=== STEP 3: ps ===" >> "$LOG"
docker compose ps >> "$LOG" 2>&1

echo "=== ALL DONE: $(date) ===" >> "$LOG"
