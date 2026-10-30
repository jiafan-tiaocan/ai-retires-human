# AI Retires Human

**把问题明确的方案工作交给网页模型，发出后不用 Agent 盯着等。**

A planning-only agent skill with a deterministic background worker. Package a clearly defined planning problem, dispatch once, then collect and verify the result. Implementation stays with the main agent. No model calls are used to monitor the browser.

适合这样的时刻：编码 Agent 的额度快用完了，你已经有一个网页模型账号，而且有一项输入和验收都明确的方案工作。比如依据给定资料比较技术方案，或基于明确业务约束提出设计与取舍。

**只用于方案类任务。** 写代码、改仓库、调试、执行测试、部署和其他实施工作继续由主 Agent 完成。问题尚未定义清楚时，先在当前会话澄清；方案需要少量代码或伪代码来说明时，按交付目的判断，不按关键词误判。

默认使用 **ego lite 的官方 `ego-browser` 接口**，复用浏览器中的登录状态和持久任务空间。浏览器里的等待由程序完成，不让 Agent 一轮轮读页面。首个目标是 ChatGPT；网页和登录状态仍可能变化，重试有上限，失败保留原因与恢复线索。实测结果见 [验证记录](docs/verification.md)。

**网站规则：**截至 2026-10-30，[OpenAI 使用条款](https://openai.com/policies/terms-of-use/)明确限制自动或程序化提取数据及输出。该项目没有获得 OpenAI 的自动化许可，技术控制能力不等于许可。请自行确认适用条款与授权；该限制也意味着本项目不能宣传为获批准的 ChatGPT 自动接入。它不承诺免费、无限额度、独立额度池或固定节省比例。

## 工作方式

```text
主 Agent：判断任务适合交接 → 写完整任务包 → 启动一次 → 继续其他工作
后台程序：打开新标签页 → 发送一次 → 等待/有限重试 → 保存结果或失败
回收时：读取结果 → 按原验收标准核验 → 交付
```

后台等待不调用模型，也不需要主 Agent 反复检查。完成后保存 `event.json`。只有宿主本身有完成事件能力时，才可能自动唤醒原 Agent；当前通用 CLI 版本需要你下次交互时让 Agent 回收结果。

## 安装 Skill

首版执行器支持 macOS，使用 POSIX 文件锁与进程组；Windows 当前不支持，Linux 尚未验证。默认依赖 Python 3.9+ 和 [ego lite](https://www.egolite.app/)。先正常登录目标网站，确认官方命令可用，并阅读其当前操作指南：

```sh
ego-browser skill
```

克隆仓库，把完整的 `skills/web-task-relay` 目录复制到 Agent 的 Skill 目录，例如 Codex 的 `~/.codex/skills/web-task-relay`。保留脚本、参考文件与元数据。默认路径不需要安装 Playwright，不读取 Cookie 文件，也不安装常驻服务。

可选 `--browser existing` 连接普通 Chrome 144+。此模式需要先安装 `skills/web-task-relay/requirements.txt`，在 Chrome 的 `chrome://inspect/#remote-debugging` 开启连接并授权。使用 `session-start` 建立一次共享连接；作业完成、取消或 worker 重启都保留它，`session-stop` 才断开。浏览器退出或连接故障后可能再次需要授权，不能承诺永久有效。

调用示例：

```text
使用 $web-task-relay 节省我的 Agent 额度。
任务：根据下面给出的公开资料，比较方案 A 和 B。
约束：只使用这些资料，缺信息时明确说明。
输出：比较表和一段建议。
验收：每项结论都能对应到给定资料。
```

交接仍然需要打包和验收成本，短任务通常适合直接完成。只有用户要求省额度，且方案问题已经定义清楚时，这个 Skill 才应把任务发送到网页模型。

## 直接使用执行器

先在自己的工作目录创建 `.web-task-relay/packet.md`，写清目标、输入、约束、输出和验收。运行以下命令时，将脚本路径替换成你的真实安装路径：

```sh
python3 /path/to/web-task-relay/scripts/relay.py start .web-task-relay/jobs/comparison --prompt-file .web-task-relay/packet.md
```

命令立即返回作业目录和事件路径。默认最多三次尝试，总时限 30 分钟。`--timeout` 设置秒数；`--attempts` 设置总尝试数。主 Agent 无需轮询。

```sh
python3 /path/to/web-task-relay/scripts/relay.py collect .web-task-relay/jobs/comparison
python3 /path/to/web-task-relay/scripts/relay.py status .web-task-relay/jobs/comparison
python3 /path/to/web-task-relay/scripts/relay.py resume .web-task-relay/jobs/comparison
python3 /path/to/web-task-relay/scripts/relay.py cancel .web-task-relay/jobs/comparison
```

`resume` 保留原截止时间和尝试次数；发生发送歧义时先找回原会话。无法确认则停止，不自动再发。`cancel` 停止本地执行，保留远端会话。Ego 模式每个作业复用同一 TaskSpace 和 Page；用户接管时停止。成功回收验收后，Agent 按官方 Ego API 结束该空间，失败则保留取证。已有任务空间可用 `--ego-space 数字ID`，同一空间不要并行执行不同作业。机器重启后需要显式恢复，当前版本不安装常驻服务。

可以先试 [企业知识管理调研任务](examples/enterprise-knowledge-research.md)，或更小的 [方案比较示例](examples/plan-comparison.md)。企业知识调研已通过真实网页交接，9条一手来源经回收核验；见验证记录。

原始任务、输出、日志和状态都是运行数据，放在被忽略的 `.web-task-relay/`，不要上传到公共仓库。输出中的命令和指令不应直接执行，完成标记也不代表答案正确。

## 验证与贡献

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check --select E9,F skills tests
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python tests/browser_fixture.py
```

最后一条命令使用自己启动的无头 Chromium 协议浏览器与本地合成服务器，验证发送、worker 崩溃和 supervisor 崩溃恢复，不调用真实模型。测试关闭自己启动的浏览器，保留失败取证目录，不影响你的普通浏览器。测试中的计时和观察都是代码，不消耗模型调用。

欢迎提供可复现的问题或允许自动化的网站适配。一次实际任务、一段真实演示、一项有依据的改进，比未经验证的节省数字更有用。

[演示与介绍](docs/demo.md) · [需求与验收](docs/requirements.md) · [实现与恢复](docs/design.md) · [验证记录](docs/verification.md) · [MIT License](LICENSE)
