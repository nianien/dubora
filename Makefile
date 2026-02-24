# ============================================================================
# Video Remix - Makefile
# ============================================================================
#
# 开发者使用说明：
#
# 本 Makefile 采用"角色导向"命名，而非"技术堆叠导向"。
# 每个 install-* 目标对应一个明确的使用场景。
#
# 重要说明：
# - 没有 "install-all" 目标（这是设计决策，不是遗漏）
# - 可选功能通过明确的 install-* 目标启用
# - 不同环境（CI/本地/生产）可能需要不同的依赖组合
#
# ============================================================================

VENV := .venv
PIP  := $(VENV)/bin/pip
PY   := $(VENV)/bin/python

.PHONY: help clean install install-dev install-dub install-full test lint full

help:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Video Remix - 可用命令"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "📦 安装命令（按使用场景选择）："
	@echo "  make install      - 基础安装（最小可运行版本）"
	@echo "  make install-dev  - 开发环境（包含开发工具：pytest, black, ruff）"
	@echo "  make install-dub  - 配音功能（包含所有配音相关依赖）"
	@echo "  make install-full - 功能完整版（所有可选功能，适合本地开发）"
	@echo "  make full         - 别名，等同于 make install-full"
	@echo ""
	@echo "🧹 维护命令："
	@echo "  make clean        - 清理 Python 缓存文件（__pycache__, *.pyc 等）"
	@echo ""
	@echo "🧪 开发命令："
	@echo "  make test         - 运行测试套件"
	@echo "  make lint         - 代码检查（ruff）"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "💡 提示："
	@echo "  - 不同环境可能需要不同的依赖组合"
	@echo "  - CI 环境通常只需要: make install-dev"
	@echo "  - 本地开发推荐: make install-full"
	@echo "  - 生产环境根据实际需求选择: install 或 install-dub"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# 清理命令
# ============================================================================

clean:
	@echo "🧹 清理 Python 缓存文件..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@find . -name "*.pyo" -delete 2>/dev/null || true
	@find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ 清理完成"

# ============================================================================
# 安装命令（角色导向）
# ============================================================================

install:
	@echo "📦 安装基础依赖（最小可运行版本）..."
	@echo "   包含：核心功能、基础工具"
	@$(PIP) install -e .

install-dev:
	@echo "📦 安装开发环境依赖..."
	@echo "   包含：基础依赖 + 开发工具（pytest, black, ruff）"
	@$(PIP) install -e ".[dev]"

install-dub:
	@echo "📦 安装配音功能依赖..."
	@echo "   包含：基础依赖 + 配音相关（Demucs, Azure TTS, Google Speech, OpenAI）"
	@$(PIP) install -e ".[dub,openai,terms]"

install-full:
	@echo "📦 安装功能完整版（所有可选功能）..."
	@echo "   包含：基础依赖 + 配音 + 开发工具 + 所有可选功能"
	@echo "   注意：此版本包含所有可选依赖，适合本地开发环境"
	@echo "   某些依赖可能在某些环境（如 CI/无 GPU）下无法安装"
	@$(PIP) install -e ".[dub,openai,terms,faster,dev]"

# 别名：方便输入（make full 等同于 make install-full）
full: install-full

# ============================================================================
# 开发命令
# ============================================================================

test:
	@echo "🧪 运行测试套件..."
	@$(PY) -m pytest tests/ -v

lint:
	@echo "🔍 代码检查（ruff）..."
	@$(VENV)/bin/ruff check src/ tools/
	@echo "✅ 代码检查完成"

# ============================================================================
# 设计说明
# ============================================================================
#
# 为什么没有 "install-all"？
#
# 1. "all" 在不同环境含义不一致（CI/本地/生产/不同平台）
# 2. 可选依赖是"组合"的，不是"一锅端"的
# 3. 某些依赖可能在某些环境下无法安装（如 GPU 相关）
# 4. 明确的命名（install-*）比模糊的 "all" 更安全、更可维护
#
# 如果需要"功能完整"的安装，使用: make install-full
# 但请注意：full ≠ all，它表示"功能完整"，不保证所有环境都能运行
#
# ============================================================================
