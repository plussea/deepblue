from .base import ToolContext, ToolRegistry
from .edit import TOOL as EDIT
from .read import TOOL as READ
from .shell import TOOL as SHELL
from .write import TOOL as WRITE


def create_tools(context: ToolContext) -> ToolRegistry:
    return ToolRegistry(context, [READ, WRITE, EDIT, SHELL])
