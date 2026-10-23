# 域名部署

适用于已安装 Docker Engine 和 Docker Compose 插件的 Linux 服务器。默认给应用限制 2 核、2GB，为 4核4GB 服务器的系统与反向代理保留余量。

## 准备

1. 域名的 A 记录解析到服务器公网 IPv4；不要添加未配置的 IPv6 AAAA 记录。
2. 中国内地服务器按云厂商要求完成域名备案。
3. 云防火墙和系统防火墙允许入站 TCP 80、443；SSH 仅对管理来源开放。不要对公网开放 8766。UDP 443 可选，用于 HTTP/3。
4. 确认 80、443 未被其他项目占用；若已有反向代理，请接入现有代理，不要直接启动第二个。

## 启动

在服务器执行：

```bash
git clone https://github.com/xiaodandandandan/Stock_workDashboard.git
cd Stock_workDashboard
cp .env.example .env
chmod 600 .env
docker run --rm -it caddy:2 caddy hash-password
```

按提示输入自己设置的访问密码。编辑 `.env`，填写域名（只填主机名，不含 https:// 或路径）、邮箱、登录用户名，并将输出的密码哈希完整放入单引号内。不要将明文密码、私钥或 `.env` 提交到 Git。

```bash
docker compose config --quiet
docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 app caddy
```

Caddy 自动申请和续期 HTTPS 证书，并将 HTTP 跳转至 HTTPS。手机打开 `https://你的域名`，输入 `.env` 所配置用户名和自己设置的密码。首次进入后点击重新扫描。

## 验证

```bash
# 未登录应返回 401，不能直接触发扫描或获取数据。
curl -I https://你的域名/api/health
# -u 只写用户名会交互询问密码，避免明文密码进入命令历史。
curl -u owner https://你的域名/api/health
```

## 更新、停止与备份

```bash
git pull --ff-only
docker compose up -d --build
docker compose stop
```

扫描、K线缓存和历史档案保存在 `scan_cache` Docker 卷，证书保存在 `caddy_data`。重建容器不会删除这些卷；不要执行 `docker compose down -v`。定期备份扫描卷，例如：

```bash
mkdir -p backups
docker compose cp app:/app/cache ./backups/cache
```

首次部署不会自动上传电脑内历史扫描；若需要迁移，先停止扫描，再备份并导入本机项目 `cache/`。部署前后需核对行情源在云服务器上的连通性，不保证本机可访问的接口在云端也可访问。
