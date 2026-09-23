"""开发模式启动脚本。

使用方式：
    cd backend
    python run_dev.py

端口默认 8010，可用环境变量 SERVER_PORT 覆盖：
    $env:SERVER_PORT=8011 ; python run_dev.py

监听 0.0.0.0：允许局域网内其他设备通过本机 IP 访问，可用 SRV_HOST 覆盖：
    $env:SRV_HOST=127.0.0.1 ; python run_dev.py
"""

import os

import uvicorn

from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("SRV_HOST", "0.0.0.0"),
        port=settings.server_port,
        reload=True,
        reload_dirs=["app"],
    )
