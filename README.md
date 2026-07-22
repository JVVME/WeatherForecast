# WeatherAI

WeatherAI 是一个面向学习与研究的局地短时天气预测项目。当前仓库完成了
`PROJECT_SPEC.md` 定义的 M0，以及 **M1-A：ERA5 小样本请求、dry-run 和安全文件管理**。
尚未实现 NetCDF 内容解析、数据预处理、模型训练或推理 API。

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

项目通过 `.python-version` 和 `pyproject.toml` 固定 Python 3.12 系列。初始化开发环境：

```bash
uv sync --locked
```

## CDS 凭据

本项目使用官方 `cdsapi.Client`，不从项目 YAML、命令行参数或日志读取密钥。请先注册并
登录 Climate Data Store，然后按 [CDS API 官方设置说明](https://cds.climate.copernicus.eu/how-to-api)
把个人访问令牌保存在用户主目录的 `.cdsapirc`：

```yaml
url: https://cds.climate.copernicus.eu/api
key: <PERSONAL-ACCESS-TOKEN>
```

- Windows：`%USERPROFILE%\.cdsapirc`
- Linux/macOS：`~/.cdsapirc`

不要把真实令牌写入 `configs/`、`.env` 或仓库内的任何文件。本仓库已忽略误放在仓库
根目录的 `.cdsapirc`，但用户主目录仍是官方推荐位置。实际下载前还必须登录数据集页面，
阅读并接受相应使用条款；CDS 官方说明该步骤需要手动完成。

## ERA5 小样本下载

默认配置 `configs/data/era5_sample.yaml` 明确标记为 `scope: sample`，只请求：

- ERA5 hourly data on single levels；
- 2024 年 1 月的完整月份，逐小时；
- 上海附近 `1.0° × 1.25°` 小区域；
- 2 米温度、2 米露点温度、地面气压、10 米 U/V 风；
- 未压缩的 NetCDF 输出。

年份、月份、区域、变量和输出路径都来自 YAML。M1-A 配置模型限制为单月、经纬度跨度
各不超过 5°、最多 10 个变量，并明确拒绝 `total_precipitation`；多年和累计降水语义留待
后续里程碑。

先执行 dry-run：

```bash
uv run weather-ai data download \
  --config configs/data/era5_sample.yaml \
  --dry-run
```

dry-run 输出标准化请求、最终/临时路径以及 manifest 预览，不实例化 CDS 客户端、不访问
网络，也不创建目录或文件。

配置和凭据确认后，执行一个月的小样本下载：

```bash
uv run weather-ai data download \
  --config configs/data/era5_sample.yaml
```

下载先写入同目录的 `.nc.part` 临时文件。临时文件存在且非空并计算 SHA-256 后，才通过
原子重命名发布最终 `.nc`；若最终文件或同名临时文件已存在，命令默认拒绝覆盖。

## 原始数据与 manifest

`data/raw/` 遵循原始数据不可变原则：已发布的原始文件不会被覆盖或原地修改。文件名由
数据集、年月、区域标识和标准化请求哈希稳定生成。每次成功下载会原子更新配置指定的 JSON
manifest，记录请求、数据时间范围、文件大小、SHA-256、项目版本和 Git commit（可获取时）。
失败下载不会写入成功记录。

ERA5 的许可、引用和数据适用范围可能更新。使用者应在实际使用和发布成果前核查
[ERA5 数据集页面](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels)
当前的许可、引用要求和数据说明。本项目不会替使用者作许可合规判断。

## 其他 CLI

显示帮助：

```bash
uv run weather-ai --help
```

校验 M0 项目配置：

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

CI 中的下载测试全部使用 fake 客户端和临时目录，不访问外网，也不依赖真实 CDS 凭据。

## 当前目录

```text
configs/data/era5_sample.yaml  # 单月、小区域 ERA5 样本配置
configs/experiment/m0.yaml     # M0 项目级配置示例
src/weather_ai/data/           # 配置、请求、客户端端口、文件管理和 manifest
tests/unit/                    # 完全离线的单元测试
.github/workflows/ci.yml       # lint、类型检查和测试
PROJECT_SPEC.md                # 项目规范与里程碑协议
```

## 当前限制

- 只支持 ERA5 single levels 的单月小区域下载，不支持多年批量和并发下载。
- 不包含自动重试；CDS 失败会清理本次已知临时文件并返回非零退出码。
- 只验证下载文件存在且非空并记录哈希，不解析或验证 NetCDF 内部变量、坐标和单位。
- 不处理累计降水、派生湿度、数据切分、Dataset、模型、训练、评估或 API。
