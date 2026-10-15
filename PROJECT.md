# 校园安全态势大屏

本项目用于本机演示。前端大屏由根目录静态服务提供，业务接口由 Django 提供。

## 目录

- `frontend/`：大屏页面、样式和脚本
- `config/`：前端运行配置，包括接口地址、地图、ROS 和巡检参数
- `backend/`：Django API、数据模型、迁移和启动脚本
- `backend/api/views/`：按功能拆分的接口实现
- `start-portal-8080.bat`：同时启动后端和前端页面

## 启动

先确认 MySQL 已启动，并在 `backend/.env` 中填写数据库连接。然后在项目根目录双击：

```bat
start-portal-8080.bat
```

也可以手动启动：

```bat
cd backend
..\.venv\Scripts\python.exe manage.py runserver 0.0.0.0:5000
```

```bat
.venv\Scripts\python.exe -m http.server 8080
```

浏览器打开 `http://127.0.0.1:8080/frontend/index.html`。

## 配置

- `backend/.env`：数据库、邮件、检测服务、高德 Web 服务 Key、大屏接口密钥和机器人上报密钥
- `config/dashboard.json`：前端接口地址、地图 Key、ROS 话题和巡检参数
- `config/patrol_ssh.json`：巡检任务的 SSH 启动参数

`DASHBOARD_API_KEY` 与 `dashboardApiKey` 留空时，后端只接受来自本机的大屏请求。需要从其他设备访问时，两个值必须设置成同一个非空字符串。

## 后端结构

`backend/api/views/` 按职责拆分：

- `geo.py`：高德地图代理
- `reports.py`：上报、审核和奖惩
- `auth.py`：登录、注册和用户资料
- `dashboard.py`：大屏指标、告警和工单
- `detections.py`：图片、视频和检测记录
- `assistant.py`：校园助手
- `realtime.py`：实时事件
- `robot.py`：机器人状态和巡检
- `dispatch.py`：调度、场景和模型反馈
- `common.py`：共用的数据转换和流程辅助函数

路由仍集中在 `backend/api/urls.py`。
