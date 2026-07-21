# WeatherAI

WeatherAI 是一个面向学习与研究的局地短时天气预测项目。当前仓库只完成
`PROJECT_SPEC.md` 定义的 **M0：仓库和规范初始化**，尚未实现数据下载、数据预处理、
模型训练或推理 API。

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

项目通过 `.python-version` 和 `pyproject.toml` 固定 Python 3.12 系列。uv 在本机缺少该
解释器时可自动安装兼容版本。

## 初始化

```bash
uv sync
```

`uv sync` 会创建项目虚拟环境、安装运行依赖与开发依赖，并依据 `uv.lock` 保持依赖可复现。

## CLI

显示帮助：

```bash
uv run weather-ai --help
```

校验配置并以 JSON 显示解析结果：

```bash
uv run weather-ai config --config configs/experiment/m0.yaml
```

结构化日志写入标准错误流，命令结果写入标准输出流，便于脚本分别处理。

## 质量检查

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

GitHub Actions 对每次推送和拉取请求执行相同的 lint、类型检查和测试。

## 当前目录

```text
configs/experiment/m0.yaml  # M0 项目级配置示例
src/weather_ai/             # CLI、配置读取和结构化日志
tests/unit/                 # M0 单元测试
.github/workflows/ci.yml    # 最小 CI
PROJECT_SPEC.md             # 项目规范与里程碑协议
```

数据目录、模型模块、训练器和服务层将在对应里程碑有真实用途时再创建，避免空壳模块。

## 当前限制

- CLI 仅支持配置校验与展示。
- 不包含 ERA5 下载请求或外部网络访问逻辑。
- 不包含数据处理、模型、训练、评估与 API。
