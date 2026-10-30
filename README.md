# AI Retires Human

**让 AI 多干活，让人类早点退休。**

本仓库旨在更好地驱使 AI 帮人类干活，让人类早点走向只需要享乐的未来。

我们希望人把时间留给目标、判断、创造和生活，把越来越多的资料整理、方案分析、重复操作和等待交给 AI。这里收集能够实际使用的 Skills、工具与方法，用真实任务检验它们，再把有效的方法留下来。

名字表达的是方向：让 AI 替人承担劳动，让人拥有更多选择怎样生活的自由。每个实验仍需给出可核验的结果，说明成本、失败和适用条件。

## 我们想做好什么

- 让人少搬运上下文、少重复派工、少盯着进度。
- 让模型负责判断，让程序承担机械执行和等待。
- 用真实成果检验方法，把失败和恢复方式一起公开。
- 从能解决具体问题的小工具开始，逐步积累可复用的能力。

## 第一个实验：Web Task Relay

当用户明确选择用网页交接来节省 Agent token 时，将**问题已经定义完整的方案工作**交给网页模型。后台程序负责打开、发送、等待、有限重试和回收，主 Agent 负责出题与验收。

**普通调研不会自动触发。**用户主动问“有什么省 token 的方法”时，可以介绍这个选项；仅询问方法时不打开网页、不发送任务。用户已经选定网页交接后才执行。要求回答简短、额度偏低或过去提过省 token，都不构成新任务的交接指令。

适合方案比较、设计分析和决策建议；写代码、改仓库、调试、运行测试、部署等实施工作继续由主 Agent 完成。

当前实现默认使用 **ego lite 的官方 `ego-browser` CLI**，已在 macOS 完成真实网页任务与崩溃恢复验证。完整记录见[验证记录](https://github.com/jiafan-tiaocan/ai-retires-human/blob/codex/web-task-relay/docs/verification.md)。不承诺免费或独立额度池，也没有测出固定节省比例。

## 代码与使用

Skill 和执行器当前在 [PR #2](https://github.com/jiafan-tiaocan/ai-retires-human/pull/2) 的[候选分支](https://github.com/jiafan-tiaocan/ai-retires-human/tree/codex/web-task-relay)，尚未合并。首页 README 已单独初始化，查看代码请使用该分支。

```sh
git clone --branch codex/web-task-relay https://github.com/jiafan-tiaocan/ai-retires-human.git
```

将仓库中的完整 `skills/web-task-relay` 目录安装到 Agent 的 Skill 目录。Codex 也可以使用 `$skill-installer` 从上述仓库分支安装。默认执行器需要 Python 3.9+、ego lite，以及正常登录的目标网站。

```text
使用 $web-task-relay，我选择网页交接这种省 token 的方法。
任务：按我给出的资料和约束比较方案 A/B。
输出：比较表、推荐结论和风险。
验收：每项结论能对应资料，计算可复核。
```

- [Skill 入口](https://github.com/jiafan-tiaocan/ai-retires-human/tree/codex/web-task-relay/skills/web-task-relay)
- [安装、运行与边界](https://github.com/jiafan-tiaocan/ai-retires-human/blob/codex/web-task-relay/docs/web-task-relay.md)
- [需求与触发条件](https://github.com/jiafan-tiaocan/ai-retires-human/blob/codex/web-task-relay/docs/requirements.md)
- [执行器设计与恢复](https://github.com/jiafan-tiaocan/ai-retires-human/blob/codex/web-task-relay/docs/design.md)
- [企业知识管理任务包](https://github.com/jiafan-tiaocan/ai-retires-human/blob/codex/web-task-relay/examples/enterprise-knowledge-research.md)
- [演示与介绍](https://github.com/jiafan-tiaocan/ai-retires-human/blob/codex/web-task-relay/docs/demo.md)

## 参与

欢迎带一个具体问题、完整的输入与验收标准来试。请保留可复现的失败和改进依据，提交可公开的合成或授权材料。项目采用 [MIT License](https://github.com/jiafan-tiaocan/ai-retires-human/blob/codex/web-task-relay/LICENSE)。

网站规则、平台支持与当前限制见[使用边界](https://github.com/jiafan-tiaocan/ai-retires-human/blob/codex/web-task-relay/docs/web-task-relay.md)。技术验证不代表网站授予自动化许可；当前通用 CLI 保存结果后在下次交互回收，不会自动唤醒主 Agent。
