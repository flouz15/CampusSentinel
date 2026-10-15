# 校园智能巡检 · 态势大屏

## 结构

| 路径 | 说明 |
|------|------|
| `frontend/` | 大屏前端（`index.html`、样式、脚本、`assets/`） |
| `config/dashboard.json` | **统一配置**：后端 API、高德 Key、rosbridge 地址与 ROS 话题名 |
| `backend/` | Django API（`manage.py runserver`） |
| `start-portal-8080.bat` | Windows 一键启动 |

## 启动（Windows）

1. 配置 `backend/.env`（数据库等），在 `backend/` 下安装依赖并迁移。  
2. 编辑 `config/dashboard.json`：  
   - `apiBase`：与 Django 一致（默认 `http://127.0.0.1:5000/api`）  
   - `rosbridge.url`：机器人侧 rosbridge_suite WebSocket（如 `ws://机器人IP:9090`）  
   - `rosTopics`：与 Noetic 中实际话题一致（相机、电量、`/odom`、`/cmd_vel` 等）  
3. 双击 **`start-portal-8080.bat`**：启动 Django `:5000`、根目录静态服务 `:8080`，并打开 **`http://127.0.0.1:8080/frontend/index.html`**。

## 功能说明

- **首页**：高德地图、告警轮询、工单/指标接口、地图检索（地理接口走 Django）。  
- **安保巡检**：经 rosbridge 订阅图像与状态，显示机器人视角；球形电量、速度折线图、`cmd_vel` 手动控制。仅在进入该页时连接 rosbridge（可在配置里改为始终连接）。

后端生成的大屏链接已指向 `/frontend/index.html`。
