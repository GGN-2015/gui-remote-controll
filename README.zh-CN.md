# GUI Remote Controll

[English](README.md)

GUI Remote Controll 可以把 Windows、Linux 或 macOS 桌面共享到浏览器，并把鼠标、触摸、
滚轮、键盘、输入法文本和纯文本剪贴板事件重定向到服务端电脑。

## 快速开始

需要 Python 3.10 或更高版本。

1. 安装：

   ```console
   pip install gui-remote-controll
   ```

2. 启动带 PIN 保护的服务器：

   ```console
   gui-remote-controll --pin 123456 --title "办公室工作站"
   ```

   程序默认只请求一次管理员/root 权限。只有在已经单独配置桌面权限，或当前环境不适合提权
   时，才使用 `--no-elevate`。
   省略 `--title` 时，客户端标题为 `GUI Remote Controll`。

3. 打开控制页面：

   - 服务端电脑：`http://127.0.0.1:8000`
   - 局域网设备：`http://服务端局域网IP:8000`

默认监听地址是 `0.0.0.0`。只要其他设备可以访问，就必须设置 `--pin`。离开可信局域网时，
应使用 TLS 或加密隧道，不要把默认 HTTP 服务直接暴露到公网。

### 首次运行权限

- **Windows：**接受 UAC 提示，并从已登录用户的交互式会话启动。
- **Linux：**使用 X11 桌面会话；原生 Wayland 不允许通用的全局画面抓取和输入注入。
- **macOS：**为终端或 Python 可执行文件授予“屏幕录制”和“辅助功能”权限，然后重启服务。
  root 权限不能代替这些隐私授权。

详细设置与排错见[平台支持手册](docs/platform-support.md)。

## 完整手册

- [手册索引](docs/README.md)
- [完整用户手册](docs/user-guide.md)
- [CLI 参数手册](docs/cli-reference.md)
- [架构、算法与协议手册](docs/architecture-and-algorithms.md)
- [平台支持手册](docs/platform-support.md)
- [开发与发布手册](docs/development.md)
- [版本变更记录](CHANGELOG.md)
- [安全策略](SECURITY.md)

Python 导入包名为 `gui_remote_controll`；PyPI 分发包和命令名沿用仓库拼写
`gui-remote-controll`。

## 许可证

[MIT](LICENSE)
