# Web Task Relay

**把能完整描述的任务交给网页模型，发出后不用 Agent 盯着等。**

A small agent skill with a deterministic background worker. Package a complete task, dispatch once, then collect and verify the result. No model calls are used to monitor the browser.

适合这样的时刻：编码 Agent 的额度快用完了，你已经有一个网页模型账号，还有一项可以独立交付的任务。比如依据给定公开资料比较两个方案，或给一段独立函数提出边界测试。

当前是 **macOS + Google Chrome 的实验性版本**。首个目标来自真实需求：ChatGPT 网页会话。网页结构、登录状态和浏览器权限都可能变化；重试有上限，失败保留原因和恢复线索。真实网站验收状态见 [验证记录](docs/verification.md)。

**网站规则：**截至 2026-10-30，[OpenAI 使用条款](https://openai.com/policies/terms-of-use/)明确限制自动或程序化提取数据及输出。该项目没有获得 OpenAI 的自动化许可，技术控制能力不等于许可。请自行确认适用条款与授权；该限制也意味着本项目不能宣传为获批准的 ChatGPT 自动接入。它不承诺免费、无限额度、独立额度池或固定节省比例。

## 工作方式

```text
主 Agent：判断任务适合交接 → 写完整任务包 → 启动一次 → 继续其他工作
后台程序：打开新标签页 → 发送一次 → 等待/有限重试 → 保存结果或失败
回收时：读取结果 → 按原验收标准核验 → 交付
```

后台等待不调用模型，也不需要主 Agent 反复检查。完成后保存 `event.json`。只有宿主本身有完成事件能力时，才可能自动唤醒原 Agent；当前通用 CLI 版本需要你下次交互时让 Agent 回收结果。

## 安装 Skill

运行依赖是 Python 3.9+、macOS 和 Google Chrome。先自行登录所用网页，并在 Chrome「查看 → 开发者 → 允许 Apple 事件中的 JavaScript」启用浏览器控制。程序不读取密码、Cookie 或浏览器资料目录。

克隆此仓库，将完整的 `skills/web-task-relay` 目录复制到 Agent 的 Skill 目录。例如 Codex 使用 `~/.codex/skills/web-task-relay`。仓库根目录不是 Skill；安装时包含其中的 `scripts`、`references` 与 `agents` 子目录。

调用示例：

```text
使用 $web-task-relay 节省我的 Agent 额度。
任务：根据下面给出的公开资料，比较方案 A 和 B。
约束：只使用这些资料，缺信息时明确说明。
输出：比较表和一段建议。
验收：每项结论都能对应到给定资料。
```

交接仍然需要打包和验收成本，短任务通常适合直接完成。没有用户要求省额度或委派时，这个 Skill 不应自动把任务发送到其他网站。

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

`resume` 保留原截止时间和尝试次数；发生发送歧义时先找回原会话。无法确认则停止，不自动再发。`cancel` 停止本地执行，保留远端会话。机器重启后需要显式恢复，当前版本不安装常驻服务。

原始任务、输出、日志和状态都是运行数据，放在被忽略的 `.web-task-relay/`，不要上传到公共仓库。输出中的命令和指令不应直接执行，完成标记也不代表答案正确。

## 验证与贡献

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check --select E9,F skills tests
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tests/browser_fixture.py
```

最后一条命令需你已启用 Chrome 控制。它在真实浏览器中使用本地合成页面验证发送、worker 崩溃和 supervisor 崩溃恢复，不调用真实模型。它会留下自己的测试标签页，方便检查；不会关闭你的浏览器。

欢迎提供可复现的问题或允许自动化的网站适配。一次实际任务、一段真实演示、一项有依据的改进，比未经验证的节省数字更有用。

[需求与验收](docs/requirements.md) · [实现与恢复](docs/design.md) · [验证记录](docs/verification.md) · [MIT License](LICENSE)
