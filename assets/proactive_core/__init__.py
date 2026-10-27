"""ai-proactive-deploy 核心包。

主动不是定时器，是"愿望 → 许可 → 时机 → 出口"四层漏斗。
"""
from .engine import Engine
from .models import Candidate, Desire, State

__all__ = ["Engine", "State", "Desire", "Candidate"]
__version__ = "1.0.0"
