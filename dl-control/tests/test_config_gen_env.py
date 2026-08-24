"""render_env_file 的 nursing-erp API 环境变量测试（2026-08-24 财务技能接入）。

NURSING_ERP_URL / NURSING_ERP_API_KEY 供 agent 侧 nursing-erp-query 技能
调用 ERP /api/（含 /api/billing/）。密钥是每台装机各自的 secret——只经
carry-forward 保留（同 Feishu 凭据模式），模板永不写死；URL 有栈内默认。
"""

from dl_control.agents.provisioning.config_gen import render_env_file


def _render(**kw):
    return render_env_file(openclaw_token="t", llm_api_key="k", agent_id="x", **kw)


def test_env_file_default_nursing_erp_url_no_key():
    """无 carry-forward 行：给栈内默认 URL，绝不凭空生成 API key"""
    env = _render()
    assert "NURSING_ERP_URL='http://dato-caddy:9081'\n" in env
    assert "NURSING_ERP_API_KEY" not in env


def test_env_file_carries_nursing_erp_lines_verbatim():
    """已有 .env 行原样携带——URL 与 key 都保留，不丢不重写"""
    lines = (
        "NURSING_ERP_URL='http://dato-caddy:9081'\n"
        "NURSING_ERP_API_KEY='per-install-secret'\n"
    )
    env = _render(nursing_erp_env_lines=lines)
    assert lines in env
    assert env.count("NURSING_ERP_URL=") == 1  # carry-forward 时不再叠默认行


def test_env_file_key_quoted_for_shell_source():
    """carry-forward 值按原样进入（service.py 侧原行搬运，含单引号包裹）"""
    env = _render(nursing_erp_env_lines="NURSING_ERP_API_KEY='abc'\n")
    assert "NURSING_ERP_API_KEY='abc'\n" in env
