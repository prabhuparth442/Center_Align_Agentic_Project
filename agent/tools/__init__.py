"""Tools register themselves with @tool when their module is imported.
Importing them all here means REGISTRY is full as soon as anything in agent.tools is used,
so describe_tools() never shows the model an empty menu."""
from . import browser, control, documents, finance_api  # noqa: F401
