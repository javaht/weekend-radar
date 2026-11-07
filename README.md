# 周末活动雷达 (Weekend Radar)

按周末维度扫描指定城市的线下活动（展会/漫展/市集、技术沙龙、大中型博览会），
写入 Markdown 日报，用 SQLite 记住历史以计算「新增 / 取消 / 改地址 / 降价」，
有变化时通过轻量 Webhook 推送至 **飞书 / 企业微信 / 钉钉 / Bark / Telegram / Server酱 / PushDeer**。

## 一条命令

```bash
python3 run.py --run
```

其余模式与快捷参数：

```bash
python3 run.py                                  # 默认 --plan：抓取+提取+打印，不写库不推送（安全）
python3 run.py --city 上海 --probe               # 切换城市并执行逐源健康表 + LLM 协议探测
python3 run.py --city 深圳 --provider deepseek --channel feishu --run
python3 run.py --self-test                      # 离线自测，不联网、不需要配置
python3 run.py --run --dry-run                  # 全流程但不推送（首次上线用）
python3 run.py --serve                          # Web 控制台（默认仅绑 127.0.0.1:8000）
```

退出码：`0` 成功｜`1` 自测或探测失败｜`2` 参数/配置问题｜`3` 推送失败。

## Web 控制台的安全边界

`--serve` **默认只绑 `127.0.0.1`**，浏览器打开 `http://127.0.0.1:8000` 即可。
控制台能改配置、跑任务、发推送，没有登录态，所以：

- 确需对外暴露时：`python3 run.py --serve --host 0.0.0.0` 并**同时**设置
  `RADAR_WRITE_TOKEN`，所有写接口需带 `X-Radar-token: <该值>` 头；
  未配置 token 且对外暴露时写接口一律 403。CORS 只放行本机来源，
  可用 `RADAR_CORS_ORIGINS` 追加白名单。
- `GET /api/config` 对密钥类字段返回 `***`，不回传明文。
- 网页保存配置时，真实 Key / 内网网关 / 各渠道密钥写入**被 git 忽略的
  `config.local.ini`**，绝不写回 `config.ini`；且采用逐行替换，保留配置文件的注释、
  `[sources]` 的对齐空格与城市模板占位符（`GET /config` 返回未渲染模板，原样保存零 diff）。
  `llm.base_url` 是接口地址而非密钥，保持明文可编辑；真正掩码的是各类 key / secret / webhook。
- `[llm]` 区块的「获取模型」按钮通过后端 `POST /api/llm/models` 拉取网关
  `/v1/models`（兼容 `/models`）列表，之后「模型名称」变为下拉选择，也可切回手动输入。
  页面编辑中未保存的 base_url / key 会作为临时值参与请求；`***` 掩码自动回落磁盘真实配置。
- 设置页所有配置项（radar / llm / push / sources 开关 / 品类偏好）均为**修改即自动保存**，
  debounce 后落盘，右下角闪一个约 0.6 秒的「已保存」；提示用 fixed 定位，滚到长页面底部也可见。
  Webhook 调试台的三个输入框只用于单次测试，不写配置。密钥「显示」走独立的写保护接口
  `GET /api/config/secret` 取真实值，查看动作本身不触发保存。

## Docker 运行

```bash
# 构建镜像
docker build -t weekend-radar .

# 离线自测
docker run --rm weekend-radar --self-test

# 探测上海数据源与 DeepSeek 模型连通性
docker run --rm \
  -e RADAR_CITY=上海 \
  -e RADAR_LLM_PROVIDER=deepseek \
  -e DEEPSEEK_API_KEY=sk-xxx \
  weekend-radar --probe

# 正式运行（挂载 data 目录持久化 SQLite 与日报，推送飞书）
docker run --rm \
  -v $(pwd)/data:/app/data \
  -e RADAR_CITY=上海 \
  -e RADAR_LLM_PROVIDER=deepseek \
  -e DEEPSEEK_API_KEY=sk-xxx \
  -e RADAR_PUSH_CHANNEL=feishu \
  -e RADAR_FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx \
  weekend-radar --run
```

## 部署（实测：Ubuntu 24.04 / Python 3.12 / 已部署在 /root/weekend-radar）

```bash
ssh root@<server>
mkdir -p /root/weekend-radar
# 上传后：
cd /root/weekend-radar
python3 run.py --self-test
python3 run.py --probe          # ★ 这一步的红绿表决定哪些源该开
python3 run.py --plan
python3 run.py --run --dry-run
```

**无需建 venv**：这台机器的 `python3` 是 `/opt/mamba/bin/python3`（conda 环境），
`requests` 已随环境自带（2.32.3），PEP 668 只约束 `/usr/lib/python3.12`，不影响它。
如果换到纯系统 Python 的机器，再按 PEP 668 建 venv。

定时：

```cron
0 9 * * * cd /root/weekend-radar && python3 run.py --run >> data/cron.log 2>&1
```

也可以直接 `python3 run.py --install-cron` 让程序自己写 crontab（见下）。

## LLM：走本机 Magic 网关

服务器上 Magic 常驻监听 `127.0.0.1:3425`，凭证在
`/root/.config/magpie/caller-keys.json`（`lan: true` 那条的 `secret`）。
脚本运行时自己读，不把 key 写进代码或配置。

实测两种协议都通，模型解析为 `deepseek-v4.1-flash`：

```
openai      ✅ model=deepseek-v4.1-flash
anthropic     ✅ model=deepseek-v4.1-flash
```

## 三件事必须知道

**1. 为什么源要分 H / J 两类。**
JSON 接口（B 站会员购）由代码直接映射字段，票价是原始字符串。
HTML 页面交给 LLM 提取，因为给 18 个站点各写一套选择器，等于维护 18 份
随时会坏的代码。分工的原则是：**票价和日期这类精确字段绝不让模型转述**。

**2. 「无变化」和「采集失败」是两件事。**
全部源成功且无变化 → 不推送。
任何一个源失败 → **必推健康告警**，哪怕当天没有活动。
如果不这样做，某天某个站挂了你收到的是"今天没活动"，而真相是"我没抓到"。

**3. 源失败时不会标记活动取消。**
只有全部源成功、**且该活动来自确定性源**（`[radar] stable_sources`，默认只有 `bili`）
时，缺席才会被记成 `gone`。原因见下一条。

**4. 变更要连续两轮成立才算数（`confirm_runs = 2`）。**
这是实测撞出来的最重要的一个设计。LLM 每轮转述同一场活动，票价写法会变
（`¥80(预售票)¥90(现场票)` vs `¥80-158`），抽取覆盖条数也会跳（同一批数据在
26–39 条之间波动）。如果按"字符串变了就算变更"，同一天跑两次会报出
「14 条改价、2 条取消」——全是假的，而且会天天触发推送，让「有变化才推」形同虚设。

所以现在：
- 指纹里的票价先归一到**最低价**，场馆先归一再去重标点，只比实质
- 一次变更连续 2 轮都成立才写进报告、才允许触发推送
- LLM 抽取源不参与"取消"判定（缺席通常只是这轮没抽到）
- 推送按性质分级：取消 / 改地址 → 立刻推；新增需 ≥ `min_new_changes`（默认 3）

代价：真实变更会晚一天体现。换来的是推送不抽风。

## 配置

全部在 `config.ini`，无需命令行参数。

> **敏感值请放 `config.local.ini`**（已被 `.gitignore` 忽略）。
> 加载顺序是「内置默认值 → `config.ini` → `config.local.ini`」，后者最后覆盖，
> 用来放真实 API Key、内网网关地址这类不能进公开仓库的值。
> 仓库里的 `config.ini` 只留占位与注释。

- `[sources]` 一行一个平台：`平台名 = 类|开关|入口URL`
  - `H` HTML（LLM 提取）、`J` JSON 接口、`S` 仅链接发现
  - 加平台 = 加一行 + 跑 `--probe` 校准，**不用改代码**
- `[llm]` 走本机 Magic 网关（默认 `http://127.0.0.1:3425`）。
  key 留空则自动读 `~/.config/magpie/caller-keys.json` 里 `lan: true` 那条的 secret。
  `mode = auto` 会先试 OpenAI 协议、再试 Anthropic 协议。
- `[fetch] allow_http` —— **重要**：`090expo` 和 `manzhan` 只有 80 端口，
  https 直接拒绝连接。把它们写进白名单强制走 http，否则永远抓不到，
  而且报错看起来像"站点挂了"（这类假阴性最难查）。
- `[push] enabled` 是双开关之一，不是 `true` 就绝不推送。

## 实测结论（2026-10-28，服务器 /root/weekend-radar）

| 源 | 结果 | 说明 |
|---|---|---|
| 本地宝 / manzhan / 豆瓣同城 / 展大人 | ✅ | 正常抓到正文 |
| 090expo | ⚠️ | 能抓到（抽到 15 条，含多档票价），但同一 IP 短时间反复请求会被封，返回 607 字节空壳。默认 off，封禁解除后改 on |
| B 站会员购 | ✅ | JSON 接口，17 条（价格单位是「分」，已除 100） |
| SegmentFault / Indienova / CSDN | ✅ | 正常 |
| 活动行 | ❌ | **从服务器 IP 被持续限流**（页面标题「哎呀，访问太快了」），加 Referer、加延迟均无效。本机家宽可通，故默认 off |
| 大麦 | ❌ | 接口正确但被 x5sec 反爬拦截，需 cookie 或换出口 IP |
| 秀动 | ❌ | 列表页命中验证页 |
| 联谱 / 活动网 / 机核 | ❌ | 404（URL 可能已变，需重新确认） |
| 今日会展 / 第一展会网 / 黑盒 / OSC / TapTap | ❌ | 返回空页或纯 JS |
| CNENA | ❌ | 403 |
| 小红书 / 微博超话 | ❌ | 登录墙，未实现 |

**换网络之后这张表会变**，以那台机器上 `--probe` 的结果为准。

## 已知边界

- 不做浏览器自动化。服务器上装 Chromium 是新的故障面，而大麦/B 站都有 JSON 接口。
- 不做语义核验。跨源场馆冲突只做机械比对并标注「待核实」，真假仍需人判断。
- 不做历史回填。第一次运行没有基线，`新增与变化` 从第二次起才有意义。
- 13 城预设联动（`[radar] city` 或 `--city`）；Web 头部切换城市仅改变展示口径，
  **库内数据由配置城市采集**，换城市需以该城市执行一次任务才会更新清单。
- 窗口 30 天；C 类博览会不受周末限制（会标注有无周末场）。
