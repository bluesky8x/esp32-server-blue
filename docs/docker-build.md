# 本地编译docker镜像方法

现在本项目已经使用`github`的`自动编译docker镜像`功能，如果您拉取的是项目发行的镜像，您没有自己编译镜像的需求，那就忽略本文档。

如果您修改了源码，然后想采用`docker`的方式部署运行，可以参照以下步骤操作：

## 1、环境准备

安装docker：
```bash
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 2、编译镜像

当你修改好代码后，需要编译新的镜像时，需要按照以下步骤操作：

准备好你的`你的用户名`和`新的版本号`。
- 这个`你的用户名`是你在`docker hub`注册的用户名，例如`xiaozhi`。当然，如果你不需要推送到`docker hub`，你可以自由定义。
- 这个`新的版本号`是你编译的镜像版本，例如`1.2.3`，你可以根据需要自定义或者使用日期格式（例如`20260609`）主要是方便和现在运行的版本号做区分，同时也方便下次回忆你是什么时候构建的，不要和现在你本机运行的版本号相同。

进入`xiaozhi-esp32-server`项目根目录，编译 server 和 web 两个镜像：

```bash
cd 项目根目录

# 编译server镜像
docker build -f Dockerfile-server -t 你的用户名/xiaozhi-esp32-server:新的版本号 .

# 编译web镜像
docker build -f Dockerfile-web -t 你的用户名/xiaozhi-esp32-server-web:新的版本号 .

```

## 3、修改docker-compose配置

```bash
cd main/xiaozhi-server
```

编辑 `docker-compose_all.yml` 文件，将镜像版本替换为你刚才编译的版本：

```yaml
services:
  xiaozhi-esp32-server:
    image: 你的用户名/xiaozhi-esp32-server:新的版本号   # 修改为你的镜像地址
    ...

  xiaozhi-esp32-server-web:
    image: 你的用户名/xiaozhi-esp32-server-web:新的版本号   #修改为你的镜像地址
    ...
```

## 4、重启服务

```bash
# 停止旧容器
docker compose -f docker-compose_all.yml down

# 启动新容器
docker compose -f docker-compose_all.yml up -d
```

## 5、验证

查看日志确认服务启动正常：

```bash
# 查看server日志
docker logs -f -n 50 xiaozhi-esp32-server

# 查看web日志
docker logs -f -n 50 xiaozhi-esp32-server-web
```

## 6、只改 server 代码时：同步文件，不重新编译镜像（Blue 部署）

server 代码由 `docker/Dockerfile` 的 `COPY main/xiaozhi-server .` 打进镜像，所以传统做法每次改动都要 `--build`，会产生大量 `<none>` 悬空镜像占满 Docker 磁盘。

`run-docker.sh` 已提供不重新编译的方式：把源码 `tar` 进正在运行的容器再重启进程。

```bash
./run-docker.sh up      # 启动：镜像已存在就直接复用，不编译
# ...修改 main/xiaozhi-server/ 下的代码...
./run-docker.sh sync    # 同步代码进容器 + 重启（秒级，不新增镜像层）
```

- `sync` 只同步镜像内代码路径：`app.py`、`core/`、`config/`、`plugins_func/`、`config.yaml`、`config_from_api.yaml`、`mcp_server_settings.json`、`agent-base-prompt.txt`（自动跳过 `__pycache__`）。
- `data/`、`tmp/`、`music/`、`models/` 本来就是 volume，无需同步。
- 只有改动 `requirements.txt` 或 `Dockerfile` 时才需要 `./run-docker.sh rebuild`。
- Docker 磁盘被旧镜像占满时：`./run-docker.sh prune`（清理悬空镜像 + 编译缓存）。

