# TaskToPR 中文说明

> **把一个 GitHub Issue 转化为透明、可测试、可审查的 Pull Request。**

TaskToPR 是一个本地优先的软件工程 Agent。它在当前 Git 仓库中读取一个 Issue，有限地选择相关代码上下文，产出结构化计划，在独立分支上应用经过校验的补丁，执行真实测试与质量检查，进行独立复核，并且只在全部安全门通过后才会创建 Pull Request。

英文完整说明见 [README.md](README.md)。本中文文档重点说明实际使用方式与安全边界。

## 适用场景

TaskToPR 适合范围清晰、可测试的小型 Bug 修复、补充回归测试、局部文档或技术债务改动。它并不保证模型一定能正确理解业务，也不适合无人审阅地处理高风险配置、基础设施、身份认证或跨仓库重构。

| TaskToPR 会做什么 | TaskToPR 不会做什么 |
| --- | --- |
| 在当前本地 Git 仓库运行。 | 部署后台服务、队列、数据库或常驻守护进程。 |
| 支持 OpenAI、Anthropic 和 OpenAI-compatible 端点。 | 保存模型 API key，或把密钥写入日志与 PR。 |
| 创建独立分支，生成有限补丁，真实执行测试。 | 赋予模型任意 shell、网络或文件系统权限。 |
| 在测试和复核通过后可创建 PR。 | 强制推送、自动合并、直接推送 `main`/`master`。 |
| 保存完整本地证据包。 | 伪造命令输出、测试结果或 Demo 记录。 |

## 安装与检查

需要 Python 3.11+ 和 Git。读取真实 GitHub Issue 或创建真实 PR 时，还需要安装并登录 [GitHub CLI](https://cli.github.com/)。

```bash
python -m pip install tasktopr
# 或开发模式安装源码
python -m pip install -e .

tasktopr --help
tasktopr doctor
```

## 核心命令

先仅生成计划，不修改工作区：

```bash
export OPENAI_API_KEY="..."
tasktopr plan 123 --provider openai --model gpt-4.1-mini
```

完整 dry-run 也只生成计划和证据，不会创建分支、修改文件、运行测试、提交或创建 PR：

```bash
tasktopr fix 123 --dry-run
```

若希望 Agent 在新建的**本地分支**上完成修改、测试和复核，但不提交、不推送、不创建 PR：

```bash
tasktopr fix 123 --no-pr
```

如果您已经审查 `.tasktopr/runs/` 的证据，并希望在安全门通过后创建远程 PR：

```bash
tasktopr fix 123
```

`tasktopr review` 会检查当前工作树相对于期望范围的改动；`tasktopr status` 展示最近一次运行结果；`tasktopr config` 校验并显示已脱敏配置。

## 模型配置

| Provider | 必需环境变量 | 示例 |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `tasktopr plan 123 --provider openai --model gpt-4.1-mini` |
| Anthropic | `ANTHROPIC_API_KEY` | `tasktopr plan 123 --provider anthropic --model claude-sonnet-4-20250514` |
| OpenAI-compatible | `OPENAI_COMPATIBLE_API_KEY`、`OPENAI_COMPATIBLE_BASE_URL` | `tasktopr plan 123 --provider openai-compatible --model your-model` |
| 内置 Demo | 无网络密钥 | 在 Demo 仓库中执行 `tasktopr fix 1 --demo --no-pr` |

凭据只从当前进程的环境变量读取。任何 API key 都不会被写入 `.tasktopr`、Git、终端证据文件或 Pull Request 内容。

## 为什么可审查

每次运行均会在仓库下生成 `.tasktopr/runs/<时间>-<id>/`。其中包括结构化事件、计划、请求和应用的补丁元数据、真实测试结果以及 Markdown 总结。终端显示的每个阶段都来自同一份事件记录，而不是预先编排的文本。

| 产物 | 作用 |
| --- | --- |
| `events.jsonl` | 追加写入的阶段事件，已做常见令牌脱敏。 |
| `plan.json` | 校验后的 Issue、代码库轮廓与修改计划。 |
| `changes.json` | 补丁请求及实际变更文件。 |
| `test-results.json` | 实际命令、退出码、耗时及已脱敏输出。 |
| `summary.md` | 人类可读的修改范围、复核意见与结果。 |

## 默认安全规则

TaskToPR 仅接受仓库内的相对路径，并拒绝 `..` 越界与符号链接逃逸。模型上下文默认排除 `.git`、`.env*`、证书、密钥、依赖目录、构建目录和运行产物。对常见 GitHub、OpenAI、Anthropic、AWS 和私钥模式会进行日志脱敏。

默认策略还会阻止自动修改工作流、依赖锁文件、Docker/部署文件、认证/凭据/机密路径和 Git 内部文件。命令通过 `subprocess` 参数数组执行，始终禁用 shell，并拒绝 `rm`、`sudo`、`curl`、`wget`、强制操作等破坏性或网络管理命令。若范围出现额外文件、命中保护路径、测试失败或 `git diff --check` 发现问题，工具会阻止提交和 PR 创建。

进一步阅读：[安全模型](docs/security-model.md)、[安全披露](SECURITY.md)、[完整架构](ARCHITECTURE.md)。

## 可重复 Demo

仓库包含一个真实的“除以 0 崩溃”项目。Demo provider 只为该夹具返回确定性 JSON；后续的文件修改、pytest、Ruff、Mypy、差异检查和复核均为真实执行。

```bash
python -m pip install -e .
./demo/run_demo.sh
```

脚本会创建临时 Git 仓库，运行 `tasktopr fix 1 --demo --no-pr`，输出真实 `git diff`，再运行一次 `pytest`。它不会声称连接了真实大模型，也不会伪造终端输出。

## v0.1.0 范围

首版支持 Python 与 Node/TypeScript 的基础测试发现。它不是 GitHub App、定时机器人、远程执行器、自动合并工具或向量数据库。Go、Rust、Java 的语言适配将根据路线图逐步加入。请始终在合并前由人类审查所有 diff 和 Pull Request。

项目采用 [MIT License](LICENSE)，贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。
