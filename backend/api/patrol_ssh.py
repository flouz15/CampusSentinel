"""
安保巡检：通过 SSH 在机器人上执行启动脚本（配置见项目 config/patrol_ssh.json，勿提交私钥）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_IO_CHUNK = 8000


def _truncate(s: str, limit: int = _MAX_IO_CHUNK) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… 已截断，共 {len(s)} 字符"


def load_patrol_ssh_config(config_path: Path) -> dict[str, Any] | None:
    if not config_path.is_file():
        return None
    try:
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("patrol_ssh: 读取配置失败 %s: %s", config_path, exc)
        return None


def _resolve_private_key_path(cfg_dir: Path, raw: str) -> str:
    p = (raw or "").strip()
    if not p:
        raise ValueError("private_key_path 为空")
    expanded = os.path.expanduser(p)
    if os.path.isabs(expanded):
        return expanded
    return str((cfg_dir / expanded).resolve())


def _load_pkey(key_path: str, password: str | None):
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("未安装 paramiko，请在后端执行 pip install paramiko") from exc

    last_err: Exception | None = None
    key_classes = []
    if hasattr(paramiko, "Ed25519Key"):
        key_classes.append(paramiko.Ed25519Key)
    key_classes.extend([paramiko.RSAKey, paramiko.ECDSAKey])
    for cls in key_classes:
        try:
            return cls.from_private_key_file(key_path, password=password or None)
        except Exception as e:
            last_err = e
            continue
    raise ValueError("无法加载私钥（格式不支持或路径/口令错误）") from last_err


def run_remote_start_script(config_path: Path) -> dict[str, Any]:
    """
    按 patrol_ssh.json 连接机器人并执行 remote_command。
    返回 dict：success, exitCode, stdout, stderr, error(可选)
    """
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        return {
            "success": False,
            "error": "CONFIG_MISSING",
            "message": (
                f"未找到 SSH 配置：{config_path}。"
                "请将 config/patrol_ssh.example.json 复制为 config/patrol_ssh.json 并填写。"
            ),
        }
    cfg = load_patrol_ssh_config(config_path)
    if cfg is None:
        return {
            "success": False,
            "error": "CONFIG_INVALID",
            "message": f"SSH 配置文件存在但无法解析（JSON 语法或编码）：{config_path}",
        }
    if cfg.get("enabled") is False:
        return {
            "success": False,
            "error": "DISABLED",
            "message": "已在配置中关闭 patrol SSH（enabled: false）",
        }

    host = (cfg.get("host") or "").strip()
    username = (cfg.get("username") or "").strip()
    remote_command = (cfg.get("remote_command") or "").strip()
    raw_key = (cfg.get("private_key_path") or "").strip()

    if not host or not username or not remote_command or not raw_key:
        return {
            "success": False,
            "error": "CONFIG_INCOMPLETE",
            "message": "配置缺少 host、username、remote_command 或 private_key_path",
        }

    port = int(cfg.get("port") or 22)
    connect_timeout = float(cfg.get("connect_timeout_sec") or 15)
    command_timeout = float(cfg.get("command_timeout_sec") or 120)

    passphrase_env = (cfg.get("private_key_passphrase_env") or "").strip()
    password = None
    if passphrase_env:
        password = os.environ.get(passphrase_env, "") or None
    elif cfg.get("private_key_passphrase"):
        password = str(cfg.get("private_key_passphrase") or "") or None

    cfg_dir = config_path.resolve().parent
    try:
        key_path = _resolve_private_key_path(cfg_dir, raw_key)
    except ValueError as e:
        return {"success": False, "error": "BAD_KEY_PATH", "message": str(e)}

    if not os.path.isfile(key_path):
        return {
            "success": False,
            "error": "KEY_NOT_FOUND",
            "message": f"私钥文件不存在：{key_path}",
        }

    try:
        pkey = _load_pkey(key_path, password)
    except Exception as e:
        logger.exception("patrol_ssh: 加载私钥失败")
        return {"success": False, "error": "KEY_LOAD_FAILED", "message": str(e)}

    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            pkey=pkey,
            timeout=connect_timeout,
            banner_timeout=connect_timeout,
            auth_timeout=connect_timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        stdin, stdout, stderr = client.exec_command(remote_command, timeout=command_timeout)
        stdin.close()
        out_b = stdout.read()
        err_b = stderr.read()
        exit_code = stdout.channel.recv_exit_status()
        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        logger.info(
            "patrol_ssh: exec done host=%s exit=%s out_len=%s err_len=%s",
            host,
            exit_code,
            len(out),
            len(err),
        )
        return {
            "success": exit_code == 0,
            "exitCode": exit_code,
            "stdout": _truncate(out),
            "stderr": _truncate(err),
            "message": "命令已在机器人上执行完毕" if exit_code == 0 else f"远程命令退出码 {exit_code}",
        }
    except Exception as e:
        logger.exception("patrol_ssh: SSH 执行失败 host=%s", host)
        return {
            "success": False,
            "error": "SSH_FAILED",
            "message": str(e),
        }
    finally:
        try:
            client.close()
        except Exception:
            pass
