# docker重新构架命令
cd /mnt/f/Trae_projects/Form_research

# 停止
docker compose down

# 重新构建（不再包含 PyTorch，不会段错误）
docker compose build --no-cache

# 启动
docker compose up -d

# 查看日志
docker compose logs app