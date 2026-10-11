import agent.loop, agent.cli  # noqa: F401  (import check)
from agent.tools import browser, documents, finance_api, control  # noqa: F401
from agent.tools.base import REGISTRY, describe_tools
from agent.tools.documents import normalise
from agent.policy import origin_allowed
from agent.config import Config
from agent.checker import check
from agent.tools.base import ToolResult
from agent.llm import _extract_json


def test_registry_has_all_tools():
    expected = {"browser_open","browser_snapshot","browser_click","browser_fill","browser_select",
                "browser_download","browser_login","read_pdf","grounded_check","finance_get_invoice",
                "finance_list_invoices","finance_list_vendors","remember","recall","ask_human",
                "request_approval","finish"}
    assert set(REGISTRY) == expected
    assert "browser_open" in describe_tools()

def test_normalise_indian_grouping():
    assert normalise("1,18,000.00") == normalise("118000.00")

def test_origin_allowlist():
    c = Config()
    assert origin_allowed("http://127.0.0.1:5001/inbox", c)
    assert not origin_allowed("http://evil.example/x", c)

def test_checker_retry_default():
    assert check(ToolResult(True), "", 0, 2).kind == "ok"

def test_extract_json():
    assert _extract_json('x ```json\n{"a":1}\n``` y') == '{"a":1}'
